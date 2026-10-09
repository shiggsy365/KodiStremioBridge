"""What the stream picker shows for a stream: badges (resolution, release,
visual, audio, channels, streaming network), cached/debrid status, size,
bitrate, release group and languages, plus its text made readable.

Addons describe streams in free text, often decorated with symbols and
"small capital" letters that Kodi's fonts can't draw (AIOStreams writes
"ɴᴇᴛғʟɪx", "ᴹᵇᵖˢ", "▣ HEVC ✦ DV"). Everything is normalised to plain text and
read from the addon's label, description and filename. An addon can instead
send one ``key: value`` per line (see STRUCTURED_KEYS); those values are used
where present.
"""

import re
import unicodedata

from .record import dataclass
from .streams import TORRENT, format_size

# ------------------------------------------------------------------ text

_SMALL_CAPS = str.maketrans({
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ғ": "f", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j",
    "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ǫ": "q", "ʀ": "r", "ꜱ": "s", "ᴛ": "t",
    "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
})
# Symbols addons use as icons or decoration (but not "+", ":" or "-", which mean something).
_DECORATION = re.compile(r"[\u2190-\u2bff\u2e80-\u2fff\u3000-\u303f\U0001f000-\U0001faff»«•|]")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff\ufe0f]")
_SPACES = re.compile(r"[ \t]{2,}")


def normalise(text):
    """Plain, drawable text: small capitals and super/subscripts become
    ordinary letters, icons and decoration go, runs of spaces collapse."""
    text = unicodedata.normalize("NFKC", _INVISIBLE.sub("", str(text or "")).translate(_SMALL_CAPS))
    lines = []
    for line in text.splitlines():
        line = _SPACES.sub(" ", _DECORATION.sub(" ", line)).strip(" ·-")
        if line:
            lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------ badges

RESOLUTIONS = {2160: "4k", 1440: "1440p", 1080: "1080p", 720: "720p", 576: "sd", 480: "sd", 360: "sd"}
_RES_TEXT = [(2160, r"2160[pi]?|4k|uhd"), (1440, r"1440[pi]?|2k|qhd"), (1080, r"1080[pi]?|fhd"), (720, r"720[pi]?"),
             (576, r"576[pi]?"), (480, r"480[pi]?|sd"), (360, r"360p")]

# How each badge reads in the cards' text lines.
LABELS = {
    "remux": "Remux", "bluray": "BluRay", "webdl": "WEB-DL", "webrip": "WEBRip", "hdtv": "HDTV", "dvd": "DVD",
    "cam": "CAM", "dv": "DV", "hdr10plus": "HDR10+", "hdr10": "HDR10", "hdr": "HDR", "sdr": "SDR",
    "imax": "IMAX", "imax_enhanced": "IMAX Enhanced", "3d": "3D", "seadex": "SeaDex",
    "atmos": "Atmos", "truehd": "TrueHD", "dtsx": "DTS:X", "dtshdma": "DTS-HD MA", "dtshd": "DTS-HD",
    "dts": "DTS", "ddp": "DD+", "dd": "DD", "flac": "FLAC", "opus": "OPUS", "aac": "AAC", "mp3": "MP3",
    "pcm": "PCM", "4k": "4K", "1440p": "1440p", "1080p": "1080p", "720p": "720p", "sd": "SD",
}

_RELEASES = [  # first match wins
    ("remux", r"remux"),
    ("bluray", r"blu-?ray|bdrip|brrip|bdremux|bd\d+"),
    ("webrip", r"web-?rip"),
    ("webdl", r"web-?dl|webdl|web"),
    ("hdtv", r"hdtv|pdtv|hdrip"),
    ("dvd", r"dvd(?:rip|r|5|9)?"),
    ("cam", r"cam(?:rip)?|hdcam|hd-?ts|telesync|ts|telecine|hd-?tc|tc"),
]

_DV = r"dv|dovi|dolby[ .]?vision"
_HDR10P = r"hdr10\+|hdr10plus|hdr10p"
_HDR10 = r"hdr10"
_HDR = r"hdr"

_AUDIO = [  # (key, pattern)
    ("atmos", r"atmos"),
    ("truehd", r"true-?hd"),
    ("dtsx", r"dts[ :.-]?x"),
    ("dtshdma", r"dts[ .-]?hd[ .-]?ma"),
    ("dtshd", r"dts[ .-]?hd"),
    ("dts", r"dts"),
    ("ddp", r"ddp|dd\+|e-?ac-?3|dolby digital plus"),
    ("dd", r"dd|ac-?3|dolby digital"),
    ("flac", r"flac"),
    ("opus", r"opus"),
    ("aac", r"aac"),
    ("mp3", r"mp3"),
    ("pcm", r"l?pcm"),
]
_CHANNELS = re.compile(r"(?<!\d)([1-7])[ .]([01])(?!\d)")

