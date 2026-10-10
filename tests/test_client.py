import pytest

from conftest import CINEMETA_LIKE
from stremio import AddonRequestError, ManifestError
from stremio.client import StremioClient, base_url, build_resource_url, normalize_transport_url


@pytest.mark.parametrize("given,expected", [
    ("https://a.com/manifest.json", "https://a.com/manifest.json"),
    ("https://a.com", "https://a.com/manifest.json"),
    ("https://a.com/cfg/", "https://a.com/cfg/manifest.json"),
    ("  stremio://a.com/x%7Cy/manifest.json ", "https://a.com/x%7Cy/manifest.json"),
    ("a.com/manifest.json", "https://a.com/manifest.json"),
    ("http://127.0.0.1:7000/manifest.json", "http://127.0.0.1:7000/manifest.json"),
])
def test_normalize_transport_url(given, expected):
    assert normalize_transport_url(given) == expected


@pytest.mark.parametrize("bad", ["", "   ", "ftp://a.com/manifest.json", "https://"])
def test_normalize_rejects_bad_urls(bad):
    with pytest.raises(ManifestError):
        normalize_transport_url(bad)


def test_base_url():
    assert base_url("https://a.com/cfg=abc/manifest.json") == "https://a.com/cfg=abc"
    assert base_url("https://a.com/manifest.json") == "https://a.com"


def test_build_resource_url():
    assert build_resource_url("https://a.com", "meta", "movie", "tt1") == "https://a.com/meta/movie/tt1.json"
    assert (build_resource_url("https://a.com", "stream", "series", "tt1:1:2")
            == "https://a.com/stream/series/tt1%3A1%3A2.json")
    assert (build_resource_url("https://a.com", "catalog", "movie", "top",
                               {"genre": "Sci-Fi & Fantasy", "skip": 100, "search": None})
            == "https://a.com/catalog/movie/top/genre=Sci-Fi%20%26%20Fantasy&skip=100.json")


def test_fetch_manifest(server):
    server.routes["/cfg/manifest.json"] = CINEMETA_LIKE
    transport_url, manifest = StremioClient().fetch_manifest(server.url + "/cfg")
    assert transport_url == server.url + "/cfg/manifest.json"
    assert manifest.name == "Example Meta"


def test_fetch_manifest_errors(server):
    server.routes["/bad/manifest.json"] = b"not json"
    server.routes["/notmanifest/manifest.json"] = {"hello": "world"}
    client = StremioClient()
    for path in ("/missing", "/bad", "/notmanifest"):
        with pytest.raises(ManifestError):
            client.fetch_manifest(server.url + path)


def test_get_resource(server):
    server.routes["/catalog/movie/top/skip=100.json"] = {"metas": [{"id": "tt1"}]}
    data = StremioClient().get_resource(server.url + "/manifest.json", "catalog", "movie", "top", {"skip": 100})
    assert data == {"metas": [{"id": "tt1"}]}
    with pytest.raises(AddonRequestError):
        StremioClient().get_resource(server.url + "/manifest.json", "meta", "movie", "nope")


def test_connection_error_is_wrapped():
    with pytest.raises(AddonRequestError):
        StremioClient(timeout=2).get_json("http://127.0.0.1:1/manifest.json")


def test_cached_resources(server, tmp_path):
    from stremio.cache import Cache

    clock = [1000.0]
    cache = Cache(str(tmp_path / "c.db"), clock=lambda: clock[0])
    logs = []
    client = StremioClient(cache=cache, ttls={"catalog": 60}, log=logs.append)
    transport = server.url + "/manifest.json"
    server.routes["/catalog/movie/top.json"] = {"metas": [{"id": "tt1"}]}
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1"}}

    for _ in range(3):
        assert client.get_resource(transport, "catalog", "movie", "top") == {"metas": [{"id": "tt1"}]}
        client.get_resource(transport, "meta", "movie", "tt1")      # no TTL: never cached
    assert server.requests.count("/catalog/movie/top.json") == 1
    assert server.requests.count("/meta/movie/tt1.json") == 3

    # Expired + addon down: the stale copy is served and the failure logged.
    clock[0] += 61
    del server.routes["/catalog/movie/top.json"]
    assert client.get_resource(transport, "catalog", "movie", "top") == {"metas": [{"id": "tt1"}]}
    assert server.requests.count("/catalog/movie/top.json") == 2 and logs


def test_resource_url_keeps_manifest_query(server):
    from stremio.client import resource_url

    assert (resource_url("https://a.com/cfg/manifest.json?bcv=48", "meta", "movie", "tt1")
            == "https://a.com/cfg/meta/movie/tt1.json?bcv=48")
    assert resource_url("https://a.com/manifest.json", "meta", "movie", "tt1") == "https://a.com/meta/movie/tt1.json"
    server.routes["/meta/movie/tt1.json?v=2"] = {"meta": {"id": "tt1"}}
    assert StremioClient().get_resource(server.url + "/manifest.json?v=2", "meta", "movie", "tt1") == {"meta": {"id": "tt1"}}


def test_stale_ok_answers_from_an_expired_copy(server, tmp_path):
    """Widgets: an expired copy straight away, noted for a refresh; nothing fetched."""
    from stremio.cache import Cache

    clock = [1000.0]
    client = StremioClient(cache=Cache(str(tmp_path / "c.db"), clock=lambda: clock[0]), ttls={"catalog": 60})
    transport = server.url + "/manifest.json"
    server.routes["/catalog/movie/top/genre=Action.json"] = {"metas": [{"id": "old"}]}
    extra = [("genre", "Action")]
    client.get_resource(transport, "catalog", "movie", "top", extra)
    server.routes["/catalog/movie/top/genre=Action.json"] = {"metas": [{"id": "new"}]}
    clock[0] += 61

    assert client.get_resource(transport, "catalog", "movie", "top", extra, stale_ok=True) == {"metas": [{"id": "old"}]}
    assert server.requests.count("/catalog/movie/top/genre=Action.json") == 1
    assert client.served_stale == [(transport, "catalog", "movie", "top", extra)]

    # without stale_ok it waits for the addon, as before
    assert client.get_resource(transport, "catalog", "movie", "top", extra) == {"metas": [{"id": "new"}]}
    # and a fresh copy isn't noted
    client.served_stale = []
    client.get_resource(transport, "catalog", "movie", "top", extra, stale_ok=True)
    assert client.served_stale == []
