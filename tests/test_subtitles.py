import os

from conftest import STREAM_ADDON
from stremio.client import StremioClient
from stremio.models import Manifest, Subtitle
from stremio.registry import InstalledAddon
from stremio.subtitles import download_subtitles, fetch_subtitles, parse_languages, select_subtitles


def sub(url, lang):
    return Subtitle(url=url, lang=lang)


def test_select_by_language_preference():
    subs = [sub("e1", "eng"), sub("s1", "spa"), sub("e2", "eng"), sub("e3", "eng"), sub("f1", "fre"),
            sub("e1", "eng")]
    assert [x.url for x in select_subtitles(subs, parse_languages("SPA, eng"))] == ["s1", "e1", "e2"]
    assert [x.url for x in select_subtitles(subs, ())] == ["e1", "s1", "e2", "e3", "f1"]
    assert select_subtitles(subs, ("ger",)) == []


def test_fetch_and_download(server, tmp_path):
    manifest = Manifest.from_dict({**STREAM_ADDON, "resources": ["subtitles"], "types": ["movie"]})
    addon = InstalledAddon(server.url + "/manifest.json", manifest)
    server.routes["/subtitles/movie/tt1/videoSize=10&filename=a.mkv.json"] = {"subtitles": [
        {"id": "1", "url": server.url + "/files/one.srt", "lang": "eng"},
        {"id": "2", "url": server.url + "/files/two", "lang": "pt/br"},
        {"id": "3", "url": server.url + "/files/missing.srt", "lang": "eng"},
    ]}
    server.routes["/files/one.srt"] = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
    server.routes["/files/two"] = b"x"

    subs, errors = fetch_subtitles(StremioClient(), [addon], "movie", "tt1", {"videoSize": 10, "filename": "a.mkv"})
    assert [x.lang for x in subs] == ["eng", "pt/br", "eng"] and not errors

    paths = download_subtitles(StremioClient(), subs, str(tmp_path / "subs"))
    assert [os.path.basename(p) for p in paths] == ["00.eng.srt", "01.ptbr.srt"]
    with open(paths[0], "rb") as f:
        assert f.read().startswith(b"1\n")
