"""Dispatcharr Bridge (script.shiggsy365.dispatcharrbridge): the Dispatcharr API side."""

import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "script.shiggsy365.dispatcharrbridge", "resources", "lib"))

from dispatcharrbridge import api  # noqa: E402

BASE = "https://dispatcharr.example"
CHANNELS_1 = {"next": f"{BASE}/api/channels/channels/?page=2", "results": [
    {"id": 1, "uuid": "u-1", "channel_number": 101.0, "name": "BBC One HD"},
    {"id": 2, "uuid": "u-2", "channel_number": 102.0, "name": "BBC Two"}]}
CHANNELS_2 = {"next": None, "results": [{"id": 3, "uuid": "u-3", "channel_number": 102.0, "name": "Sky Sports"},
                                        {"id": 4, "name": "no uuid"}]}


class FakeDispatcharr:
    """urlopen stand-in: routes by method + path; records requests."""

    def __init__(self, running=None):
        self.requests, self.running = [], running or {}

    def __call__(self, request, timeout=None):
        path = request.full_url.replace(BASE, "")
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), path, body, dict(request.header_items())))
        routes = {
            "/api/channels/channels/": CHANNELS_1,
            "/api/channels/channels/?page=2": CHANNELS_2,
            "/api/m3u/accounts/": [{"id": 7, "name": "Provider A"}, {"id": 8, "name": "Provider B"}],
            "/api/channels/channels/1/streams/": [
                {"id": 11, "name": "BBC One FHD", "m3u_account": 7,
                 "stream_stats": {"resolution": "1920x1080", "video_codec": "h264", "source_fps": 50,
                                  "ffmpeg_output_bitrate": 6200}},
                {"id": 12, "name": "BBC One backup", "m3u_account": 8, "stream_stats": None}],
        }
        if path.startswith("/proxy/ts/status/"):
            uuid = path.rsplit("/", 1)[1]
            if uuid not in self.running:
                raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
            return io.BytesIO(json.dumps({"stream_id": self.running[uuid]}).encode())
        if path.startswith("/proxy/ts/"):
            return io.BytesIO(json.dumps({"message": "Stream changed successfully"}).encode())
        if path in routes:
            return io.BytesIO(json.dumps(routes[path]).encode())
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)


def test_channels_sources_and_switching():
    fake = FakeDispatcharr(running={"u-1": "11"})
    client = api.Dispatcharr(BASE + "/", " key123 ", opener=fake)
    channels = client.channels()
    assert [c.name for c in channels] == ["BBC One HD", "BBC Two", "Sky Sports"]          # both pages, no uuid-less
    headers = fake.requests[0][3]
    assert headers["X-api-key"] == "key123" and headers["Authorization"] == "ApiKey key123"

    one = channels[0]
    sources = client.sources(one, client.accounts())
    assert [(s.id, s.account, s.details) for s in sources] == [
        (11, "Provider A", "1920x1080 · H264 · 50 fps · 6.2 Mbps"), (12, "Provider B", "")]
    assert client.current_source(one) == 11 and client.current_source(channels[1]) is None
    client.switch(one, 12)
    client.next_source(one)
    assert fake.requests[-2][:3] == ("POST", "/proxy/ts/change_stream/u-1", {"stream_id": 12})
    assert fake.requests[-1][:3] == ("POST", "/proxy/ts/next_stream/u-1", {})


def test_errors_are_explained():
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

    with pytest.raises(api.DispatcharrError, match="admin"):
        api.Dispatcharr(BASE, "k", opener=refuse).channels()

    def unreachable(request, timeout=None):
        raise urllib.error.URLError("no route")

    with pytest.raises(api.DispatcharrError, match="reach"):
        api.Dispatcharr(BASE, "k", opener=unreachable).channels()


def test_find_channel_by_number_then_name():
    channels = [api.Channel(1, "a", 101.0, "BBC One HD"), api.Channel(2, "b", 102.0, "BBC Two"),
                api.Channel(3, "c", 102.0, "Sky Sports")]
    assert api.find_channel(channels, "101", "whatever").uuid == "a"
    assert api.find_channel(channels, "102", "[B]Sky Sports[/B]").uuid == "c"   # shared number: the name decides
    assert api.find_channel(channels, "", "bbc  two").uuid == "b"
    assert api.find_channel(channels, "999", "Nope") is None


def test_base_from_playlist():
    assert api.base_from_playlist("https://dispatcharr.example/output/m3u/default?x=1") == BASE
    assert api.base_from_playlist("special://profile/list.m3u") == ""


def test_long_press_keymap():
    import xml.etree.ElementTree as ET

    from dispatcharrbridge import keymap

    root = ET.fromstring(keymap.keymap_xml())
    for window in ("FullscreenLiveTV", "FullscreenVideo"):
        # OK/Select: an Android TV remote's OK is a keyboard key, a CEC/IR remote's is "select"
        for path in ("keyboard/return", "keyboard/enter", "remote/select"):
            key = root.find(f"{window}/{path}")
            assert key.get("mod") == "longpress" and key.text == "RunScript(script.shiggsy365.dispatcharrbridge)"
        assert root.find(f"{window}/keyboard/play_pause") is None   # Play/Pause just pauses again
