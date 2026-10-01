"""HTTP client for the Stremio addon protocol."""

from urllib.parse import quote, urlsplit, urlunsplit

from . import AddonRequestError, ManifestError
from .models import Manifest

DEFAULT_TIMEOUT = 15
USER_AGENT = "KodiStremioBridge/0.1"
MANIFEST = "manifest.json"


def normalize_transport_url(url):
    """Turn user input into a canonical ``https://…/manifest.json`` transport URL.

    Accepts ``stremio://`` links and base URLs without ``/manifest.json``. The
    path is otherwise kept verbatim, because configured addons store their
    settings (often including tokens) in it.
    """
    url = (url or "").strip()
    if not url:
        raise ManifestError("No URL given")
    if url.startswith("stremio://"):
        url = "https://" + url[len("stremio://"):]
    elif "://" not in url:
        url = "https://" + url

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ManifestError(f"Not a valid addon URL: {url}")

    path = parts.path
    if not path.endswith("/" + MANIFEST):
        path = path.rstrip("/") + "/" + MANIFEST
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def base_url(transport_url):
    """The addon root that resource paths are appended to."""
    parts = urlsplit(transport_url)
    path = parts.path
    if path.endswith("/" + MANIFEST):
        path = path[: -len(MANIFEST) - 1]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _encode(value):
    # Matches JavaScript's encodeURIComponent, which Stremio uses.
    return quote(str(value), safe="-_.!~*'()")


def build_resource_url(base, resource, type_, id_, extra=None):
    """``{base}/{resource}/{type}/{id}[/{extra}].json`` with Stremio's encoding."""
    url = f"{base}/{_encode(resource)}/{_encode(type_)}/{_encode(id_)}"
    if extra:
        items = extra.items() if isinstance(extra, dict) else extra
        pairs = [(k, v) for k, v in items if v is not None and v != ""]
        if pairs:
            url += "/" + "&".join(f"{_encode(k)}={_encode(v)}" for k, v in pairs)
    return url + ".json"


def resource_url(transport_url, resource, type_, id_, extra=None):
    """Like Stremio: the manifest URL's query string (e.g. a cache-buster such as
    ``?bcv=48``) is kept on every resource request."""
    url = build_resource_url(base_url(transport_url), resource, type_, id_, extra)
    query = urlsplit(transport_url).query
    return f"{url}?{query}" if query else url


class StremioClient:
    def __init__(self, timeout=DEFAULT_TIMEOUT, session=None, cache=None, ttls=None, log=None):
        """`ttls` maps resource names (e.g. "catalog") to cache lifetimes in seconds;
        resources without a positive TTL are never cached."""
        self.timeout = timeout
        self._session = session
        self.cache = cache
        self.ttls = ttls or {}
        self._log = log or (lambda msg: None)

    @property
    def session(self):
        # Imported lazily: Kodi starts a fresh interpreter per plugin call and
        # many routes never touch the network.
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
        return self._session

    def get_json(self, url):
        import requests

        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AddonRequestError(f"Request failed: {exc}") from exc
        if response.status_code != 200:
            raise AddonRequestError(f"HTTP {response.status_code} from {url}")
        try:
            return response.json()
        except ValueError as exc:
            raise AddonRequestError(f"Invalid JSON from {url}") from exc

    def fetch_manifest(self, url):
        """Fetch and parse a manifest. Returns ``(transport_url, Manifest)``."""
        transport_url = normalize_transport_url(url)
        try:
            data = self.get_json(transport_url)
        except AddonRequestError as exc:
            raise ManifestError(str(exc)) from exc
        return transport_url, Manifest.from_dict(data)

    def get_resource(self, transport_url, resource, type_, id_, extra=None, refresh=False):
        """`refresh`: fetch even if a fresh cached copy exists (and cache the new one)."""
        url = resource_url(transport_url, resource, type_, id_, extra)
        ttl = self.ttls.get(resource, 0)
        if self.cache is None or ttl <= 0:
            return self.get_json(url)

        cached = self.cache.get(url, allow_stale=True)
        if cached and cached[1] and not refresh:
            return cached[0]
        try:
            data = self.get_json(url)
        except AddonRequestError as exc:
            if cached:
                self._log(f"{exc}; using stale cache for {url}")
                return cached[0]
            raise
        self.cache.set(url, data, ttl)
        return data
