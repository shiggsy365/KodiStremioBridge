"""Streams: parsing, quality detection, filtering, sorting and play paths."""

import re
import time
from urllib.parse import quote, urlsplit

from .aggregate import gather
from .models import Subtitle
from .record import dataclass, field

YOUTUBE_PLAY = "plugin://plugin.video.youtube/play/?video_id={}"
ELEMENTUM_PLAY = "plugin://plugin.video.elementum/play?uri={}"

# Stream kinds
URL, YOUTUBE, TORRENT, EXTERNAL = "url", "youtube", "torrent", "external"

# Sort modes
SORT_QUALITY, SORT_SIZE, SORT_ADDON = 0, 1, 2


# ---------------------------------------------------------------- quality

_RESOLUTIONS = [
    (2160, re.compile(r"\b(2160p|4k|uhd)\b", re.I)),
    (1440, re.compile(r"\b1440p\b", re.I)),
    (1080, re.compile(r"\b(1080p|fhd)\b", re.I)),
    (720, re.compile(r"\b720p\b", re.I)),
    (576, re.compile(r"\b576p\b", re.I)),
    (480, re.compile(r"\b(480p|sd|dvd(rip)?)\b", re.I)),
    (360, re.compile(r"\b360p\b", re.I)),
]
_HDR = [
    ("DV", re.compile(r"\b(dv|dovi|dolby[ ._-]?vision)\b", re.I)),
    ("HDR10+", re.compile(r"\bhdr10(\+|plus)", re.I)),
    ("HDR", re.compile(r"\bhdr(10)?\b", re.I)),
]
_CODECS = [
    ("AV1", re.compile(r"\bav1\b", re.I)),
    ("HEVC", re.compile(r"\b(x265|h[ .]?265|hevc)\b", re.I)),
    ("VP9", re.compile(r"\bvp9\b", re.I)),
    ("H.264", re.compile(r"\b(x264|h[ .]?264|avc)\b", re.I)),
]
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(TB|GB|GiB|MB|MiB)\b", re.I)
_SIZE_UNITS = {"tb": 1024 ** 4, "gb": 1024 ** 3, "gib": 1024 ** 3, "mb": 1024 ** 2, "mib": 1024 ** 2}
_REMUX = re.compile(r"\bremux\b", re.I)
_CAM = re.compile(r"\b(cam|camrip|hdcam|hd-?ts|telesync|ts|telecine|hd-?tc)\b", re.I)
# Debrid addons mark instantly available ("cached") results like "[RD+]" or with ⚡.
_CACHED = re.compile(r"\[(RD|AD|PM|DL|TB|ED|OC|PK|EN|DR)\+\]|⚡|\binstant\b", re.I)


@dataclass(frozen=True)
class Quality:
    resolution: int = None
    hdr: tuple = ()
    codec: str = ""
    size: int = None
    remux: bool = False
    cam: bool = False
    cached: bool = False

    @classmethod
    def parse(cls, text, size=None, label=""):
        """`label` is the addon's own short label (the stream name); its
        resolution wins over anything mentioned in the longer description."""
        resolution = _resolution(label) or _resolution(text)
        hdr = [name for name, rx in _HDR if rx.search(text)]
        if "HDR10+" in hdr and "HDR" in hdr:
            hdr.remove("HDR")
        if size is None:
            match = _SIZE.search(text)
            if match:
                size = int(float(match.group(1).replace(",", ".")) * _SIZE_UNITS[match.group(2).lower()])
        return cls(
            resolution=resolution,
            hdr=tuple(hdr),
            codec=next((name for name, rx in _CODECS if rx.search(text)), ""),
            size=size,
            remux=bool(_REMUX.search(text)),
            cam=bool(_CAM.search(text)),
            cached=bool(_CACHED.search(text)),
        )


def _resolution(text):
    return next((res for res, rx in _RESOLUTIONS if rx.search(text)), None)


_INVISIBLE = re.compile("[\u200b-\u200f\u2060\ufeff]")
_SPACES = re.compile(r"[ \t]{2,}")


def clean_text(text):
    """Drop zero-width characters (some addons pad labels with them; Kodi skins
    may draw them as boxes) and squeeze runs of spaces."""
    lines = (_SPACES.sub(" ", _INVISIBLE.sub("", line)).strip() for line in str(text or "").splitlines())
    return "\n".join(line for line in lines if line)


def format_size(size):
    if not size:
        return ""
    for unit, factor in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{size} B"


# ---------------------------------------------------------------- model