NETWORKS = [  # (key, filename tags, names)
    ("netflix", ("NF",), ("netflix",)),
    ("amazon", ("AMZN",), ("amazon", "prime video")),
    ("appletv", ("ATVP", "ATV"), ("apple tv",)),
    ("disney", ("DSNP", "DNSP"), ("disney",)),
    ("max", ("HMAX", "MAX"), ("hbo max", "hbo")),
    ("hulu", ("HULU",), ("hulu",)),
    ("peacock", ("PCOK",), ("peacock",)),
    ("paramount", ("PMTP",), ("paramount",)),
    ("crunchyroll", ("CR",), ("crunchyroll",)),
]

DEBRID = {"RD": "Real-Debrid", "AD": "AllDebrid", "PM": "Premiumize", "TB": "TorBox", "DL": "Debrid-Link",
          "ED": "EasyDebrid", "OC": "Offcloud", "PK": "PikPak", "ND": "Usenet", "EN": "Easynews",
          "PKP": "PikPak", "DR": "Debrider"}
_DEBRID_CODE = re.compile(r"\[([A-Z]{2,3})(\+?)\]")
_CACHED = re.compile(r"⚡|\[[A-Z]{2,3}\+\]|\binstant\b|\bcached\b", re.I)
_BITRATE = re.compile(r"(\d+(?:\.\d+)?)\s*([MK])bps", re.I)
_SIZE_TEXT = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(TB|GB|MB)\b", re.I)
_GROUP = re.compile(r"-([A-Za-z0-9]{2,15})(?:\.[a-z0-9]{2,4})?$")

# One "key: value" per line, for addons that can format their output for us.
STRUCTURED_KEYS = ("res", "src", "vis", "aud", "ch", "size", "br", "svc", "cached", "type", "net", "grp", "lang", "prov")
_KEY_LINE = re.compile(r"^\s*([a-z]+)\s*:\s*(.+?)\s*$", re.I)


def _find(pattern, text):
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text, re.I)


@dataclass(frozen=True)
class StreamInfo:
    resolution: str = ""   # badge keys, "" when unknown
    release: str = ""
    visual: tuple = ()     # ("dv", "hdr10plus"), ("hdr",), ("sdr",)
    extras: tuple = ()     # "imax", "imax_enhanced", "3d", "seadex"
    audio: tuple = ()      # ("atmos",), ("ddp",)
    channels: str = ""     # "5.1"
    network: str = ""
    cached: bool = False
    p2p: bool = False
    debrid: str = ""       # "TorBox"
    provider: str = ""     # the addon's source line, e.g. "StremThru Torz"
    group: str = ""
    size: str = ""
    bitrate: str = ""
    languages: str = ""
    filename: str = ""
    details: str = ""      # the addon's own text, readable

    def badges(self):
        """Badge image names in display order, e.g. ``["dl_cached", "res_4k", "vis_dv_hdr"]``
        (resources/media/badges/<name>.png)."""
        badges = [("dl", "cached" if self.cached else "p2p" if self.p2p else ""), ("res", self.resolution),
                  ("rel", self.release)]
        badges += [("vis", key) for key in self.visual] + [("extra", key) for key in self.extras]
        badges += [("aud", key) for key in self.audio]
        badges += [("ch", self.channels.replace(".", "")), ("net", self.network)]
        return [f"{slot}_{key}" for slot, key in badges if key]

    def summary(self):
        """Size, bitrate, where it comes from and languages, as one line."""
        parts = [self.size, self.bitrate, self.debrid, self.provider, self.group, self.languages.upper()]
        return " · ".join(p for p in parts if p)


def structured_fields(text):
    """``{key: value}`` from ``key: value`` lines, if the text is in that format
    (at least three known keys), else {}."""
    fields = {}
    for line in str(text or "").splitlines():
        match = _KEY_LINE.match(line)
        if match and match.group(1).lower() in STRUCTURED_KEYS:
            fields[match.group(1).lower()] = match.group(2)
    return fields if len(fields) >= 3 else {}


def _resolution(text, fallback=None):
    for res, pattern in _RES_TEXT:
        if _find(pattern, text):
            return RESOLUTIONS[res]
    return RESOLUTIONS.get(fallback, "")


def _release(text):
    return next((key for key, pattern in _RELEASES if _find(pattern, text)), "")


def _visual(text):
    hdr = "hdr10plus" if _find(_HDR10P, text) else "hdr10" if _find(_HDR10, text) else "hdr" if _find(_HDR, text) else ""
    found = (("dv",) if _find(_DV, text) else ()) + ((hdr,) if hdr else ())
    return found or (("sdr",) if _find("sdr", text) else ())


