from conftest import CINEMETA_LIKE
from stremio.client import StremioClient
from stremio.models import Manifest
from stremio.refresh import DAY, refresh_manifests
from stremio.registry import AddonRegistry


def test_fetched_at_persists_and_stale(tmp_path):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    addon = reg.add("https://x/manifest.json", Manifest.from_dict(CINEMETA_LIKE))
    assert addon.fetched_at > 0
    now = addon.fetched_at
    assert reg.stale_addons(DAY, now=now + DAY - 1) == []
    assert [a.key for a in AddonRegistry(reg.path).stale_addons(DAY, now=now + DAY + 1)] == [addon.key]


def test_entries_without_timestamp_are_stale(tmp_path):
    path = tmp_path / "a.json"
    path.write_text('{"addons": [{"transportUrl": "https://x/manifest.json", "manifest": %s}]}'
                    % __import__("json").dumps(CINEMETA_LIKE))
    assert len(AddonRegistry(str(path)).stale_addons(DAY)) == 1


def test_refresh_updates_reachable_and_keeps_others(tmp_path, server):
    reg = AddonRegistry(str(tmp_path / "a.json"))
    up = reg.add(server.url + "/up/manifest.json", Manifest.from_dict(CINEMETA_LIKE))
    down = reg.add(server.url + "/down/manifest.json", Manifest.from_dict(CINEMETA_LIKE))
    reg.set_catalog_pref(up.key, "movie/top", home=False)
    server.routes["/up/manifest.json"] = {**CINEMETA_LIKE, "version": "9.0.0",
                                          "catalogs": CINEMETA_LIKE["catalogs"][:1]}

    refreshed, errors = refresh_manifests(StremioClient(), reg, reg.all(), now=12345.0)
    assert [a.key for a in refreshed] == [up.key] and len(errors) == 1

    reg = AddonRegistry(reg.path)
    assert reg.get(up.key).manifest.version == "9.0.0" and reg.get(up.key).fetched_at == 12345.0
    assert [c.key for c in reg.get(up.key).manifest.catalogs] == ["movie/top"]
    assert reg.get(up.key).catalog_prefs["movie/top"].home is False       # user prefs survive
    assert reg.get(down.key).manifest.version == "1.2.0" and reg.get(down.key).fetched_at != 12345.0
