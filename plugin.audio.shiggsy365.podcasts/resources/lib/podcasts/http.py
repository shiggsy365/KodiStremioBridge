"""Small urllib wrapper: the add-on has no dependencies beyond the standard library."""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from . import PodcastError

USER_AGENT = "Kodi Podcasts/1.0 (+https://github.com/shiggsy365/KodiStremioBridge)"
MAX_BYTES = 12 * 1024 * 1024  # big enough for feeds with thousands of episodes


def request(url, method="GET", body=None, headers=None, auth=None, timeout=15, opener=None):
    """The response body as bytes. `body` is sent as JSON; `auth` is (user, password)."""
    if not url.startswith(("https://", "http://")):
        raise PodcastError(f"Unsupported address: {url}")
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with (opener.open if opener else urllib.request.urlopen)(req, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise PodcastError(f"{url.split('?')[0]} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PodcastError(f"Couldn't reach {urllib.parse.urlsplit(url).netloc}: {reason}") from exc
    if len(raw) > MAX_BYTES:
        raise PodcastError("The response is too large")
    return raw


def get_json(url, **kwargs):
    raw = request(url, headers={"Accept": "application/json"}, **kwargs)
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except ValueError as exc:
        raise PodcastError(f"{url.split('?')[0]} didn't return JSON") from exc
