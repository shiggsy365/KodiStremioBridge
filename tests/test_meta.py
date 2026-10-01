import pytest

from conftest import CINEMETA_LIKE
from stremio.client import StremioClient
from stremio.meta import CINEMETA_URL, MetaNotFound, cinemeta_fallback, fetch_meta
from stremio.models import Manifest
from stremio.registry import InstalledAddon


def addon(url, name):
    return InstalledAddon(transport_url=url + "/manifest.json",
                          manifest=Manifest.from_dict({**CINEMETA_LIKE, "name": name}))


def test_first_addon_with_meta_wins(server):
    server.routes["/a/meta/series/tt1.json"] = {"meta": None}
    server.routes["/c/meta/series/tt1.json"] = {"meta": {"id": "tt1", "type": "series", "name": "From C"}}
    addons = [addon(server.url + "/a", "A"), addon(server.url + "/b", "B"), addon(server.url + "/c", "C")]
    source, meta = fetch_meta(StremioClient(), addons, "series", "tt1")
    assert (source, meta.name) == ("C", "From C")
    # A answered null, B 404'd; both were asked in order before C.
    assert server.requests == ["/a/meta/series/tt1.json", "/b/meta/series/tt1.json", "/c/meta/series/tt1.json"]


def test_fallback_used_last_and_not_repeated(server):
    fallback_url = server.url + "/fb/manifest.json"
    server.routes["/fb/meta/movie/tt2.json"] = {"meta": {"id": "tt2", "name": "Fallback"}}
    source, meta = fetch_meta(StremioClient(), [], "movie", "tt2", [("FB", fallback_url)])
    assert source == "FB" and meta.type == "movie"

    server.requests.clear()
    installed = addon(server.url + "/fb", "Installed FB")
    fetch_meta(StremioClient(), [installed], "movie", "tt2", [("FB", fallback_url)])
    assert server.requests == ["/fb/meta/movie/tt2.json"]


def test_not_found(server):
    with pytest.raises(MetaNotFound) as exc:
        fetch_meta(StremioClient(), [addon(server.url, "A")], "movie", "tt3")
    assert "A:" in str(exc.value)


def test_cinemeta_fallback_rules():
    assert cinemeta_fallback("series", "tt1") == [("Cinemeta", CINEMETA_URL)]
    assert cinemeta_fallback("series", "kitsu:1") == []
    assert cinemeta_fallback("channel", "tt1") == []
