import pytest

from conftest import CINEMETA_LIKE
from stremio import AddonRequestError
from stremio.catalog import (
    fetch_catalog, find_catalog, next_page, nominal_page_size, ordered_extra, with_default_filters,
)
from stremio.client import StremioClient
from stremio.models import Manifest
from stremio.registry import InstalledAddon

YEAR_CATALOG = {"type": "movie", "id": "year", "name": "By year",
                "extra": [{"name": "genre", "isRequired": True, "options": ["2026", "2025"]},
                          {"name": "skip"}]}


def make_addon(url, catalogs=None):
    manifest = {**CINEMETA_LIKE, "catalogs": catalogs or CINEMETA_LIKE["catalogs"]}
    return InstalledAddon(transport_url=url + "/manifest.json", manifest=Manifest.from_dict(manifest))


def test_find_catalog():
    addon = make_addon("https://a")
    assert find_catalog(addon, "series", "top").name == "Popular"
    assert find_catalog(addon, "series", "nope") is None


def test_with_default_filters():
    catalog = make_addon("https://a", [YEAR_CATALOG]).manifest.catalogs[0]
    assert with_default_filters(catalog, {}) == {"genre": "2026"}
    assert with_default_filters(catalog, {"genre": "2025"}) == {"genre": "2025"}
    optional = Manifest.from_dict(CINEMETA_LIKE).catalogs[0]
    assert with_default_filters(optional, {}) == {}


def test_ordered_extra_follows_manifest():
    catalog = Manifest.from_dict(CINEMETA_LIKE).catalogs[0]
    assert ordered_extra(catalog, {"genre": "Action", "unknown": "x"}, skip=100) == [
        ("genre", "Action"), ("skip", 100), ("unknown", "x")]
    assert ordered_extra(catalog, {"genre": ""}) == []


def test_fetch_catalog(server):
    server.routes["/catalog/movie/top/genre=Sci-Fi&skip=20.json"] = {"metas": [
        {"id": "tt1", "name": "One"}, {"id": "tt2"}, {"id": "tt3", "name": "Three", "type": "series"},
    ]}
    addon = make_addon(server.url)
    previews = fetch_catalog(StremioClient(), addon, addon.manifest.catalogs[0], {"genre": "Sci-Fi"}, skip=20)
    assert [(p.id, p.type) for p in previews] == [("tt1", "movie"), ("tt3", "series")]


def test_fetch_catalog_requires_metas(server):
    server.routes["/catalog/movie/top.json"] = {"error": "nope"}
    addon = make_addon(server.url)
    with pytest.raises(AddonRequestError):
        fetch_catalog(StremioClient(), addon, addon.manifest.catalogs[0])


def test_nominal_page_size():
    assert [nominal_page_size(n) for n in (9, 20, 48, 49, 50, 95, 100, 900)] == [10, 20, 50, 50, 50, 100, 100, 900]


def test_next_page():
    catalogs = Manifest.from_dict(CINEMETA_LIKE).catalogs
    paged, unpaged = catalogs[0], catalogs[1]
    # Cinemeta-style: nominal 50 per page, a few items dropped server-side.
    assert next_page(paged, 0, 49) == (50, 50)
    assert next_page(paged, 50, 48, page_size=50) == (100, 50)
    assert next_page(paged, 100, 12, page_size=50) is None      # mostly empty: last page
    assert next_page(paged, 0, 100) == (100, 100)
    assert next_page(paged, 0, 9) is None                        # tiny catalog
    assert next_page(paged, 0, 0) is None
    assert next_page(unpaged, 0, 100) is None                    # no skip support


def test_next_page_with_declared_page_size():
    def catalog(size):
        return Manifest.from_dict({**CINEMETA_LIKE, "catalogs": [
            {"type": "movie", "id": "x", "pageSize": size, "extra": [{"name": "skip"}]}]}).catalogs[0]

    accurate = catalog(20)
    assert accurate.page_size == 20
    assert next_page(accurate, 0, 19) == (20, 20)
    assert next_page(accurate, 20, 20, page_size=20) == (40, 20)
    assert next_page(accurate, 40, 5, page_size=20) is None
    assert next_page(catalog(5), 0, 5) == (5, 5)            # small but consistent: trusted

    # Declares 50 but serves 20 (seen in the wild): measure instead of skipping 30 items.
    wrong = catalog(50)
    assert next_page(wrong, 0, 20) == (20, 20)
    assert next_page(wrong, 20, 20, page_size=20) == (40, 20)


BYW = [{"type": "movie", "id": f"aicat_because_watched_movie_seed_{n}", "name": name}
       for n, name in [(1435, "The Batman"), (1444, "Whiplash"), (1950, "Arrival")]]


def test_catalog_family_and_slot():
    from stremio.catalog import catalog_family, catalog_slot

    assert catalog_family("aicat_because_watched_movie_seed_1435") == "aicat_because_watched_movie_seed"
    assert catalog_family("trakt.list.22899376") == "trakt.list"
    assert catalog_family("tmdb.discover.movie.decades.2020s") is None
    assert catalog_family("top") is None
    addon = make_addon("https://a", BYW + [{"type": "series", "id": "aicat_because_watched_movie_seed_9"}])
    assert [catalog_slot(addon, c) for c in addon.manifest.catalogs] == [(0, 3), (1, 3), (2, 3), (0, 1)]


def test_resolve_catalog_after_rotation():
    from stremio.catalog import resolve_catalog

    rotated = make_addon("https://a", BYW[:1] + [{**BYW[1], "id": "aicat_because_watched_movie_seed_77",
                                                  "name": "Dune"}] + BYW[2:])
    old_id = BYW[1]["id"]
    assert resolve_catalog(rotated, "movie", BYW[0]["id"], 0, 3).name == "The Batman"   # exact id wins
    assert resolve_catalog(rotated, "movie", old_id, 1, 3).name == "Dune"               # slot fallback
    assert resolve_catalog(rotated, "movie", old_id) is None                            # no slot info
    assert resolve_catalog(rotated, "movie", old_id, 1, 4) is None                      # family resized
    assert resolve_catalog(rotated, "movie", old_id, 7, 3) is None
