"""Stream picker data: badges and text from addon labels, badge strips."""

import os
import struct
import zlib

from stremio.badgestrip import BadgeStrips, compose
from stremio.streaminfo import audio_codecs, normalise, stream_info, structured_fields
from stremio.streams import Stream

BADGES = os.path.join(os.path.dirname(__file__), "..", "plugin.video.stremiobridge", "resources", "media", "badges")

# Real AIOStreams output (URL left out), as of 2026-10.
AIO_4K = {
    "name": "   4K ‍‍    ‍‍‍⚡‍‍\n  〈Web‍-‍dl〉‍     ", "url": "https://x/1",
    "description": "✎  Unabomber (2026) \n▣  HEVC  ✦  DV · HDR  \n♬  Atmos · DD+  ♯  5.1 \n◈ 10.6 GB · 13.9 ᴹᵇᵖˢ \n"
                   "⛊ [TB] StremThru Torz · BYNDR\n✓ ᴇɴ · sᴜʙ (ᴇɴ)  » ɴᴇᴛғʟɪx ",
    "behaviorHints": {"bingeGroup": "aiostreams|2160p|WEB-DL|BYNDR", "videoSize": 10558879147,
                      "filename": "Unabomber.2026.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-BYNDR.mkv"}}
AIO_USENET = {
    "name": "‍     ‍‍⁽ⁿᶻᵇ⁾‍‍‍‍⚡‍‍\n  〈Web‍-‍dl〉‍     ", "url": "https://x/2",
    "description": "✎  Unabomber (2026) \n▣  AVC  \n◈ 931 MB · 1.23 ᴹᵇᵖˢ · 6d\n⛊ [ND] Newznab · rbb · NZBgeek\n✓ ᴇɴ · sᴜʙ (ᴇɴ) ",
    "behaviorHints": {"videoSize": 930808000, "filename": "unabomber.2026.web.h264-rbb",
                      "bingeGroup": "aiostreams|WEB-DL|rbb"}}
TORRENT = {"name": "Torrentio\n1080p", "infoHash": "a" * 40,
           "title": "Movie.2019.1080p.BluRay.REMUX.AVC.TrueHD.7.1.Atmos-FGT\n👤 12 💾 30.2 GB ⚙️ ThePirateBay"}


def info(data):
    return stream_info(Stream.from_dict(data, "AIOStreams"))


def test_normalise_makes_addon_text_drawable():
    assert normalise("◈ 10.6 GB · 13.9 ᴹᵇᵖˢ") == "10.6 GB · 13.9 Mbps"
    assert normalise("✓ ᴇɴ · sᴜʙ (ᴇɴ)  » ɴᴇᴛғʟɪx ") == "en · sub (en) netflix"
    assert normalise("  4K ‍‍ ⚡\n\n 〈Web‍-‍dl〉 ") == "4K\nWeb-dl"


def test_aiostreams_4k():
    i = info(AIO_4K)
    assert i.badges() == ["dl_cached", "res_4k", "rel_webdl", "vis_dv", "vis_hdr", "aud_atmos", "ch_51", "net_netflix"]
    assert (i.size, i.bitrate, i.debrid, i.provider, i.group, i.languages) == (
        "10.6 GB", "13.9 Mbps", "TorBox", "StremThru Torz", "BYNDR", "en")


def test_aiostreams_usenet_and_a_torrent():
    i = info(AIO_USENET)
    assert i.badges() == ["dl_cached", "rel_webdl"] and (i.debrid, i.provider, i.size) == ("Usenet", "Newznab", "931 MB")
    t = info(TORRENT)
    assert t.badges() == ["dl_p2p", "res_1080p", "rel_remux", "aud_atmos", "ch_71"]
    assert audio_codecs("TrueHD 7.1 Atmos") == ["Atmos", "TrueHD"]


def test_structured_format_wins():
    text = "res: 2160p\nsrc: BluRay REMUX\nvis: DV, HDR10+\naud: TrueHD, Atmos\nch: 7.1\nsize: 58.2 GB\nsvc: RD+\nnet: Netflix"
    assert structured_fields(text)["vis"] == "DV, HDR10+"
    assert structured_fields("Quality: 4K\nnot: a format") == {}            # fewer than three known keys
    i = info({"name": "AIOStreams", "url": "https://x", "description": text})
    assert i.badges() == ["dl_cached", "res_4k", "rel_remux", "vis_dv", "vis_hdr10plus", "aud_atmos", "ch_71",
                          "net_netflix"]
    assert (i.debrid, i.size, i.details) == ("Real-Debrid", "58.2 GB", "")


def test_badge_strips(tmp_path):
    strips = BadgeStrips(BADGES, str(tmp_path))
    path = strips.strip(["res_4k", "nope", "aud_atmos"])
    assert path and strips.strip(["res_4k", "aud_atmos"]) == path              # unknown keys skipped, cached
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    widths = [strips._badge(k)[0] for k in ("res_4k", "aud_atmos")]
    assert (width, height) == (sum(widths) + 12, 64)
    assert strips.strip(["nope"]) == ""


def test_compose_pads_shorter_badges():
    tall, short = (2, 3, b"\xff" * 24), (1, 1, b"\x80" * 4)
    data = compose([tall, short])
    raw = zlib.decompress(data[data.index(b"IDAT") + 4:-16])
    row = 1 + (2 + 12 + 1) * 4
    assert len(raw) == 3 * row and raw[row + 1 + (2 + 12) * 4:2 * row] == b"\0\0\0\0"


def test_aiostreams_template_output():
    """What the suggested AIOStreams description template produces (blank lines
    where a value is missing)."""
    text = ("res: 2160p\nsrc: WEB-DL\nvis: DV, HDR\naud: Atmos, DD+\nch: 5.1\nsize: 10.6 GB\nbr: 13.9 Mbps\n"
            "svc: TB\ncached: yes\ntype: debrid\nprov: StremThru Torz\ngrp: BYNDR\nlang: en\nnet: Netflix\n\n")
    i = info({"name": "4K", "url": "https://x", "description": text})
    assert i.badges() == ["dl_cached", "res_4k", "rel_webdl", "vis_dv", "vis_hdr", "aud_atmos", "ch_51", "net_netflix"]
    assert i.summary() == "10.6 GB · 13.9 Mbps · TorBox · StremThru Torz · BYNDR · EN"
    torrent = info({"name": "1080p", "url": "https://x", "description": "res: 1080p\nsrc: BluRay\ncached: no\ntype: p2p"})
    assert torrent.badges()[:2] == ["dl_p2p", "res_1080p"] and not torrent.cached


def test_badges_in_another_png_layout_are_skipped(tmp_path):
    from PIL import Image

    Image.new("RGBA", (40, 64), (255, 0, 0, 255)).save(tmp_path / "res_4k.png", optimize=True)   # adaptive filters
    (tmp_path / "rel_webdl.png").write_bytes(b"not a png")
    strips = BadgeStrips(str(tmp_path), str(tmp_path / "cache"))
    assert strips._badge("rel_webdl") is None
    assert strips.strip(["res_4k", "rel_webdl"]) in ("", strips.strip(["res_4k"]))  # never an error
