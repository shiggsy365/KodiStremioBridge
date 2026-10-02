"""Dispatcharr's API, as far as switching a channel's source needs it. No
Kodi imports (standard library only), so it can be tested.

A Dispatcharr channel has several source streams (from your M3U accounts)
that its proxy fails over between. IPTV Simple plays the channel's proxy URL
(/proxy/ts/stream/<channel uuid>); switching the source on the server
(change_stream) keeps that URL, so Kodi carries on playing the same channel
with its guide and channel info.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT = 15


class DispatcharrError(Exception):
    pass


class NotRunning(DispatcharrError):
    """The channel isn't being streamed by Dispatcharr right now."""


@dataclass(frozen=True)
class Channel:
    id: int
    uuid: str
    number: float
    name: str


@dataclass(frozen=True)
class Source:
    id: int
    name: str
    account: str = ""
    details: str = ""   # resolution · codec · bitrate, when Dispatcharr has stats


class Dispatcharr:
    def __init__(self, base_url, api_key, opener=None, timeout=TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _request(self, path, body=None):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {"Accept": "application/json", "X-API-Key": self.api_key,
                   "Authorization": f"ApiKey {self.api_key}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotRunning(f"{path}: not found") from exc
            if exc.code in (401, 403):
                raise DispatcharrError("Dispatcharr refused the API key (an admin user's key is needed)") from exc
            raise DispatcharrError(f"Dispatcharr HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DispatcharrError(f"Can't reach Dispatcharr: {exc}") from exc
        try:
            return json.loads(raw) if raw else {}
        except ValueError as exc:
            raise DispatcharrError("Dispatcharr sent something that isn't JSON") from exc

    def channels(self):
        """Every channel (all pages)."""
        found, page = [], self._request("/api/channels/channels/")
        while True:
            items = page.get("results") if isinstance(page, dict) else page
            for item in items or []:
                channel = _channel(item)
                if channel:
                    found.append(channel)
            following = page.get("next") if isinstance(page, dict) else None
            if not following:
                return found
            page = self._request(following)

    def accounts(self):
        """``{M3U account id: name}``."""
        data = self._request("/api/m3u/accounts/")
        items = data.get("results") if isinstance(data, dict) else data
        return {a["id"]: a.get("name") or "" for a in items or [] if isinstance(a, dict) and "id" in a}

    def sources(self, channel, accounts=None):
        """The channel's source streams, in its failover order."""
        data = self._request(f"/api/channels/channels/{channel.id}/streams/")
        items = data.get("results") if isinstance(data, dict) else data
        return [s for s in (_source(item, accounts or {}) for item in items or []) if s]

    def current_source(self, channel):
        """The source the running channel is using, or None if it isn't running."""
        try:
            status = self._request(f"/proxy/ts/status/{channel.uuid}")
        except NotRunning:
            return None
        stream_id = status.get("stream_id") if isinstance(status, dict) else None
        try:
            return int(stream_id) if stream_id is not None else None
        except (TypeError, ValueError):
            return None

    def switch(self, channel, source_id):
        """Switch the running channel to another source."""
        return self._request(f"/proxy/ts/change_stream/{channel.uuid}", {"stream_id": source_id})

    def next_source(self, channel):
        return self._request(f"/proxy/ts/next_stream/{channel.uuid}", {})


def _channel(item):
    if not isinstance(item, dict) or "id" not in item or not item.get("uuid"):
        return None
    try:
        number = float(item.get("channel_number"))
    except (TypeError, ValueError):
        number = None
    return Channel(int(item["id"]), str(item["uuid"]), number, str(item.get("name") or ""))


def _source(item, accounts):
    if not isinstance(item, dict) or "id" not in item:
        return None
    account = item.get("m3u_account")
    account_name = accounts.get(account, "") if isinstance(account, int) else str(account or "")
    return Source(int(item["id"]), str(item.get("name") or f"Stream {item['id']}"), account_name,
                  stats_text(item.get("stream_stats")))


def stats_text(stats):
    """"1920x1080 · h264 · 6.2 Mbps" from Dispatcharr's stream_stats (keys vary by version)."""
    if not isinstance(stats, dict):
        return ""
    parts = [stats.get("resolution") or ""]
    codec = stats.get("video_codec") or ""
    parts.append(str(codec).upper() if codec else "")
    fps = stats.get("source_fps")
    if fps:
        parts.append(f"{float(fps):g} fps")
    bitrate = stats.get("ffmpeg_output_bitrate") or stats.get("bitrate") or stats.get("video_bitrate")
    if bitrate:
        try:
            parts.append(f"{float(bitrate) / 1000:.1f} Mbps" if float(bitrate) > 1000 else f"{float(bitrate):g} kbps")
        except (TypeError, ValueError):
            pass
    return " · ".join(p for p in parts if p)


def find_channel(channels, number_label="", name=""):
    """The Dispatcharr channel Kodi is showing: by channel number (IPTV Simple
    takes it from Dispatcharr's playlist), then by name."""
    try:
        number = float(number_label) if number_label else None
    except ValueError:
        number = None
    by_number = [c for c in channels if number is not None and c.number == number]
    if len(by_number) == 1:
        return by_number[0]
    wanted = _plain(name)
    by_name = [c for c in (by_number or channels) if wanted and _plain(c.name) == wanted]
    return by_name[0] if by_name else (by_number[0] if by_number else None)


def _plain(text):
    return re.sub(r"\s+", " ", re.sub(r"\[/?[A-Z]+[^\]]*\]", "", text or "")).strip().lower()


def base_from_playlist(m3u_url):
    """Dispatcharr's address from IPTV Simple's playlist URL
    (https://host/output/m3u/... -> https://host)."""
    parts = urllib.parse.urlsplit(m3u_url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"
