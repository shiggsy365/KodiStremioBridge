"""BingeTV: YouTube trailers without the YouTube add-on.

YouTube is asked for the video as its Apple Vision Pro app (VISIONOS, as NuvioTV does): its
streams come as plain URLs (nothing to decipher) and play to the end, where other clients' are
refused (403) about a minute in. Kodi plays the trailer's video and audio from an MPEG-DASH
manifest written here, served by the service (trailerserver.py): InputStream Adaptive only
fetches a manifest over HTTP. (YouTube's HLS for the same video plays without sound on a Fire TV
Stick: InputStream Adaptive's audio stalls.)
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

import xbmcvfs

from .common import log

WATCH = "https://www.youtube.com/watch?v={}"
PLAYER = "https://www.youtube.com/youtubei/v1/player?key={}&prettyPrint=false"
WEB_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
             " Chrome/130.0.0.0 Safari/537.36")
AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 (KHTML, like Gecko)"
         " Version/26.0 Safari/605.1.15")
CLIENT = {
    "clientName": "VISIONOS", "clientVersion": "1.02", "deviceMake": "Apple",
    "deviceModel": "RealityDevice17,1", "osName": "visionOS", "osVersion": "26.5.23O471",
    "hl": "en", "gl": "GB",
}
CLIENT_ID = "101"
CONFIG = "special://temp/stremiobridge/youtube.json"  # the API key and visitor id, kept a while
CONFIG_FOR = 6 * 3600
TIMEOUT = 10
VIDEO_ID = re.compile(r"(?:video_id=|youtu\.be/|[?&]v=|/embed/|/shorts/)([\w-]{11})")


class YouTubeError(Exception):
    pass


def video_id(url):
    """The YouTube video id in a trailer URL (YouTube's add-on's, or YouTube's own), else ""."""
    if re.fullmatch(r"[\w-]{11}", url or ""):
        return url
    match = VIDEO_ID.search(url or "")
    return match.group(1) if match else ""


def _get(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _config(refresh=False, clock=time.time):
    """``(api_key, visitor_id)``: from a watch page, kept for CONFIG_FOR."""
    path = xbmcvfs.translatePath(CONFIG)
    if not refresh:
        try:
            with open(path, encoding="utf-8") as f:
                kept = json.load(f)
            if clock() - kept["at"] < CONFIG_FOR:
                return kept["key"], kept["visitor"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    page = _get(WATCH.format("aqz-KE-bpKQ"), headers={"User-Agent": WEB_AGENT, "Accept-Language": "en-GB"})
    page = page.decode("utf-8", "replace")
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', page)
    visitor = re.search(r'"VISITOR_DATA":"([^"]+)"', page)
    if not key or not visitor:
        raise YouTubeError("no API key in YouTube's watch page")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"at": clock(), "key": key.group(1), "visitor": visitor.group(1)}, f)
    except OSError:
        pass
    return key.group(1), visitor.group(1)


def _player(vid, refresh=False):
    key, visitor = _config(refresh)
    body = json.dumps({"context": {"client": CLIENT}, "videoId": vid,
                       "contentCheckOk": True, "racyCheckOk": True}).encode()
    return json.loads(_get(PLAYER.format(key), data=body, headers={
        "Content-Type": "application/json", "User-Agent": AGENT, "Origin": "https://www.youtube.com",
        "X-YouTube-Client-Name": CLIENT_ID, "X-YouTube-Client-Version": CLIENT["clientVersion"],
        "X-Goog-Visitor-Id": visitor,
    }))


def _codec(fmt):
    match = re.search(r'codecs="([^"]+)"', fmt.get("mimeType", ""))
    return match.group(1) if match else ""


def _usable(fmt):
    return fmt.get("url") and fmt.get("initRange") and fmt.get("indexRange")


def pick(formats, max_height=1080):
    """``(video, audio)`` formats to play: H.264 (else VP9) up to max_height, and the default
    audio track's AAC (else Opus)."""
    formats = [f for f in formats if _usable(f)]
    videos = [f for f in formats if f.get("mimeType", "").startswith("video/") and f.get("height", 0) <= max_height]
    audios = [f for f in formats if f.get("mimeType", "").startswith("audio/")]
    default = [f for f in audios if (f.get("audioTrack") or {}).get("audioIsDefault", True)]
    audios = default or audios

    def best(items, *codecs):
        for codec in codecs:
            found = [f for f in items if _codec(f).startswith(codec)]
            if found:
                return max(found, key=lambda f: (f.get("height", 0), f.get("fps", 0), f.get("bitrate", 0)))
        return None

    video, audio = best(videos, "avc1", "vp09", "vp9"), best(audios, "mp4a", "opus")
    if not video or not audio:
        raise YouTubeError("no playable video and audio")
    return video, audio


def _representation(fmt, kind):
    attrs = f'id="{fmt["itag"]}" codecs="{_codec(fmt)}" bandwidth="{fmt.get("bitrate", 0)}"'
    if kind == "video":
        attrs += f' width="{fmt.get("width", 0)}" height="{fmt.get("height", 0)}" frameRate="{fmt.get("fps", 30)}"'
        channels = ""
    else:
        attrs += f' audioSamplingRate="{fmt.get("audioSampleRate", 44100)}"'
        channels = ('<AudioChannelConfiguration schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011"'
                    f' value="{fmt.get("audioChannels", 2)}"/>')
    index, init = fmt["indexRange"], fmt["initRange"]
    return (f'<AdaptationSet mimeType="{fmt["mimeType"].split(";")[0]}" contentType="{kind}">'
            f'<Representation {attrs}>{channels}<BaseURL>{escape(fmt["url"])}</BaseURL>'
            f'<SegmentBase indexRange="{index["start"]}-{index["end"]}">'
            f'<Initialization range="{init["start"]}-{init["end"]}"/></SegmentBase>'
            '</Representation></AdaptationSet>')


def manifest(video, audio):
    """An MPEG-DASH manifest for one video and one audio format."""
    seconds = int(video.get("approxDurationMs") or audio.get("approxDurationMs") or 0) / 1000
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"'
            f' type="static" mediaPresentationDuration="PT{seconds:.3f}S" minBufferTime="PT1.5S"><Period>'
            f'{_representation(video, "video")}{_representation(audio, "audio")}</Period></MPD>')


