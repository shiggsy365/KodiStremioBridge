from conftest import CINEMETA_LIKE
from stremio.catalog import search
from stremio.client import StremioClient
from stremio.history import SearchHistory
from stremio.models import Manifest
from stremio.registry import AddonRegistry


def test_is_searchable_alone():
    top, _, search_only = Manifest.from_dict(CINEMETA_LIKE).catalogs
    assert search_only.is_searchable_alone and not top.is_searchable_alone
    needs_genre = Manifest.from_dict({**CINEMETA_LIKE, "catalogs": [{
        "type": "movie", "id": "x", "extra": [{"name": "search"}, {"name": "genre", "isRequired": True}]}]}).catalogs[0]
    assert needs_genre.is_searchable and not needs_genre.is_searchable_alone


def test_registry_search_catalogs(tmp_path, server):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    a = reg.add(server.url + "/a/manifest.json", Manifest.from_dict(CINEMETA_LIKE))
    assert [(x.key, c.key) for x, c in reg.search_catalogs()] == [(a.key, "movie/search")]
    assert reg.search_catalogs("series") == []
    reg.set_catalog_pref(a.key, "movie/search", search=False)
    assert reg.search_catalogs() == []


def test_search_runs_all_targets(tmp_path, server):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    reg.add(server.url + "/a/manifest.json", Manifest.from_dict(CINEMETA_LIKE))
    reg.add(server.url + "/b/manifest.json", Manifest.from_dict({**CINEMETA_LIKE, "name": "B"}))
    server.routes["/b/catalog/movie/search/search=it.json"] = {"metas": [{"id": "tt1", "name": "It"}]}

    results, errors, cancelled = search(StremioClient(), reg.search_catalogs(), "it")
    assert [(a.name, [p.id for p in previews]) for a, _, previews in results] == [("B", ["tt1"])]
    assert len(errors) == 1 and not cancelled  # addon A 404s


def test_history(tmp_path):
    path = str(tmp_path / "h.json")
    history = SearchHistory(path, limit=3)
    for q in ["one", "two", " One ", "three", "four", ""]:
        history.add(q)
    assert history.all() == ["four", "three", "One"]
    assert SearchHistory(path).all() == ["four", "three", "One"]
    history.remove("three")
    assert SearchHistory(path).all() == ["four", "One"]

    (tmp_path / "bad.json").write_text("{nope")
    assert SearchHistory(str(tmp_path / "bad.json")).all() == []


def test_is_search_flag():
    catalogs = Manifest.from_dict({**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "s", "name": "Search", "isSearch": True},
        {"type": "movie", "id": "t", "isSearch": True, "extra": [{"name": "search"}, {"name": "skip"}]},
        {"type": "movie", "id": "u", "isSearch": False},
    ]}).catalogs
    flagged, declared, off = catalogs
    assert flagged.is_searchable_alone and not flagged.is_browsable
    assert declared.is_searchable_alone and declared.is_browsable   # its own extra is kept as-is
    assert not off.is_searchable


def test_search_order_and_toggles(tmp_path, server):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    cats = [{"type": t, "id": f"s{i}", "extra": [{"name": "search", "isRequired": True}]}
            for i, t in enumerate(["movie", "series"])]
    a = reg.add("https://a/manifest.json", Manifest.from_dict({**CINEMETA_LIKE, "catalogs": cats}))
    b = reg.add("https://b/manifest.json", Manifest.from_dict({**CINEMETA_LIKE, "name": "B", "catalogs": cats[:1]}))
    keys = lambda: [reg.search_key(x, c) for x, c, _ in reg.search_entries()]
    assert keys() == [f"{a.key}|movie/s0", f"{a.key}|series/s1", f"{b.key}|movie/s0"]

    assert reg.move_search_catalog(f"{b.key}|movie/s0", -10) == 0          # to the top
    reg.set_catalog_pref(a.key, "series/s1", search=False)
    reg = AddonRegistry(reg.path)                                           # persisted
    assert keys() == [f"{b.key}|movie/s0", f"{a.key}|movie/s0", f"{a.key}|series/s1"]
    assert [e[2] for e in reg.search_entries()] == [True, True, False]
    assert [(x.name, c.id) for x, c in reg.search_catalogs()] == [("B", "s0"), ("Example Meta", "s0")]

    # A catalog added later goes to the end; a removed one simply disappears.
    reg.add("https://c/manifest.json", Manifest.from_dict({**CINEMETA_LIKE, "name": "C", "catalogs": cats[1:]}))
    assert [x.name for x, _, _ in reg.search_entries()] == ["B", "Example Meta", "Example Meta", "C"]
    reg.remove(b.key)
    assert [x.name for x, _, _ in reg.search_entries()] == ["Example Meta", "Example Meta", "C"]
