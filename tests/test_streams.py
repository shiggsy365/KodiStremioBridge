import pytest

from conftest import STREAM_ADDON
from stremio.client import StremioClient
from stremio.models import Manifest
from stremio.registry import InstalledAddon
from stremio.streams import (
    ELEMENTUM_PLAY, SORT_ADDON, SORT_SIZE, TORRENT, URL, YOUTUBE, Quality, Stream, StreamPrefs,
    describe, fetch_streams, format_size, magnet, parse_keywords, play_path, prepare_streams,
)

GB = 1024 ** 3


def s(addon="A", index=0, **data):
    return Stream.from_dict(data, addon, index)


@pytest.mark.parametrize("text,resolution,hdr,codec", [
    ("Torrentio\n4k DV | HDR", 2160, ("DV", "HDR"), ""),
    ("Movie.2023.2160p.UHD.BluRay.REMUX.HDR10+.HEVC", 2160, ("HDR10+",), "HEVC"),
    ("[RD+] Comet 1080p\nMovie 1080p WEB-DL x264", 1080, (), "H.264"),
    ("720p.HDTV.h265", 720, (), "HEVC"),
    ("Show S01E01 2160p NF WEB-DL DDP5 1 H 265-XEBEC", 2160, (), "HEVC"),
    ("Show (1080p)(VP9)(WebDL)", 1080, (), "VP9"),
    ("Torrentio | DVDRip", 480, (), ""),
    ("Some stream", None, (), ""),
])
def test_quality_parse(text, resolution, hdr, codec):
    q = Quality.parse(text)
    assert (q.resolution, q.hdr, q.codec) == (resolution, hdr, codec)


def test_label_resolution_wins():
    stream = s(url="u", name="Torrentio\n1440p HDR", title="Movie UHD BDRip 1440p | 4K | HDR10")
    assert stream.quality.resolution == 1440


def test_quality_flags_and_size():
    q = Quality.parse("[RD+] Torrentio 2160p\nMovie.REMUX\n👤 12 💾 15.2 GB ⚙️ ThePirateBay")
    assert q.cached and q.remux and q.size == int(15.2 * GB)
    assert Quality.parse("Movie 2023 HDCAM x264").cam
    assert not Quality.parse("Movie 2023 1080p WEB").cam
    assert Quality.parse("💾 700 MB").size == 700 * 1024 ** 2
    assert Quality.parse("💾 1,5 GB", size=123).size == 123  # behaviorHints.videoSize wins


def test_stream_parsing():
    stream = s(url="https://cdn/x.mkv", name="Addon\n1080p", title="Movie.1080p.mkv\n💾 2 GB",
               behaviorHints={"bingeGroup": "g1", "filename": "Movie.1080p.mkv", "videoSize": 2 * GB,
                              "proxyHeaders": {"request": {"User-Agent": "UA/1", "Referer": "https://r/"}}},
               subtitles=[{"url": "https://s/1.srt", "lang": "eng"}, {"lang": "x"}])
    assert stream.kind == URL and stream.binge_group == "g1"
    assert stream.headers == (("User-Agent", "UA/1"), ("Referer", "https://r/"))
    assert stream.quality.resolution == 1080 and stream.quality.size == 2 * GB
    assert [sub.url for sub in stream.subtitles] == ["https://s/1.srt"]
    assert stream.subtitle_extra() == {"videoSize": 2 * GB, "filename": "Movie.1080p.mkv"}


def test_unplayable_entries_rejected():
    assert s(name="only a name") is None
    assert s() is None
    assert s(externalUrl="https://site").kind == "external"


def test_play_paths():
    headers = {"behaviorHints": {"proxyHeaders": {"request": {"User-Agent": "A B", "Cookie": "a=1&b=2"}}}}
    plain = s(url="https://cdn/x.mkv", **headers)
    assert play_path(plain) == "https://cdn/x.mkv|User-Agent=A%20B&Cookie=a%3D1%26b%3D2"
    assert play_path(plain, headers_in_url=False) == "https://cdn/x.mkv"
    assert play_path(s(url="https://cdn/x.mkv")) == "https://cdn/x.mkv"
    assert play_path(s(ytId="abc")) == "plugin://plugin.video.youtube/play/?video_id=abc"

    torrent = s(infoHash="ABCDEF", fileIdx=2, behaviorHints={"filename": "a b.mkv"},
                sources=["tracker:udp://t:80/announce", "dht:ABCDEF"])
    assert torrent.kind == TORRENT and torrent.file_idx == 2
    assert magnet(torrent) == "magnet:?xt=urn:btih:abcdef&dn=a%20b.mkv&tr=udp%3A%2F%2Ft%3A80%2Fannounce"
    assert play_path(torrent) is None
    assert play_path(torrent, torrents_via_elementum=True).startswith(ELEMENTUM_PLAY.format("magnet%3A"))
    assert play_path(s(externalUrl="https://x")) is None


def test_adaptive_detection():
    assert s(url="https://cdn/live/index.m3u8?token=1").adaptive_mimetype == "application/vnd.apple.mpegurl"
    assert s(url="https://cdn/a.mpd").is_adaptive
    assert not s(url="https://cdn/a.mp4").is_adaptive