@dataclass(frozen=True)
class Stream:
    addon: str
    addon_index: int
    name: str = ""
    title: str = ""
    url: str = ""
    yt_id: str = ""
    info_hash: str = ""
    file_idx: int = None
    external_url: str = ""
    headers: tuple = ()  # (name, value) pairs for the request
    filename: str = ""
    video_size: int = None
    video_hash: str = ""
    binge_group: str = ""
    sources: tuple = ()
    subtitles: tuple = ()
    quality: Quality = field(default_factory=Quality)

    @classmethod
    def from_dict(cls, data, addon, addon_index=0):
        """None if the entry has nothing we could ever play or open."""
        if not isinstance(data, dict):
            return None
        if not any(isinstance(data.get(k), str) and data[k] for k in ("url", "ytId", "infoHash", "externalUrl")):
            return None
        hints = data.get("behaviorHints") if isinstance(data.get("behaviorHints"), dict) else {}
        proxy = hints.get("proxyHeaders") if isinstance(hints.get("proxyHeaders"), dict) else {}
        request_headers = proxy.get("request") if isinstance(proxy.get("request"), dict) else {}
        try:
            video_size = int(hints["videoSize"]) if hints.get("videoSize") else None
        except (TypeError, ValueError):
            video_size = None
        try:
            file_idx = int(data["fileIdx"]) if data.get("fileIdx") is not None else None
        except (TypeError, ValueError):
            file_idx = None

        name = clean_text(data.get("name"))
        title = clean_text(data.get("title") or data.get("description"))
        filename = str(hints.get("filename") or "")
        return cls(
            addon=addon,
            addon_index=addon_index,
            name=name,
            title=title,
            url=data.get("url") or "",
            yt_id=data.get("ytId") or "",
            info_hash=(data.get("infoHash") or "").lower(),
            file_idx=file_idx,
            external_url=data.get("externalUrl") or "",
            headers=tuple((str(k), str(v)) for k, v in request_headers.items()),
            filename=filename,
            video_size=video_size,
            video_hash=str(hints.get("videoHash") or ""),
            binge_group=str(hints.get("bingeGroup") or ""),
            sources=tuple(s for s in data.get("sources") or [] if isinstance(s, str)),
            subtitles=tuple(
                s for s in (Subtitle.from_dict(x, addon) for x in data.get("subtitles") or []) if s
            ),
            quality=Quality.parse("\n".join((name, title, filename)), video_size, label=name),
        )

    @property
    def kind(self):
        if self.url:
            return URL
        if self.yt_id:
            return YOUTUBE
        if self.info_hash:
            return TORRENT
        return EXTERNAL

    @property
    def text(self):
        return "\n".join(t for t in (self.name, self.title, self.filename) if t)

    @property
    def dedupe_key(self):
        if self.url:
            return ("url", self.url)
        if self.info_hash:
            return ("torrent", self.info_hash, self.file_idx)
        return (self.kind, self.yt_id or self.external_url)

    @property
    def is_adaptive(self):
        path = urlsplit(self.url).path.lower()
        return path.endswith((".m3u8", ".mpd"))

    @property
    def adaptive_mimetype(self):
        path = urlsplit(self.url).path.lower()
        if path.endswith(".m3u8"):
            return "application/vnd.apple.mpegurl"
        if path.endswith(".mpd"):
            return "application/dash+xml"
        return ""

    def subtitle_extra(self):
        """Hints for the subtitles resource, as Stremio sends them."""
        extra = {}
        if self.video_hash:
            extra["videoHash"] = self.video_hash
        if self.video_size:
            extra["videoSize"] = self.video_size
        if self.filename:
            extra["filename"] = self.filename
        return extra


def header_string(headers):
    """Kodi's ``url|Name=value&Name2=value2`` header syntax."""
    return "&".join(f"{name}={quote(value, safe='')}" for name, value in headers)


def magnet(stream):
    uri = f"magnet:?xt=urn:btih:{stream.info_hash}"
    if stream.filename:
        uri += f"&dn={quote(stream.filename, safe='')}"
    for source in stream.sources:
        if source.startswith("tracker:"):
            uri += f"&tr={quote(source[len('tracker:'):], safe='')}"
    return uri


def play_path(stream, torrents_via_elementum=False, headers_in_url=True):
    """The path Kodi should play, or None if Kodi can't play this stream.

    Request headers go into the URL (``url|Name=value``) unless `headers_in_url`
    is False, e.g. when inputstream.adaptive gets them as properties instead.
    """
    if stream.kind == URL:
        if stream.headers and headers_in_url:
            return f"{stream.url}|{header_string(stream.headers)}"
        return stream.url
    if stream.kind == YOUTUBE:
        return YOUTUBE_PLAY.format(stream.yt_id)
    if stream.kind == TORRENT and torrents_via_elementum:
        return ELEMENTUM_PLAY.format(quote(magnet(stream), safe=""))
    return None


# ---------------------------------------------------------------- probing

PROBE_TIMEOUT = 10
# Proxies/debrid often answer the very first request for a fresh link with 400
# and serve it fine a moment later (seen with AIOStreams), so these are retried.
RETRYABLE_STATUS = (400, 408, 425, 429, 500, 502, 503, 504)
PROBE_RETRIES = 2
PROBE_RETRY_DELAY = 2.0
# A "stream" this small when the addon says it's gigabytes is an error/placeholder video.
PLACEHOLDER_BYTES = 50 * 1024 ** 2
_RANGE_TOTAL = re.compile(r"/(\d+)\s*$")