def resolve(vid, max_height=1080):
    """The MPEG-DASH manifest (text) for a YouTube video; YouTubeError when it can't be played."""
    started = time.monotonic()
    try:
        response = _player(vid)
        if (response.get("playabilityStatus") or {}).get("status") == "LOGIN_REQUIRED":
            response = _player(vid, refresh=True)  # the visitor id gone stale
    except (OSError, ValueError) as exc:
        raise YouTubeError(f"YouTube didn't answer: {exc}") from exc
    status = response.get("playabilityStatus") or {}
    if status.get("status") != "OK":
        raise YouTubeError(f"{status.get('status')}: {status.get('reason', '')}")
    video, audio = pick((response.get("streamingData") or {}).get("adaptiveFormats") or [], max_height)
    log(f"YouTube {vid}: {video.get('height')}p {_codec(video)} and {_codec(audio)}"
        f" in {time.monotonic() - started:.1f} s")
    return manifest(video, audio)


def stream_headers():
    """InputStream Adaptive's headers for the streams (as ListItem property)."""
    return "User-Agent=" + urllib.parse.quote(AGENT)



def publish_trailer(vid, max_height=1080):
    """The URL (the service's server) of a YouTube trailer's manifest, or "" (logged) when it
    can't be found or the service isn't running. Network and files only: fine in any thread."""
    from .trailerserver import publish

    try:
        url = publish(vid, resolve(vid, max_height))
    except (YouTubeError, OSError) as exc:
        log(f"YouTube trailer {vid}: {exc}")
        return ""
    if not url:
        log(f"YouTube trailer {vid}: the service's trailer server isn't running")
    return url


def playable(url):
    """The ListItem to play a published trailer manifest with."""
    import xbmcgui

    item = xbmcgui.ListItem(path=url, offscreen=True)
    item.setProperty("inputstream", "inputstream.adaptive")
    item.setProperty("inputstream.adaptive.stream_headers", stream_headers())
    item.setMimeType("application/dash+xml")
    item.setContentLookup(False)
    return item


def trailer_item(vid, max_height=1080):
    """``(url, ListItem)`` for a YouTube trailer, or None."""
    url = publish_trailer(vid, max_height)
    return (url, playable(url)) if url else None
