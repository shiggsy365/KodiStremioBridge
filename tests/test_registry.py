import pytest

from conftest import CINEMETA_LIKE, STREAM_ADDON
from stremio.models import Manifest
from stremio.registry import AddonRegistry, DuplicateAddonError, UnknownAddonError

META_URL = "https://meta.example/manifest.json"
STREAM_URL = "https://streams.example/token=abc/manifest.json"


@pytest.fixture
def registry(tmp_path):
    reg = AddonRegistry(str(tmp_path / "addons.json"))
    reg.add(META_URL, Manifest.from_dict(CINEMETA_LIKE))
    reg.add(STREAM_URL, Manifest.from_dict(STREAM_ADDON))
    return reg


def reload(reg):
    return AddonRegistry(reg.path)


def test_add_persists(registry):
    loaded = reload(registry)
    assert [a.transport_url for a in loaded.all()] == [META_URL, STREAM_URL]
    assert loaded.get(STREAM_URL).base_url == "https://streams.example/token=abc"
    assert loaded.get(META_URL).manifest == registry.get(META_URL).manifest


def test_duplicate_url_rejected_but_same_addon_other_config_allowed(registry):
    with pytest.raises(DuplicateAddonError):
        registry.add(META_URL, Manifest.from_dict(CINEMETA_LIKE))
    registry.add("https://streams.example/token=xyz/manifest.json", Manifest.from_dict(STREAM_ADDON))
    assert len(registry.all()) == 3


def test_move_is_clamped_and_persisted(registry):
    assert registry.move(STREAM_URL, -1) == 0
    assert registry.move(STREAM_URL, -5) == 0
    assert [a.transport_url for a in reload(registry).all()] == [STREAM_URL, META_URL]
    assert registry.move(STREAM_URL, 10) == 1


def test_enable_disable_affects_routing(registry):
    assert [a.name for a in registry.addons_for("stream", "movie", "tt1")] == ["Example Streams"]
    registry.set_enabled(STREAM_URL, False)
    assert registry.addons_for("stream", "movie", "tt1") == []
    assert reload(registry).get(STREAM_URL).enabled is False


def test_addons_for_respects_prefixes(registry):
    assert [a.name for a in registry.addons_for("meta", "movie", "tt1")] == ["Example Meta"]
    assert registry.addons_for("meta", "movie", "kitsu:1") == []


def test_remove(registry):
    registry.remove(META_URL)
    assert [a.transport_url for a in reload(registry).all()] == [STREAM_URL]
    with pytest.raises(UnknownAddonError):
        registry.remove(META_URL)


def test_catalog_prefs(registry):
    addon = registry.get(META_URL)
    assert [c.key for c in addon.home_catalogs()] == ["movie/top", "series/top"]
    assert [c.key for c in addon.search_catalogs()] == ["movie/search"]

    registry.set_catalog_pref(META_URL, "series/top", home=False)
    addon = reload(registry).get(META_URL)
    assert [c.key for c in addon.home_catalogs()] == ["movie/top"]


def test_update_manifest_drops_stale_prefs(registry):
    registry.set_catalog_pref(META_URL, "series/top", home=False)
    registry.set_catalog_pref(META_URL, "movie/top", home=False)
    trimmed = {**CINEMETA_LIKE, "version": "2.0.0", "catalogs": CINEMETA_LIKE["catalogs"][:1]}
    registry.update_manifest(META_URL, Manifest.from_dict(trimmed))
    addon = reload(registry).get(META_URL)
    assert addon.manifest.version == "2.0.0"
    assert set(addon.catalog_prefs) == {"movie/top"}


def test_corrupt_file_is_backed_up(tmp_path):
    path = tmp_path / "addons.json"
    path.write_text("{ not json")
    messages = []
    reg = AddonRegistry(str(path), log=messages.append)
    assert reg.all() == []
    assert (tmp_path / "addons.json.corrupt").exists()
    assert messages