def _extras(text):
    found = []
    if _find(r"imax[ .-]?enhanced", text):
        found.append("imax_enhanced")
    elif _find("imax", text):
        found.append("imax")
    if _find(r"3d|sbs|half-?ou", text):
        found.append("3d")
    if _find(r"seadex|best[ .-]?release", text):
        found.append("seadex")
    return tuple(found)


def _audio(text):
    """The main audio badge: Atmos when present (its codec is implied), else the best codec."""
    found = [key for key, pattern in _AUDIO if _find(pattern, text)]
    for key in ("atmos", "dtsx", "dtshdma", "dtshd", "dts", "truehd", "ddp", "dd", "flac", "opus", "aac", "mp3", "pcm"):
        if key in found:
            return (key,)
    return ()


def audio_codecs(text):
    """Every audio format mentioned, for the card's text line ("Atmos · DD+")."""
    return [LABELS[key] for key, pattern in _AUDIO if _find(pattern, text)]


def _channels(text):
    match = _CHANNELS.search(text)
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def _network(text, filename):
    tokens = set(re.split(r"[\s._\-\[\]()]+", filename or ""))
    for key, tags, names in NETWORKS:
        if any(tag in tokens for tag in tags) or any(_find(re.escape(name), text) for name in names):
            return key
    return ""


def _debrid(text):
    match = _DEBRID_CODE.search(text)
    if match:
        return DEBRID.get(match.group(1), match.group(1))
    for name in DEBRID.values():
        if _find(re.escape(name), text):
            return name
    return ""


def _group(filename, binge_group):
    match = _GROUP.search(filename or "")
    if match and not match.group(1).isdigit():
        return match.group(1)
    tail = (binge_group or "").split("|")[-1]
    return tail if tail and not re.search(r"\d{3,4}p|web|bluray|remux|\.", tail, re.I) else ""


def _size_text(description):
    match = _SIZE_TEXT.search(description)
    return f"{match.group(1)} {match.group(2).upper()}" if match else ""


def _provider(description):
    """The addon's "where from" line, e.g. "[TB] StremThru Torz · BYNDR" -> "StremThru Torz"."""
    for line in description.splitlines():
        if _DEBRID_CODE.search(line):
            rest = _DEBRID_CODE.sub("", line).strip(" ·")
            return rest.split(" · ")[0].strip()
    return ""


def _languages(description):
    for line in description.splitlines():
        if re.search(r"\bsub\b|\(\w{2}\)", line, re.I) or re.fullmatch(r"[a-z]{2}(?: · [a-z]{2})*", line, re.I):
            return line.split(" ")[0] if " · " not in line else " · ".join(
                p for p in line.split(" · ") if re.fullmatch(r"[a-z]{2,3}", p, re.I))
    return ""


def stream_info(stream):
    """StreamInfo for a stremio.streams.Stream."""
    name = normalise(stream.name)
    description = normalise(stream.title)
    filename = stream.filename or ""
    fields = structured_fields(stream.title)
    text = "\n".join((name, description, filename.replace(".", " ")))
    q = stream.quality

    def pick(key, parse, *fallback):
        value = fields.get(key)
        return parse(value) if value else parse(*fallback)

    bitrate = pick("br", lambda t: (lambda m: f"{m.group(1)} {m.group(2).upper()}bps" if m else "")(_BITRATE.search(t)),
                   description)
    raw = stream.name + "\n" + stream.title
    return StreamInfo(
        resolution=pick("res", lambda t: _resolution(t), name) or _resolution(text, q.resolution),
        release=pick("src", _release, name + "\n" + filename.replace(".", " ") + "\n" + description),
        visual=pick("vis", _visual, text),
        extras=_extras(fields.get("vis", "") or text),
        audio=pick("aud", _audio, text),
        channels=pick("ch", _channels, text),
        network=_network(fields.get("net", ""), "") if fields.get("net") else _network(text, filename),
        cached=(fields["cached"].lower() in ("yes", "true", "1")) if "cached" in fields
        else bool(_CACHED.search(raw)) or "+" in fields.get("svc", ""),
        p2p=stream.kind == TORRENT or fields.get("type", "").lower() == "p2p",
        debrid=_debrid(f"[{fields['svc']}]" if fields.get("svc") else raw),
        provider=fields.get("prov") or _provider(description),
        group=fields.get("grp") or _group(filename, stream.binge_group),
        # The size as the addon writes it (it may count GB in 1000s), else from the byte count.
        size=fields.get("size") or _size_text(description) or format_size(q.size),
        bitrate=bitrate,
        languages=fields.get("lang") or _languages(description),
        filename=filename,
        details=description if not fields else "",
    )
