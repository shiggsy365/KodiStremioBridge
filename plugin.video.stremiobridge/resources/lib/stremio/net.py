"""A small stand-in for the parts of ``requests`` the add-on uses, on the
standard library.

Kodi starts a fresh interpreter for every plugin call, and ``import requests``
(urllib3, idna, charset detection...) took longer than the rest of a widget call
put together: about 0.1 s on a desktop and several times that on a Fire TV
Stick. ``urllib.request`` is already part of Python, costs a fraction of that
to import, and does all this add-on needs: GET/POST with headers, query
parameters, JSON bodies, timeouts and redirects.

What it leaves out: connection reuse between requests (each request opens its
own connection; within one plugin call most requests go to different addons
anyway), cookies and retries.
"""

import gzip
import json as _json
import os
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from urllib.parse import urlencode, urlsplit, urlunsplit


class RequestException(Exception):
    """Anything that stopped a request getting an answer (like requests')."""


class ConnectionError(RequestException):  # noqa: A001 - same name as requests'
    """The server couldn't be reached: DNS, refused, reset, TLS."""


class Timeout(RequestException):
    """No answer in time."""


class HTTPError(RequestException):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


def _has_default_certificates():
    """Whether Python has a certificate store to check servers against: Kodi
    points SSL_CERT_FILE at its own bundle, other systems have one of their own.
    Only file checks: importing certifi to find out costs ~40 ms a call."""
    paths = ssl.get_default_verify_paths()
    return any(path and os.path.exists(path) for path in (paths.cafile, paths.capath))


def _ssl_context():
    """Python's own certificates; certifi (script.module.certifi) only where
    there are none, as on some Android builds."""
    if not _has_default_certificates():
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:  # not installed, or its file is missing
            pass
    return ssl.create_default_context()


_CONTEXT = None


def _context():
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = _ssl_context()
    return _CONTEXT


class Response:
    """What a request answered. The body is read on first use, so a probe that
    only wants the status and headers (``stream=True``) never downloads it."""

    def __init__(self, url, status_code, headers, raw):
        self.url = url
        self.status_code = status_code
        self.headers = headers  # http.client.HTTPMessage: case-insensitive .get()
        self._raw = raw
        self._content = None

    @property
    def content(self):
        if self._content is None:
            try:
                data = self._raw.read() if self._raw is not None else b""
            finally:
                self.close()
            encoding = (self.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                data = gzip.decompress(data)
            elif encoding == "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:  # raw deflate, as some servers send it
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
            self._content = data
        return self._content

    @property
    def text(self):
        charset = self.headers.get_content_charset() if hasattr(self.headers, "get_content_charset") else None
        return self.content.decode(charset or "utf-8", errors="replace")

    def json(self):
        return _json.loads(self.content)  # ValueError on bad JSON, as with requests

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code} for {self.url}", response=self)

    def close(self):
        if self._raw is not None:
            try:
                self._raw.close()
            except Exception:
                pass
            self._raw = None


def _with_params(url, params):
    if not params:
        return url
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
    return urlunsplit(parts._replace(query=f"{parts.query}&{query}" if parts.query else query))


class Session:
    """``requests.Session``'s interface, for the calls the add-on makes."""

    def __init__(self):
        self.headers = {}

    def request(self, method, url, params=None, headers=None, json=None, data=None, timeout=None,
                stream=False, allow_redirects=True):
        url = _with_params(url, params)
        sent = {**self.headers, **(headers or {})}
        body = data
        if json is not None:
            body = _json.dumps(json).encode()
            sent.setdefault("Content-Type", "application/json")
        if isinstance(body, str):
            body = body.encode()
        if not stream:  # compressed bodies would change what a probe sees in Content-Length
            sent.setdefault("Accept-Encoding", "gzip, deflate")
        request = urllib.request.Request(url, data=body, headers=sent, method=method.upper())
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_context()),
                                             *([] if allow_redirects else [_NoRedirect()]))
        try:
            raw = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:  # 4xx/5xx still answered: hand back a response
            response = Response(exc.geturl() or url, exc.code, exc.headers, exc)
        except (socket.timeout, TimeoutError) as exc:
            raise Timeout(f"Timed out: {url}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise Timeout(f"Timed out: {url}") from exc
            raise ConnectionError(f"{exc.reason}") from exc
        except (OSError, ValueError, ssl.SSLError) as exc:  # reset, bad URL, TLS
            raise ConnectionError(f"{type(exc).__name__}: {exc}") from exc
        else:
            response = Response(raw.geturl(), raw.status, raw.headers, raw)
        if not stream:
            try:
                response.content  # read now, so a timeout mid-body raises here like requests
            except (socket.timeout, TimeoutError) as exc:
                raise Timeout(f"Timed out reading {url}") from exc
            except (OSError, EOFError, zlib.error) as exc:
                raise ConnectionError(f"{type(exc).__name__}: {exc}") from exc
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