def test_key_lookup(registry):
    addon = registry.get(STREAM_URL)
    assert len(addon.key) == 10 and "abc" not in addon.key
    assert registry.get(addon.key) is addon
    assert reload(registry).get(addon.key).transport_url == STREAM_URL
    registry.set_enabled(addon.key, False)
    assert registry.get(STREAM_URL).enabled is False


def test_home_catalogs_and_types(registry):
    assert [(a.name, c.key) for a, c in registry.home_catalogs()] == [
        ("Example Meta", "movie/top"), ("Example Meta", "series/top")]
    assert [c.key for _, c in registry.home_catalogs("series")] == ["series/top"]
    assert registry.home_types() == ["movie", "series"]

    registry.set_catalog_pref(META_URL, "movie/top", home=False)
    assert registry.home_types() == ["series"]
    registry.set_enabled(META_URL, False)
    assert registry.home_types() == []


def test_home_types_order(tmp_path):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    reg.add("https://x/manifest.json", Manifest.from_dict({
        "id": "x", "name": "X", "resources": ["catalog"], "types": [],
        "catalogs": [{"type": t, "id": "c"} for t in ("tv", "series", "anime", "movie")],
    }))
    assert reg.home_types() == ["movie", "series", "anime", "tv"]


def test_front_page_prefs(tmp_path):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    reg.add("https://x/manifest.json", Manifest.from_dict({
        "id": "x", "name": "X", "resources": ["catalog"], "types": ["movie"], "catalogs": [
            {"type": "movie", "id": "pinned", "showInHome": True},
            {"type": "movie", "id": "hidden", "showInHome": False},
            {"type": "movie", "id": "unsaid"},
        ]}))
    assert [c.id for _, c in reg.front_catalogs()] == ["pinned", "unsaid"]
    reg.set_catalog_pref("https://x/manifest.json", "movie/hidden", front=True)
    reg.set_catalog_pref("https://x/manifest.json", "movie/unsaid", front=False)
    reg = AddonRegistry(reg.path)
    assert [c.id for _, c in reg.front_catalogs()] == ["pinned", "hidden"]
    # Browse menus are independent of the front page.
    assert [c.id for _, c in reg.home_catalogs()] == ["pinned", "hidden", "unsaid"]


def test_search_catalogs_include_all_type(tmp_path):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    reg.add("https://x/manifest.json", Manifest.from_dict({
        "id": "x", "name": "X", "resources": ["catalog"], "types": ["movie"], "catalogs": [
            {"type": "all", "id": "s", "extra": [{"name": "search", "isRequired": True}]},
            {"type": "series", "id": "t", "extra": [{"name": "search", "isRequired": True}]},
        ]}))
    assert [c.id for _, c in reg.search_catalogs("movie")] == ["s"]
    assert [c.id for _, c in reg.search_catalogs("series")] == ["s", "t"]