def test_prepare_sorts_by_quality_then_cached_then_size():
    streams = [
        s("A", 0, url="u1", name="720p"),
        s("A", 0, url="u2", name="1080p 💾 2 GB"),
        s("B", 1, url="u3", name="[RD+] 1080p 💾 1 GB"),
        s("B", 1, url="u4", name="2160p HDR 💾 20 GB"),
        s("B", 1, url="u5", name="no quality info"),
        s("A", 0, url="u2", name="duplicate url"),
    ]
    assert [x.url for x in prepare_streams(streams, StreamPrefs())] == ["u4", "u3", "u2", "u1", "u5"]
    assert [x.url for x in prepare_streams(streams, StreamPrefs(sort=SORT_SIZE))] == ["u4", "u2", "u3", "u1", "u5"]
    assert [x.url for x in prepare_streams(streams, StreamPrefs(sort=SORT_ADDON))] == ["u1", "u2", "u3", "u4", "u5"]


def test_prepare_filters():
    streams = [
        s(url="u1", name="2160p"), s(url="u2", name="1080p HDCAM"), s(url="u3", name="1080p 3D SBS"),
        s(url="u4", name="1080p"), s(infoHash="aa", name="1080p"), s(externalUrl="https://x"),
    ]
    prefs = StreamPrefs(max_resolution=1080, exclude=parse_keywords(" 3D, , hc "))
    assert [x.url for x in prepare_streams(streams, prefs)] == ["u4"]
    with_torrents = StreamPrefs(allow_torrents=True, hide_cam=False)
    assert len(prepare_streams(streams, with_torrents)) == 5


def test_describe():
    headline, details = describe(s(url="u", name="Torrentio\n4k DV", title="Movie.REMUX.HEVC\n💾 15.2 GB"))
    assert headline == "4K · DV · REMUX · HEVC · 15.2 GB"
    assert details == "Torrentio · 4k DV · Movie.REMUX.HEVC · 💾 15.2 GB"
    assert format_size(None) == "" and format_size(3 * 1024 ** 2) == "3.0 MB"


def test_fetch_streams_same_addon_installed_twice(server):
    manifest = Manifest.from_dict(STREAM_ADDON)
    addons = [InstalledAddon(server.url + "/one/manifest.json", manifest),
              InstalledAddon(server.url + "/two/manifest.json", manifest),
              InstalledAddon(server.url + "/down/manifest.json", manifest)]
    server.routes["/one/stream/movie/tt1.json"] = {"streams": [{"url": "https://1", "name": "720p"}, {"bad": 1}]}
    server.routes["/two/stream/movie/tt1.json"] = {"streams": [{"url": "https://2", "name": "1080p"}]}

    streams, errors, cancelled = fetch_streams(StremioClient(), addons, "movie", "tt1")
    assert [(x.url, x.addon_index) for x in streams] == [("https://1", 0), ("https://2", 1)]
    assert len(errors) == 1 and not cancelled


def test_invisible_characters_are_stripped():
    stream = s(url="u", name="   4K \u200d\u200d    \u200d\u26a1\u200d\n  \u3008Bluray\u3009\u200d     ",
               description="\u270e  Title \n\u25a3  HEVC  \u2726  DV \u00b7 HDR  ")
    assert stream.name == "4K \u26a1\n\u3008Bluray\u3009"
    assert stream.title == "\u270e Title\n\u25a3 HEVC \u2726 DV \u00b7 HDR"
    assert stream.quality.resolution == 2160 and stream.quality.cached


def test_probe(server):
    from stremio.net import Session
    from stremio.streams import fallback_order, probe

    video = {"Content-Type": "video/x-matroska", "Content-Range": "bytes 0-1/20000000000"}
    server.routes["/ok.mkv"] = (206, video, b"\x1a\x45")
    server.routes["/html.mkv"] = (200, {"Content-Type": "text/html"}, b"<html>")
    server.routes["/tiny.mp4"] = (206, {"Content-Type": "video/mp4", "Content-Range": "bytes 0-1/900000"}, b"xx")
    server.routes["/live.m3u8"] = (200, {"Content-Type": "application/vnd.apple.mpegurl"}, b"#EXTM3U")
    session = Session()
    big = {"name": "2160p", "title": "💾 20 GB"}

    assert probe(session, s(url=server.url + "/ok.mkv", **big)) == (True, "")
    assert probe(session, s(url=server.url + "/missing.mkv", **big), sleep=None) == (False, "HTTP 404")  # no retry
    assert probe(session, s(url=server.url + "/html.mkv", **big))[0] is False
    assert probe(session, s(url=server.url + "/tiny.mp4", **big))[0] is False          # placeholder
    assert probe(session, s(url=server.url + "/tiny.mp4"))[0] is True                  # size unknown: fine
    assert probe(session, s(url=server.url + "/live.m3u8"))[0] is True
    assert probe(session, s(url="http://127.0.0.1:1/x.mkv"), timeout=2, retry_delay=0)[0] is False  # unreachable
    assert probe(session, s(infoHash="abc")) == (True, "")                             # not probed

    a, b, c = s(url="a"), s(url="b"), s(url="c")
    assert fallback_order([a, b, c], b) == [b, a, c] and fallback_order([a, b], None) == [a, b]



def test_probe_retries_transient_errors(server):
    from stremio.net import Session
    from stremio.streams import probe

    answers = iter([(400, {}, b"not ready"), (400, {}, b"not ready"),
                    (206, {"Content-Type": "video/mp4", "Content-Range": "bytes 0-1/9000000000"}, b"xx")])
    server.routes["/fresh.mp4"] = lambda: next(answers)
    naps = []
    stream = s(url=server.url + "/fresh.mp4", name="2160p")
    assert probe(Session(), stream, sleep=naps.append) == (True, "")
    assert naps == [2.0, 2.0]

    server.routes["/dead.mp4"] = (503, {}, b"")
    naps.clear()
    assert probe(Session(), s(url=server.url + "/dead.mp4"), sleep=naps.append) == (False, "HTTP 503")
    assert len(naps) == 2                                      # gave up after two retries