def probe(session, stream, timeout=PROBE_TIMEOUT, retries=PROBE_RETRIES, retry_delay=PROBE_RETRY_DELAY,
          sleep=time.sleep):
    """Check that a stream's link actually serves video, without downloading it.
    Returns ``(ok, reason)``. Only direct links are checked; YouTube and
    torrents are passed through. "Try again" answers are retried."""
    if stream.kind != URL:
        return True, ""
    for attempt in range(retries + 1):
        ok, reason, retryable = _probe_once(session, stream, timeout)
        if ok or not retryable or attempt == retries:
            return ok, reason
        sleep(retry_delay)
    return False, reason


def _probe_once(session, stream, timeout):
    """``(ok, reason, worth_retrying)``."""
    import requests

    headers = dict(stream.headers)
    if not stream.is_adaptive:
        headers["Range"] = "bytes=0-1"
    try:
        response = session.get(stream.url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
    except requests.ConnectionError as exc:
        return False, type(exc).__name__, True
    except requests.RequestException as exc:  # timeouts etc.: already waited long enough
        return False, type(exc).__name__, False
    try:
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}", response.status_code in RETRYABLE_STATUS
        if "text/html" in response.headers.get("Content-Type", "").lower():
            return False, "an HTML page, not video", False
        total = None
        match = _RANGE_TOTAL.search(response.headers.get("Content-Range", ""))
        if match:
            total = int(match.group(1))
        elif response.status_code == 200 and response.headers.get("Content-Length", "").isdigit():
            total = int(response.headers["Content-Length"])
        expected = stream.quality.size
        if not stream.is_adaptive and total is not None and total < PLACEHOLDER_BYTES \
                and expected and expected > PLACEHOLDER_BYTES * 4:
            return False, f"only {format_size(total) or str(total) + ' B'} (placeholder?)", False
        return True, "", False
    finally:
        response.close()


def fallback_order(candidates, preferred=None):
    """`preferred` first (e.g. the same source as last time), then the rest in order."""
    if preferred is None:
        return list(candidates)
    return [preferred] + [s for s in candidates if s is not preferred]


# ---------------------------------------------------------------- fetching

def fetch_streams(client, addons, type_, id_, on_progress=None):
    """Ask every addon at once. Returns ``(streams, errors, cancelled)``;
    streams are in addon order, then the order each addon returned them."""

    def task(addon):
        return lambda: client.get_resource(addon.transport_url, "stream", type_, id_)

    results, errors, cancelled = gather([(a.name, task(a)) for a in addons], on_progress)
    streams = []
    for i, data in results:
        entries = data.get("streams") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            stream = Stream.from_dict(entry, addons[i].name, i)
            if stream is not None:
                streams.append(stream)
    return streams, errors, cancelled


# ---------------------------------------------------------------- choosing

@dataclass(frozen=True)
class StreamPrefs:
    sort: int = SORT_QUALITY
    max_resolution: int = None
    hide_cam: bool = True
    exclude: tuple = ()  # lower-case keywords
    allow_torrents: bool = False


def parse_keywords(text):
    return tuple(k.strip().lower() for k in (text or "").split(",") if k.strip())


def is_allowed(stream, prefs):
    if stream.kind == EXTERNAL:
        return False
    if stream.kind == TORRENT and not prefs.allow_torrents:
        return False
    q = stream.quality
    if prefs.hide_cam and q.cam:
        return False
    if prefs.max_resolution and q.resolution and q.resolution > prefs.max_resolution:
        return False
    text = stream.text.lower()
    return not any(keyword in text for keyword in prefs.exclude)


def _sort_key(prefs):
    def key(indexed):
        position, s = indexed
        q = s.quality
        by_addon = (s.addon_index, position)
        if prefs.sort == SORT_ADDON:
            return by_addon
        if prefs.sort == SORT_SIZE:
            return (-(q.size or 0), -(q.resolution or 0)) + by_addon
        return (-(q.resolution or 0), not q.cached, -(q.size or 0)) + by_addon

    return key


def prepare_streams(streams, prefs):
    """Drop duplicates and filtered streams, then sort by preference."""
    seen, unique = set(), []
    for stream in streams:
        if stream.dedupe_key not in seen:
            seen.add(stream.dedupe_key)
            unique.append(stream)
    allowed = [(i, s) for i, s in enumerate(unique) if is_allowed(s, prefs)]
    return [s for _, s in sorted(allowed, key=_sort_key(prefs))]


def describe(stream):
    """``(headline, details)`` for a picker: technical tags, then the addon's own text."""
    q = stream.quality
    tags = []
    if q.resolution:
        tags.append("4K" if q.resolution == 2160 else f"{q.resolution}p")
    tags += list(q.hdr)
    if q.remux:
        tags.append("REMUX")
    if q.codec:
        tags.append(q.codec)
    if q.size:
        tags.append(format_size(q.size))
    if stream.kind == TORRENT:
        tags.append("P2P")
    details = " · ".join(line.strip() for line in stream.text.splitlines() if line.strip())
    return " · ".join(tags), details