def test_hubs_order_names_and_pins(tmp_path):
    from stremio.registry import default_hub, tidy_name

    assert [default_hub(t) for t in ("movie", "series", "anime.movie", "anime", "Trakt", "all")] == [
        "movies", "tvshows", "anime", "anime", "more", "more"]
    assert tidy_name("Netflix 🍿") == "Netflix" and tidy_name("🎭 Kids Shuffle 📺") == "Kids Shuffle"
    assert tidy_name("📺") == "📺"                                     # never empty

    reg = AddonRegistry(str(tmp_path / "a.json"))
    a = reg.add("https://x/manifest.json", Manifest.from_dict({
        "id": "x", "name": "X", "resources": ["catalog"], "types": [], "catalogs": [
            {"type": "movie", "id": "m1", "name": "Netflix 🍿"}, {"type": "movie", "id": "m2", "name": "Trending"},
            {"type": "series", "id": "s1", "name": "Popular"}, {"type": "collection", "id": "c1", "name": "Sets"},
            {"type": "movie", "id": "srch", "name": "Search", "extra": [{"name": "search", "isRequired": True}]},
        ]}))
    keys = lambda hub: [c.id for _, c in reg.hub_catalogs(hub)]
    assert (keys("movies"), keys("tvshows"), keys("more")) == (["m1", "m2"], ["s1"], ["c1"])  # search-only excluded
    assert reg.hubs_in_use() == ["movies", "tvshows", "more"] and reg.pinned_catalogs() == []

    m1 = next(c for c in a.manifest.catalogs if c.id == "m1")
    assert a.display_name(m1) == "Netflix 🍿" and a.display_name(m1, tidy=True) == "Netflix"
    reg.move_in_hub(f"{a.key}|movie/m2", -1)                              # reorder within Movies
    reg.set_catalog_pref(a.key, "series/s1", hub="more")                  # move to another hub
    reg.set_catalog_pref(a.key, "movie/m1", name="My Netflix", front=True)
    reg.set_catalog_pref(a.key, "collection/c1", home=False)              # hide
    reg = AddonRegistry(reg.path)                                         # all persisted
    a = reg.get(a.key)
    assert keys("movies") == ["m2", "m1"] and keys("more") == ["s1"] and keys("tvshows") == []
    assert [c.id for _, c, shown in reg.hub_entries("more")] == ["s1", "c1"]   # hidden still listed for organising
    assert [c.id for _, c in reg.pinned_catalogs()] == ["m1"]
    assert a.display_name(next(c for c in a.manifest.catalogs if c.id == "m1"), tidy=True) == "My Netflix"
    reg.set_catalog_pref(a.key, "series/s1", hub="")                      # back to its default hub
    assert keys("tvshows") == ["s1"]


def test_custom_hubs(tmp_path):
    import pytest

    reg = AddonRegistry(str(tmp_path / "a.json"))
    a = reg.add("https://x/manifest.json", Manifest.from_dict({
        "id": "x", "name": "X", "resources": ["catalog"], "types": [], "catalogs": [
            {"type": "movie", "id": "m1", "name": "Kids Films"}, {"type": "series", "id": "s1", "name": "Cartoons"},
            {"type": "movie", "id": "m2", "name": "Trending"}]}))
    keys = lambda hub: [c.id for _, c in reg.hub_catalogs(hub)]

    kids = reg.create_hub("  Kids ")
    assert kids == "c1" and reg.hubs() == ["movies", "tvshows", "anime", "more", "c1"]
    assert reg.hub_name(kids) == "Kids" and reg.is_custom_hub(kids) and not reg.is_custom_hub("movies")
    assert kids not in reg.hubs_in_use()                                  # empty until something moves in
    reg.set_catalog_pref(a.key, "movie/m1", hub=kids)
    reg.set_catalog_pref(a.key, "series/s1", hub=kids)
    reg.set_catalog_pref(a.key, "movie/m2", hub="nope")                   # unknown hub: its usual one
    reg.rename_hub(kids, "Family")
    reg.rename_hub("movies", "Films")
    with pytest.raises(ValueError):
        reg.rename_hub(kids, "  ")
    with pytest.raises(ValueError):
        reg.remove_hub("movies")                                          # built-ins vanish when empty instead

    reg = AddonRegistry(reg.path)                                         # all persisted
    assert keys(kids) == ["m1", "s1"] and keys("movies") == ["m2"] and keys("tvshows") == []
    assert reg.hubs_in_use() == ["movies", kids]
    assert (reg.hub_name(kids), reg.hub_name("movies"), reg.hub_name("tvshows")) == ("Family", "Films", "")
    reg.rename_hub("movies", "")                                          # back to its own name
    assert reg.hub_name("movies") == ""

    assert reg.create_hub("Docs") == "c2"
    reg.remove_hub(kids)                                                  # catalogs go back home
    assert reg.hubs() == ["movies", "tvshows", "anime", "more", "c2"]
    assert keys("movies") == ["m1", "m2"] and keys("tvshows") == ["s1"]
    assert reg.create_hub("Again") == "c1"                                # a fresh, empty hub
    assert keys("c1") == []
