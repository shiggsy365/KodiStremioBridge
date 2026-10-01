"""Smoke tests for the Kodi layer, run against Kodistubs (no real Kodi needed)."""

import os
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

pytest.importorskip("xbmc")

import xbmc  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402

from conftest import CINEMETA_LIKE  # noqa: E402
from kodi_ui import common, router  # noqa: E402

BASE = "plugin://plugin.video.stremiobridge/"


SETTINGS = {"cinemeta_fallback": False, "show_specials": True, "hide_unaired": False,
            "request_timeout": 5, "cache_catalog_minutes": 0, "cache_meta_hours": 0,
            "autoplay": False, "sort_mode": 0, "max_resolution": 0, "hide_cam": True,
            "exclude_keywords": "", "torrent_mode": 0, "subtitles_enabled": False,
            "subtitle_languages": "eng", "auto_refresh_manifests": False,
            "watched_percent": 90, "min_resume_seconds": 120, "show_continue": True, "show_next_up": True,
            "upnext_enabled": True, "same_source_next": True, "mdblist_enabled": False, "mdblist_api_key": "",
            "mdblist_scrobble": True, "mdblist_sync_hours": 6, "library_watchlist": False,
            "view_movies": "", "view_tvshows": "", "view_seasons": "", "view_episodes": "", "show_watchlist": True,
            "genre_filter": False, "select_opens_info": False, "hide_watched": False, "show_widgets_folder": False,
            "continue_with_next_up": False, "prewarm": False, "tidy_names": True}


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "profile_dir", lambda: str(tmp_path))


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    values = dict(SETTINGS)
    monkeypatch.setattr(common.ADDON, "getSettingBool", lambda key: bool(values[key]))
    monkeypatch.setattr(common.ADDON, "getSettingInt", lambda key: int(values[key]))
    monkeypatch.setattr(common.ADDON, "getSettingString", lambda key: str(values[key]))
    return values


@pytest.fixture
def listing(monkeypatch):
    """Records directory items as ``(params, is_folder)`` and endOfDirectory results."""
    items, ends = [], []

    def add(handle, url, item, isFolder=False, totalItems=0):
        items.append((dict(parse_qsl(urlsplit(url).query)), isFolder))
        return True

    monkeypatch.setattr(xbmcplugin, "addDirectoryItem", add)
    monkeypatch.setattr(xbmcplugin, "addDirectoryItems",
                        lambda handle, entries, total=0: [add(handle, *e) for e in entries])
    monkeypatch.setattr(xbmcplugin, "endOfDirectory",
                        lambda handle, succeeded=True, **kw: ends.append(succeeded))
    return items, ends


def call(action, handle=1, **params):
    router.run([BASE, str(handle), "?" + urlencode({"action": action, **params})])


def install(server):
    server.routes["/manifest.json"] = CINEMETA_LIKE
    call("add_addon", handle=-1, url=server.url)
    return common.get_registry().all()[0]


def test_plugin_parses_argv():
    plugin = router.Plugin([BASE, "7", "?action=manage&url=https%3A%2F%2Fa.com%2Fmanifest.json"])
    assert plugin.handle == 7
    assert plugin.params == {"action": "manage", "url": "https://a.com/manifest.json"}
    assert plugin.url_for("move_addon", addon="k", delta=-1) == BASE + "?action=move_addon&addon=k&delta=-1"


def test_unknown_action_is_ignored():
    router.run([BASE, "1", "?action=does_not_exist"])


def test_add_and_manage(server, monkeypatch, listing):
    addon = install(server)
    assert addon.name == "Example Meta"

    # Adding it twice shows an error dialog instead of raising.
    shown = []
    monkeypatch.setattr(xbmcgui.Dialog, "ok", lambda self, heading, msg: shown.append(msg))
    call("add_addon", handle=-1, url=server.url)
    assert shown and len(common.get_registry().all()) == 1

    call("manage")
    call("addon_catalogs", addon=addon.key)
    call("addon_details", handle=-1, addon=addon.key)
    call("set_catalog_pref", handle=-1, addon=addon.key, catalog="movie/top", field="home", value=0)
    assert common.get_registry().get(addon.key).catalog_prefs["movie/top"].home is False
    # Manage URLs carry the short key, never the transport URL.
    items, _ = listing
    assert all("url" not in params for params, _ in items)


def test_root_lists_types(server, listing):
    items, _ = listing
    call("root")
    assert [p["action"] for p, _ in items] == ["manage"]  # the "no addons yet" hint

    install(server)
    items.clear()
    call("root")
    # Hubs that have catalogs (nothing pinned yet), then Search; Manage addons is in settings.
    assert [(p["action"], p.get("hub")) for p, _ in items] == [
        ("hub", "movies"), ("hub", "tvshows"), ("search_menu", None)]


def test_type_menu(server, listing):
    addon = install(server)
    items, _ = listing
    call("type", type="movie")
    # "Search movies…" first; the search-only catalog is not browsable.
    assert items == [({"action": "new_search", "type": "movie"}, False),
                     ({"action": "catalog", "addon": addon.key, "type": "movie", "id": "top"}, True)]


def test_catalog_listing_with_filters_and_paging(server, listing):
    addon = install(server)
    items, ends = listing
    server.routes["/catalog/movie/top.json"] = {"metas": [
        {"id": f"tt{i}", "type": "movie", "name": f"Movie {i}", "imdbRating": "7.1",
         "releaseInfo": "2020", "genres": ["Drama"], "runtime": "90 min", "background": "http://b"}
        for i in range(20)]}

    call("catalog", addon=addon.key, type="movie", id="top")
    assert ends == [True]
    *movies, next_page = items
    assert len(movies) == 20 and movies[0] == ({"action": "play", "type": "movie", "id": "tt0"}, False)
    assert next_page[0] == {"action": "catalog", "addon": addon.key, "type": "movie", "id": "top",
                            "skip": "20", "ps": "20"}

    # An explicit filter in the URL (e.g. from a widget) is still honoured.
    items.clear()
    server.routes["/catalog/movie/top/genre=Sci-Fi&skip=20.json"] = {"metas": [
        {"id": "tt99", "type": "series", "name": "A show"}]}
    call("catalog", addon=addon.key, type="movie", id="top", f_genre="Sci-Fi", skip=20, ps=20)
    assert items == [({"action": "meta", "type": "series", "id": "tt99"}, True)]


def test_required_filter_defaults_to_first_option(server, listing):
    server.routes["/manifest.json"] = {**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "year", "name": "By year",
         "extra": [{"name": "genre", "isRequired": True, "options": ["2026", "2025"]}]}]}
    call("add_addon", handle=-1, url=server.url)
    addon = common.get_registry().all()[0]
    server.routes["/catalog/movie/year/genre=2026.json"] = {"metas": [{"id": "tt1", "type": "movie", "name": "A"}]}
    items, _ = listing

    call("catalog", addon=addon.key, type="movie", id="year")
    # No picker: the first option is used and the items are listed straight away.
    assert items == [({"action": "play", "type": "movie", "id": "tt1"}, False)]
    assert server.requests[-1] == "/catalog/movie/year/genre=2026.json"


def test_all_type_catalog_filtered_by_only(server, listing):
    server.routes["/manifest.json"] = {**CINEMETA_LIKE, "catalogs": [
        {"type": "all", "id": "mix", "name": "Mixed", "extra": [{"name": "search"}]}]}
    call("add_addon", handle=-1, url=server.url)
    addon = common.get_registry().all()[0]
    server.routes["/catalog/all/mix/search=x.json"] = {"metas": [
        {"id": "tt1", "type": "movie", "name": "M"}, {"id": "tt2", "type": "series", "name": "S"}]}
    items, _ = listing
    call("catalog", addon=addon.key, type="all", id="mix", f_search="x", only="series")
    assert items == [({"action": "meta", "type": "series", "id": "tt2"}, True)]


def test_catalog_errors_fail_gracefully(server, listing):
    addon = install(server)
    _, ends = listing
    call("catalog", addon=addon.key, type="movie", id="top")       # 404 from addon
    call("catalog", addon=addon.key, type="movie", id="missing")   # unknown catalog
    call("catalog", addon="nope", type="movie", id="top")          # unknown addon
    assert ends == [False, False, False]


SHOW = {"meta": {
    "id": "tt5", "type": "series", "name": "Show", "poster": "http://p",
    "videos": [
        {"id": "tt5:1:1", "season": 1, "episode": 1, "title": "One"},
        {"id": "tt5:1:2", "season": 1, "episode": 2, "title": "Two", "released": "2999-01-01T00:00:00.000Z"},
        {"id": "tt5:2:1", "season": 2, "episode": 1, "title": "Three"},
        {"id": "tt5:0:1", "season": 0, "episode": 1, "title": "Special"},
    ],
}}


def test_series_seasons_and_episodes(server, listing, settings):
    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    items, ends = listing

    call("meta", type="series", id="tt5")
    assert [(p["action"], p["season"]) for p, _ in items] == [("season", "1"), ("season", "2"), ("season", "0")]

    items.clear()
    call("season", type="series", id="tt5", season=1)
    assert items == [
        ({"action": "play", "type": "series", "id": "tt5:1:1", "meta": "tt5"}, False),
        ({"action": "play", "type": "series", "id": "tt5:1:2", "meta": "tt5"}, False),
    ]

    items.clear()
    settings.update(show_specials=False, hide_unaired=True)
    call("meta", type="series", id="tt5")
    assert [p["season"] for p, _ in items] == ["1", "2"]
    items.clear()
    call("season", type="series", id="tt5", season=1)
    assert [p["id"] for p, _ in items] == ["tt5:1:1"]
    assert all(ends)


def test_single_season_skips_season_level(server, listing):
    install(server)
    server.routes["/meta/series/tt6.json"] = {"meta": {
        "id": "tt6", "type": "series", "name": "Mini",
        "videos": [{"id": "tt6:1:1", "season": 1, "episode": 1}, {"id": "tt6:1:2", "season": 1, "episode": 2}]}}
    items, _ = listing
    call("meta", type="series", id="tt6")
    assert [(p["action"], p["id"]) for p, _ in items] == [("play", "tt6:1:1"), ("play", "tt6:1:2")]


def test_meta_without_videos_is_one_playable_item(server, listing):
    install(server)
    server.routes["/meta/series/tt7.json"] = {"meta": {
        "id": "tt7", "type": "series", "name": "Odd", "behaviorHints": {"defaultVideoId": "tt7:1:1"}}}
    items, _ = listing
    call("meta", type="series", id="tt7")
    assert items == [({"action": "play", "type": "series", "id": "tt7:1:1", "meta": "tt7"}, False)]


def test_meta_not_found(server, listing):
    install(server)
    _, ends = listing
    call("meta", type="series", id="tt404")
    assert ends == [False]


def test_people_route_searches_by_name(server, listing):
    install(server)
    server.routes["/meta/movie/tt1.json"] = {"meta": {
        "id": "tt1", "type": "movie", "name": "M", "director": "Frank Darabont", "writer": "Frank Darabont",
        "app_extras": {"cast": [{"name": "Tim Robbins", "character": "Andy", "photo": "http://p/t.jpg"}]}}}
    items, _ = listing
    call("people", type="movie", id="tt1")
    assert items == [({"action": "search_window", "query": "Frank Darabont", "person": "1"}, False),
                     ({"action": "search_window", "query": "Tim Robbins", "person": "1"}, False)]


def test_play_trailer(server, monkeypatch):
    from kodi_ui import details

    install(server)
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1", "type": "movie", "name": "M",
                                                      "trailers": [{"source": "abc", "type": "Trailer"}]}}
    played, notes, has_youtube = [], [], [True]
    monkeypatch.setattr(details.xbmc, "executebuiltin", lambda cmd, *a: played.append(cmd))
    monkeypatch.setattr(details.xbmc, "getCondVisibility", lambda cond: has_youtube[0])
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda self, h, msg, *a: notes.append(msg))

    call("play_trailer", handle=-1, type="movie", id="tt1", yt="known")        # id from the listing
    call("play_trailer", handle=-1, type="movie", id="tt1")                    # looked up from meta
    assert played == ["PlayMedia(plugin://plugin.video.youtube/play/?video_id=known)",
                      "PlayMedia(plugin://plugin.video.youtube/play/?video_id=abc)"]
    has_youtube[0] = False
    call("play_trailer", handle=-1, type="movie", id="tt1")
    server.routes["/meta/movie/tt2.json"] = {"meta": {"id": "tt2", "type": "movie", "name": "No trailer"}}
    call("play_trailer", handle=-1, type="movie", id="tt2")
    assert len(played) == 2 and len(notes) == 2


def test_context_menus(server, listing, settings, monkeypatch):
    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    server.routes["/catalog/movie/top.json"] = {"metas": [
        {"id": "tt1", "type": "movie", "name": "M", "trailers": [{"source": "yt1", "type": "Trailer"}]}]}
    menus = []
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems", lambda self, items: menus.append(items))

    call("catalog", addon=common.get_registry().all()[0].key, type="movie", id="top")
    commands = [cmd for _, cmd in menus[-1]]
    assert "action=set_watched" in commands[0] and "action=extended_info&type=movie&id=tt1" in commands[1]
    assert commands[2].endswith("action=library_add&type=movie&id=tt1)")
    assert commands[3].endswith("action=play_trailer&type=movie&id=tt1&yt=yt1)")

    state = common.get_watchstate()
    state.set_watched([PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2)], True)
    menus.clear()
    call("next_up")                                         # an episode outside its show: "Browse show"
    browse = [cmd for _, cmd in menus[0] if "action=meta" in cmd]
    assert browse == [f"ActivateWindow(Videos,{BASE}?action=meta&type=series&id=tt5,return)"]
    info = [cmd for _, cmd in menus[0] if "action=extended_info" in cmd]
    assert info == [f"RunPlugin({BASE}?action=extended_info&type=series&id=tt5)"]  # the show's page


def test_search_falls_back_to_cinemeta(server, listing, settings, monkeypatch):
    from kodi_ui import search as search_module
    from stremio.meta import cinemeta_search_targets
    from stremio.registry import InstalledAddon

    server.routes["/manifest.json"] = SEARCHABLE
    call("add_addon", handle=-1, url=server.url)          # its search catalogs return 404/nothing
    monkeypatch.setattr(xbmcgui.DialogProgress, "iscanceled", lambda self: False)
    fake_cinemeta = [(InstalledAddon(server.url + "/cm/manifest.json", addon.manifest), catalog)
                     for addon, catalog in cinemeta_search_targets()]
    monkeypatch.setattr(search_module, "cinemeta_search_targets",
                        lambda type_=None: [t for t in fake_cinemeta if type_ is None or t[1].type == type_])
    server.routes["/cm/catalog/movie/top/search=frank%20darabont.json"] = metas("tt0111161")
    server.routes["/cm/catalog/series/top/search=frank%20darabont.json"] = {"metas": [
        {"id": "tt1520211", "type": "series", "name": "The Walking Dead"}]}
    items, ends = listing

    settings["cinemeta_fallback"] = False
    call("search", query="frank darabont")
    assert ends == [False]
    settings["cinemeta_fallback"] = True
    call("search", query="frank darabont")
    assert [(p["action"], p["id"]) for p, _ in items] == [("play", "tt0111161"), ("meta", "tt1520211")]


def test_meta_is_cached(server, listing, settings):
    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    settings.update(cache_meta_hours=24)
    call("meta", type="series", id="tt5")
    call("season", type="series", id="tt5", season=2)
    assert server.requests.count("/meta/series/tt5.json") == 1

    call("clear_cache", handle=-1)
    call("season", type="series", id="tt5", season=2)
    assert server.requests.count("/meta/series/tt5.json") == 2


# ------------------------------------------------------------------ playback

from conftest import STREAM_ADDON  # noqa: E402
from kodi_ui import player  # noqa: E402

UA = {"behaviorHints": {"proxyHeaders": {"request": {"User-Agent": "UA 1"}}}}


@pytest.fixture
def playback(server, monkeypatch, tmp_path):
    """Installs a meta addon and a stream addon; records what gets resolved."""
    install(server)
    server.routes["/s/manifest.json"] = STREAM_ADDON
    call("add_addon", handle=-1, url=server.url + "/s")

    state = {"resolved": [], "picker": [], "choice": 0, "installed": {"inputstream.adaptive", "plugin.video.elementum"},
             "notes": [], "broken": set(), "probed": [], "progress": [], "cancel_after": None}
    monkeypatch.setattr(player, "probe", lambda session, stream: (
        state["probed"].append(stream.url) or ((False, "broken") if stream.url in state["broken"] else (True, ""))))

    def record(name):
        def setter(self, value, *rest):
            if rest:  # setProperty(key, value)
                self.__dict__.setdefault("props", {})[value] = rest[0]
            else:
                self.__dict__[name] = value
        return setter

    for method in ("setPath", "setSubtitles", "setMimeType", "setContentLookup"):
        monkeypatch.setattr(xbmcgui.ListItem, method, record(method[3:].lower()))
    monkeypatch.setattr(xbmcgui.ListItem, "setProperty", record("props"))
    monkeypatch.setattr(xbmcplugin, "setResolvedUrl",
                        lambda handle, ok, item: state["resolved"].append((ok, item)))
    monkeypatch.setattr(xbmcgui.DialogProgress, "iscanceled",
                        lambda self: state["cancel_after"] is not None and len(state["probed"]) >= state["cancel_after"])
    monkeypatch.setattr(xbmcgui.DialogProgress, "update",
                        lambda self, percent, message="": state["progress"].append(message))

    def select(self, heading, items, useDetails=False, **kw):
        state["picker"].append(items)
        return state["choice"]

    monkeypatch.setattr(xbmcgui.Dialog, "select", select)
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda self, h, msg, *a: state["notes"].append(msg))
    monkeypatch.setattr(player.xbmc, "getCondVisibility",
                        lambda cond: cond[len("System.HasAddon("):-1] in state["installed"])
    monkeypatch.setattr(player.xbmcvfs, "translatePath", lambda path: str(tmp_path / "kodi-temp"))
    return state


def streams(*entries):
    return {"streams": list(entries)}


def resolved_path(state):
    (ok, item), = state["resolved"]
    return ok, item.__dict__.get("path"), item


def test_play_picker(server, playback):
    server.routes["/s/stream/movie/tt1.json"] = streams(
        {"url": "https://cdn/720.mkv", "name": "720p"},
        {"url": "https://cdn/1080.mkv", "name": "1080p", **UA},
    )
    playback["choice"] = 1  # second entry after sorting = the 720p one
    call("play", type="movie", id="tt1")
    assert len(playback["picker"][0]) == 2
    ok, path, _ = resolved_path(playback)
    assert ok and path == "https://cdn/720.mkv"


def test_play_autoplay_uses_best_and_headers(server, playback, settings):
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams(
        {"url": "https://cdn/720.mkv", "name": "720p"},
        {"url": "https://cdn/1080.mkv", "name": "1080p", **UA},
    )
    call("play", type="movie", id="tt1")
    assert playback["picker"] == []
    assert resolved_path(playback)[:2] == (True, "https://cdn/1080.mkv|User-Agent=UA%201")


def test_play_failures_resolve_false(server, playback, settings):
    call("play", type="channel", id="yt:1")                       # no stream addon for this
    server.routes["/s/stream/movie/tt2.json"] = streams({"url": "https://x", "name": "HDCAM"})
    call("play", type="movie", id="tt2")                          # everything filtered
    call("play", type="movie", id="tt3")                          # addon 404s: nothing found
    server.routes["/s/stream/movie/tt4.json"] = streams({"url": "https://x"})
    playback["choice"] = -1
    call("play", type="movie", id="tt4")                          # picker cancelled
    assert [ok for ok, _ in playback["resolved"]] == [False] * 4
    assert len(playback["notes"]) == 3


def test_play_hls_with_and_without_inputstream(server, playback, settings):
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams({"url": "https://cdn/master.m3u8", **UA})
    call("play", type="movie", id="tt1")
    ok, path, item = resolved_path(playback)
    assert path == "https://cdn/master.m3u8"
    assert item.props["inputstream"] == "inputstream.adaptive"
    assert item.props["inputstream.adaptive.stream_headers"] == "User-Agent=UA%201"
    assert item.mimetype == "application/vnd.apple.mpegurl"

    playback["resolved"].clear()
    playback["installed"].clear()
    call("play", type="movie", id="tt1")
    ok, path, item = resolved_path(playback)
    assert path == "https://cdn/master.m3u8|User-Agent=UA%201"
    assert "inputstream" not in item.__dict__.get("props", {})


def test_play_torrents(server, playback, settings):
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams({"infoHash": "abc", "name": "1080p"})
    call("play", type="movie", id="tt1")
    assert resolved_path(playback)[0] is False  # hidden by default

    playback["resolved"].clear()
    settings["torrent_mode"] = 1
    call("play", type="movie", id="tt1")
    assert resolved_path(playback)[1].startswith("plugin://plugin.video.elementum/play?uri=magnet")

    playback["resolved"].clear()
    playback["installed"].discard("plugin.video.elementum")
    call("play", type="movie", id="tt1")
    assert resolved_path(playback)[0] is False  # Elementum chosen but not installed


def test_play_subtitles(server, playback, settings):
    settings.update(autoplay=True, subtitles_enabled=True, subtitle_languages="eng")
    server.routes["/subs/manifest.json"] = {**STREAM_ADDON, "id": "subs", "name": "Subs",
                                            "resources": ["subtitles"], "catalogs": []}
    call("add_addon", handle=-1, url=server.url + "/subs")
    server.routes["/s/stream/movie/tt1.json"] = streams({
        "url": "https://cdn/a.mkv", "subtitles": [{"url": server.url + "/f/embedded.srt", "lang": "eng"}],
        "behaviorHints": {"filename": "a.mkv"}})
    server.routes["/subs/subtitles/movie/tt1/filename=a.mkv.json"] = {"subtitles": [
        {"url": server.url + "/f/addon.srt", "lang": "eng"}, {"url": server.url + "/f/fr.srt", "lang": "fre"}]}
    for name in ("embedded.srt", "addon.srt", "fr.srt"):
        server.routes["/f/" + name] = b"1\n"

    call("play", type="movie", id="tt1")
    _, _, item = resolved_path(playback)
    assert [os.path.basename(p) for p in item.subtitles] == ["00.eng.srt", "01.eng.srt"]


def test_play_episode_uses_show_meta(server, playback, settings):
    settings["autoplay"] = True
    server.routes["/meta/series/tt5.json"] = SHOW
    server.routes["/s/stream/series/tt5%3A1%3A1.json"] = streams({"url": "https://cdn/e.mkv"})
    call("play", type="series", id="tt5:1:1", meta="tt5")
    assert resolved_path(playback)[:2] == (True, "https://cdn/e.mkv")
    assert "/meta/series/tt5.json" in server.requests


# ------------------------------------------------------------------ search

SEARCHABLE = {**CINEMETA_LIKE, "catalogs": [
    {"type": "movie", "id": "top", "name": "Popular", "extra": [{"name": "search"}, {"name": "skip"},
                                                                 {"name": "genre", "options": ["Action"]}]},
    {"type": "series", "id": "top", "name": "Popular", "extra": [{"name": "search"}]},
    {"type": "movie", "id": "odd", "name": "Needs genre",
     "extra": [{"name": "search"}, {"name": "genre", "isRequired": True, "options": ["A"]}]},
]}


@pytest.fixture
def searchable(server, monkeypatch):
    server.routes["/manifest.json"] = SEARCHABLE
    call("add_addon", handle=-1, url=server.url)
    monkeypatch.setattr(xbmcgui.DialogProgress, "iscanceled", lambda self: False)
    notes = []
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda self, h, msg, *a: notes.append(msg))
    return common.get_registry().all()[0], notes


def metas(*ids):
    return {"metas": [{"id": i, "type": "movie", "name": i} for i in ids]}


def test_search_groups_results(server, listing, searchable):
    addon, _ = searchable
    server.routes["/catalog/movie/top/search=star%20wars.json"] = metas("tt1", "tt2")
    server.routes["/catalog/series/top/search=star%20wars.json"] = {"metas": [
        {"id": "tt3", "type": "series", "name": "Show"}]}
    items, ends = listing
    call("search", query="star wars")
    assert ends == [True]
    assert items == [
        ({"action": "catalog", "addon": addon.key, "type": "movie", "id": "top", "f_search": "star wars"}, True),
        ({"action": "catalog", "addon": addon.key, "type": "series", "id": "top", "f_search": "star wars"}, True),
    ]
    # The catalog that also needs a genre is never searched.
    assert not any("/odd/" in r for r in server.requests)

    # Opening a group: plain results, no "Genre…" picker, paging still works.
    items.clear()
    call("catalog", addon=addon.key, type="movie", id="top", f_search="star wars")
    assert [p["action"] for p, _ in items] == ["play", "play"]


def test_search_single_group_lists_items_directly(server, listing, searchable):
    server.routes["/catalog/movie/top/search=alien.json"] = metas("tt1")
    server.routes["/catalog/series/top/search=alien.json"] = {"metas": []}
    items, _ = listing
    call("search", query="alien")
    assert items == [({"action": "play", "type": "movie", "id": "tt1"}, False)]

    items.clear()
    call("search", query="alien", type="series")
    assert items == []


def test_search_no_results(server, listing, searchable):
    _, notes = searchable
    _, ends = listing
    call("search", query="nothing")
    assert ends == [False] and len(notes) == 1


def test_search_without_targets(server, listing, monkeypatch):
    _, ends = listing
    call("search", query="x")
    assert ends == [False]


def test_new_search_and_history(server, listing, searchable, monkeypatch):
    from kodi_ui import searchwindow

    opened = []
    monkeypatch.setattr(searchwindow, "search_window", lambda plugin, query, type=None: opened.append((query, type)))
    queries = iter(["  Alien  ", "Blade Runner", "alien", ""])
    monkeypatch.setattr(xbmcgui.Dialog, "input", lambda self, *a, **kw: next(queries))

    call("new_search", handle=-1, type="movie")
    call("new_search", handle=-1)
    call("new_search", handle=-1)
    call("new_search", handle=-1)  # empty input: nothing happens
    assert opened == [("Alien", "movie"), ("Blade Runner", None), ("alien", None)]
    assert common.get_history().all() == ["alien", "Blade Runner"]

    items, _ = listing
    call("search_menu")
    assert [(p["action"], p.get("query"), folder) for p, folder in items] == [
        ("new_search", None, False), ("search_catalogs", None, True),
        ("search_window", "alien", False), ("search_window", "Blade Runner", False)]

    call("search_history_remove", handle=-1, query="alien")
    assert common.get_history().all() == ["Blade Runner"]
    call("search_history_clear", handle=-1)
    assert common.get_history().all() == []


# ------------------------------------------------------------------ rotating catalogs

def byw(*seeds):
    return {**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": f"seed_{n}", "name": f"Because you watched {n}", "extra": [{"name": "skip"}]}
        for n in seeds]}


def test_rotating_catalog_follows_its_slot(server, listing):
    server.routes["/manifest.json"] = byw(1, 2)
    call("add_addon", handle=-1, url=server.url)
    addon = common.get_registry().all()[0]
    items, ends = listing

    call("type", type="movie")
    widget = items[-1][0]                                  # a widget saved for seed_2
    assert (widget["id"], widget["slot"], widget["of"]) == ("seed_2", "1", "2")

    # BingeCat rotates seed_2 -> seed_3; the old id now answers with nothing.
    server.routes["/manifest.json"] = byw(1, 3)
    server.routes["/catalog/movie/seed_2.json"] = {"metas": []}
    server.routes["/catalog/movie/seed_3.json"] = {"metas": [{"id": "tt3", "type": "movie", "name": "New"}]}
    common.get_registry().update_manifest(addon.key, addon.manifest, fetched_at=0)   # last fetched long ago

    items.clear()
    call("catalog", **{k: v for k, v in widget.items() if k != "action"})
    assert items == [({"action": "play", "type": "movie", "id": "tt3"}, False)] and ends[-1] is True

    # Once the manifest is current, the old link resolves via its slot without refetching it.
    fetches = server.requests.count("/manifest.json")
    items.clear()
    call("catalog", **{k: v for k, v in widget.items() if k != "action"})
    assert items[0][0]["id"] == "tt3" and server.requests.count("/manifest.json") == fetches


def test_removed_numbered_catalog_is_not_replaced(server, listing):
    server.routes["/manifest.json"] = byw(1, 2)
    call("add_addon", handle=-1, url=server.url)
    addon = common.get_registry().all()[0]
    server.routes["/manifest.json"] = byw(1)               # seed_2 removed, family shrank
    server.routes["/catalog/movie/seed_2.json"] = {"metas": []}
    server.routes["/catalog/movie/seed_1.json"] = {"metas": [{"id": "tt1", "type": "movie", "name": "Other"}]}
    common.get_registry().update_manifest(addon.key, addon.manifest, fetched_at=0)
    items, ends = listing
    call("catalog", addon=addon.key, type="movie", id="seed_2", slot=1, of=2)
    # The manifest was refreshed, but slot 1 of a family that shrank isn't substituted.
    assert server.requests.count("/manifest.json") == 2
    assert items == [] and "/catalog/movie/seed_1.json" not in server.requests

    call("catalog", addon=addon.key, type="movie", id="seed_2", slot=1, of=2)
    assert ends[-1] is False                               # now missing from the manifest: an error


# ------------------------------------------------------------------ watch state (phase 6)

import base64  # noqa: E402
import json as jsonlib  # noqa: E402

from stremio.watchstate import PlaybackEntry  # noqa: E402


@pytest.fixture
def window(monkeypatch):
    """A dict-backed home window, so announcements between play and service work."""
    props = {}

    class FakeWindow:
        def __init__(self, *a):
            pass

        def setProperty(self, k, v):
            props[k] = v

        def getProperty(self, k):
            return props.get(k, "")

        def clearProperty(self, k):
            props.pop(k, None)

    monkeypatch.setattr(xbmcgui, "Window", FakeWindow)
    return props


def test_play_announces_entry_and_resume_offset(server, playback, settings, window):
    settings["autoplay"] = True
    server.routes["/meta/series/tt5.json"] = SHOW
    server.routes["/s/stream/series/tt5%3A1%3A1.json"] = streams(
        {"url": "https://cdn/e.mkv", "behaviorHints": {"bingeGroup": "grp-A"}})
    common.get_watchstate().record(PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5",
                                                 season=1, episode=1), 600, 2400)

    router.run([BASE, "1", "?action=play&type=series&id=tt5%3A1%3A1&meta=tt5", "resume:true"])
    entry, offset = common.take_announced_playback()
    assert (entry.video_id, entry.meta_id, entry.season, entry.episode) == ("tt5:1:1", "tt5", 1, 1)
    assert entry.show_title == "Show" and entry.binge_group == "grp-A" and entry.ids == {"imdb": "tt5"}
    assert offset == 600

    router.run([BASE, "1", "?action=play&type=series&id=tt5%3A1%3A1&meta=tt5", "resume:false"])
    assert common.take_announced_playback()[1] == 0


def test_play_prefers_same_source(server, playback, settings, window):
    server.routes["/s/stream/series/tt5%3A1%3A2.json"] = streams(
        {"url": "https://cdn/best.mkv", "name": "2160p"},
        {"url": "https://cdn/same.mkv", "name": "720p", "behaviorHints": {"bingeGroup": "grp-A"}})
    state = common.get_watchstate()
    state.touch(PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1,
                              binge_group="grp-A"))
    call("play", type="series", id="tt5:1:2", meta="tt5")
    assert playback["picker"] == [] and resolved_path(playback)[1] == "https://cdn/same.mkv"

    # Up Next passes the group explicitly; with the setting off and no group, the picker shows.
    playback["resolved"].clear()
    settings["same_source_next"] = False
    call("play", type="series", id="tt5:1:2", meta="tt5")
    assert len(playback["picker"]) == 1
    playback["resolved"].clear()
    call("play", type="series", id="tt5:1:2", meta="tt5", binge="grp-A")
    assert len(playback["picker"]) == 1                       # no new picker
    assert resolved_path(playback)[1] == "https://cdn/same.mkv"


def test_continue_watching_and_next_up(server, listing, settings):
    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    state = common.get_watchstate()
    movie = PlaybackEntry(video_id="tt1", type="movie", title="Movie")
    state.record(movie, 1200, 6000)
    state.set_watched([PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1)], True)
    items, _ = listing

    call("root")
    assert [p["action"] for p, _ in items][:2] == ["continue", "next_up"]

    items.clear()
    call("continue")
    assert items == [({"action": "play", "type": "movie", "id": "tt1"}, False)]

    # Next episode after 1x01 is 1x02 (unaired in SHOW, so none); after 1x02 comes 2x01.
    items.clear()
    call("next_up")
    assert items == []
    state.set_watched([PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2)], True)
    call("next_up")
    assert items == [({"action": "play", "type": "series", "id": "tt5:2:1", "meta": "tt5"}, False)]

    call("clear_resume", handle=-1, id="tt1")
    assert state.continue_watching() == []


def test_set_watched_levels_and_mdblist_push(server, listing, settings, monkeypatch):
    from kodi_ui import watching

    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1", "type": "movie", "name": "M", "moviedb_id": 550}}
    pushed = []

    class FakeMDBList:
        def add_watched(self, entries, watched_at=None):
            pushed.append(("add", sorted(e.video_id for e in entries), entries[0].ids))

        def remove_watched(self, entries):
            pushed.append(("remove", sorted(e.video_id for e in entries), entries[0].ids))

    monkeypatch.setattr(watching, "get_mdblist", lambda: FakeMDBList())
    state = common.get_watchstate()

    call("set_watched", handle=-1, type="movie", id="tt1", value=1)
    assert state.get("tt1").watched and state.get("tt1").synced
    assert pushed[-1] == ("add", ["tt1"], {"imdb": "tt1", "tmdb": 550})

    call("set_watched", handle=-1, type="series", id="tt5:1:1", meta="tt5", value=1)
    assert state.watched_episodes("tt5") == {(1, 1)}

    call("set_watched", handle=-1, type="series", id="tt5", value=1)          # whole show: aired episodes only
    assert state.watched_episodes("tt5") == {(1, 1), (2, 1)}
    call("set_watched", handle=-1, type="series", id="tt5", season=2, value=0)
    assert state.watched_episodes("tt5") == {(1, 1)}
    assert pushed[-1] == ("remove", ["tt5:2:1"], {"imdb": "tt5"})


def test_listings_carry_watch_state(server, listing, settings, monkeypatch):
    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    state = common.get_watchstate()
    state.set_watched([PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1)], True)
    state.record(PlaybackEntry(video_id="tt5:2:1", type="series", meta_id="tt5", season=2, episode=1), 300, 1200)
    marks = []
    monkeypatch.setattr(xbmc.InfoTagVideo, "setPlaycount", lambda self, n: marks.append(("count", n)))
    monkeypatch.setattr(xbmc.InfoTagVideo, "setResumePoint", lambda self, t, total: marks.append(("resume", t)))
    props = {}
    monkeypatch.setattr(xbmcgui.ListItem, "setProperty", lambda self, k, v: props.__setitem__(k, v))

    call("meta", type="series", id="tt5")                      # seasons: 1, 2, 0
    assert ("count", 0) in marks and props["WatchedEpisodes"] == "0"   # last season rendered: specials
    marks.clear()
    call("season", type="series", id="tt5", season=1)
    assert marks == [("count", 1)]
    marks.clear()
    call("season", type="series", id="tt5", season=2)
    assert marks == [("count", 0), ("resume", 300)]


# ------------------------------------------------------------------ service

class FakeWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((getattr(fn, "__name__", str(fn)), args))


def test_tracker_records_progress_and_resumes(settings, window, monkeypatch):
    from kodi_ui import service

    common.announce_playback(PlaybackEntry(video_id="tt1", type="movie", title="M", ids={"imdb": "tt1"},
                                           binge_group="g"), offset=900)
    worker = FakeWorker()
    tracker = service.Tracker(worker)
    clock = {"pos": 2.0, "now": 1000.0}
    monkeypatch.setattr(service.time, "time", lambda: clock["now"])
    monkeypatch.setattr(tracker, "getTotalTime", lambda: 6000.0)
    monkeypatch.setattr(tracker, "getTime", lambda: clock["pos"])
    monkeypatch.setattr(tracker, "isPlayingVideo", lambda: True)
    seeks = []
    monkeypatch.setattr(tracker, "seekTime", lambda t: seeks.append(t))

    tracker.onAVStarted()
    assert tracker.entry.video_id == "tt1" and common.get_watchstate().get("tt1").binge_group == "g"
    clock["now"] += 2
    tracker.tick()
    assert seeks == [900]                                     # Kodi hadn't resumed: we seek once
    clock["pos"], clock["now"] = 2400.0, clock["now"] + 20
    tracker.tick()                                            # periodic save
    assert common.get_watchstate().get("tt1").position == 2400

    clock["pos"] = 5800.0
    tracker.tick()
    tracker.onPlayBackStopped()
    row = common.get_watchstate().get("tt1")
    assert row.watched and row.position == 0 and tracker.entry is None
    assert worker.jobs[-1] == ("after_playback", ("watched",))


def test_tracker_ignores_other_playback(settings, window):
    from kodi_ui import service

    tracker = service.Tracker(FakeWorker())
    tracker.onAVStarted()                                     # nothing announced
    tracker.onPlayBackStopped()
    assert tracker.entry is None and common.get_watchstate().continue_watching() == []


def test_tracker_scrobbles_when_enabled(settings, window, monkeypatch):
    from kodi_ui import service

    settings.update(mdblist_enabled=True, mdblist_api_key="KEY")
    common.announce_playback(PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1,
                                           ids={"imdb": "tt5"}))
    monkeypatch.setattr(service.xbmc, "getCondVisibility", lambda cond: False)   # no Up Next installed
    worker = FakeWorker()
    tracker = service.Tracker(worker)
    monkeypatch.setattr(tracker, "getTotalTime", lambda: 1000.0)
    tracker.onAVStarted()
    tracker.position = 500.0
    tracker.onPlayBackPaused()
    names = [name for name, _ in worker.jobs]
    assert names == ["scrobble", "scrobble"]
    (event, payload), = [worker.jobs[1][1]]
    assert event == "pause" and payload["progress"] == 50.0 and payload["show"]["ids"] == {"imdb": "tt5"}


def test_upnext_signal(server, settings, monkeypatch):
    from kodi_ui import service

    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    for module in (service, common):   # Kodistubs reports an empty add-on id
        monkeypatch.setattr(module, "ADDON_ID", "plugin.video.stremiobridge")
    sent = []
    monkeypatch.setattr(service.xbmc, "executeJSONRPC", lambda body: sent.append(jsonlib.loads(body)))
    service.signal_upnext(PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2,
                                        binge_group="grp"))
    (request,) = sent
    assert request["params"]["sender"] == "plugin.video.stremiobridge.SIGNAL"
    data = jsonlib.loads(base64.b64decode(request["params"]["data"][0]))
    assert data["current_episode"]["episodeid"] == "tt5:1:2" and data["next_episode"]["episodeid"] == "tt5:2:1"
    assert set(data["next_episode"]) >= {"tvshowid", "title", "art", "season", "episode", "showtitle", "plot",
                                         "playcount", "rating", "firstaired"}
    assert data["play_url"] == f"{BASE}?action=play&type=series&id=tt5%3A2%3A1&meta=tt5&binge=grp"

    sent.clear()                                              # last aired episode: nothing to offer
    service.signal_upnext(PlaybackEntry(video_id="tt5:2:1", type="series", meta_id="tt5", season=2, episode=1))
    assert sent == []


# ------------------------------------------------------------------ extended info dialog

class FakeControl:
    def __init__(self):
        self.label, self.visible, self.items, self.selected = "", True, [], 0

    def setLabel(self, label):
        self.label = label

    def setVisible(self, visible):
        self.visible = visible

    def addItems(self, items):
        self.items = list(items)

    def getSelectedPosition(self):
        return self.selected


MOVIE_META = {"meta": {
    "id": "tt1", "type": "movie", "name": "Unabomber", "releaseInfo": "2026", "runtime": "100 min",
    "imdbRating": "7.1", "genres": ["Crime", "Drama"], "director": "Janus Metz", "writer": "Sam Chalsen, Nelson Greaves",
    "trailers": [{"source": "yt9", "type": "Trailer"}],
    "app_extras": {"certificationLocal": "15",
                   "cast": [{"name": "Annabelle Wallis", "character": "Agent", "photo": "http://p/a.jpg"},
                            {"name": "Jacob Tremblay", "character": "Ted", "photo": ""}]}}}


@pytest.fixture
def info_dialog(monkeypatch):
    """Runs the dialog headless: onInit/onClick against fake controls, then the scripted choice."""
    from kodi_ui import infodialog

    state = {"script": [], "controls": {}, "props": {}, "builtins": []}

    def get_control(self, control_id):
        return state["controls"].setdefault(control_id, FakeList())

    def do_modal(self):
        self.onInit()
        state["dialog"] = self
        for step in state["script"]:
            if isinstance(step, str):                     # an action tile, by name
                control_id, selected = infodialog.ACTIONS, self._actions.index(step)
            else:
                control_id, selected = step
            get_control(self, control_id).selected = selected
            self.onClick(control_id)

    monkeypatch.setattr(infodialog.InfoDialog, "getControl", get_control)
    monkeypatch.setattr(infodialog.InfoDialog, "setProperty", lambda self, k, v: state["props"].__setitem__(k, v))
    monkeypatch.setattr(infodialog.InfoDialog, "setFocusId", lambda self, i: None)
    monkeypatch.setattr(infodialog.InfoDialog, "close", lambda self: None)
    monkeypatch.setattr(infodialog.InfoDialog, "doModal", do_modal)
    monkeypatch.setattr(infodialog, "L", lambda string_id, **kw: f"#{string_id}")   # readable labels
    monkeypatch.setattr(infodialog.xbmc, "executebuiltin",
                        lambda cmd, *a: "busydialog" in cmd or state["builtins"].append(cmd))
    return state


def test_extended_info_renders(server, settings, info_dialog, monkeypatch):
    from kodi_ui import infodialog

    install(server)
    server.routes["/meta/movie/tt1.json"] = MOVIE_META

    class FakeMDBList:
        def item(self, type_, id_):
            return {"score": 72, "ratings": [{"source": "imdb", "value": 7.1}, {"source": "tomatoes", "value": 81}]}

    monkeypatch.setattr(infodialog, "get_mdblist", lambda: FakeMDBList())
    call("extended_info", handle=-1, type="movie", id="tt1")
    controls, props = info_dialog["controls"], info_dialog["props"]
    assert info_dialog["dialog"]._cast_names == ["Annabelle Wallis", "Jacob Tremblay"] and len(controls[50].items) == 2
    dialog = info_dialog["dialog"]
    assert dialog._actions == ["play", "trailer", "director", "watched", "similar", "library"]
    assert [dialog._look(a) for a in dialog._actions] == [
        ("#30221", "play"), ("#30223", "trailer"), ("#30224", "director"), ("#30190", "watched"),
        ("#30226", "similar"), ("#30266", "library_add")]
    assert len(controls[infodialog.ACTIONS].items) == 6
    details = [props[f"detail{i}"] for i in range(1, 9) if props[f"detail{i}"]]
    assert props["detail8"] == ""                             # unused rows are blank
    assert "Janus Metz" in details[0] and "Sam Chalsen / Nelson Greaves" in details[1]
    assert details[3].endswith("Movie") and details[4].endswith(" 75/100")  # no source count  # ratings under Type
    assert props["badges"] == "[B]1h 40m[/B]     [B]IMDb 7.1[/B]     [B]15[/B]"
    assert info_dialog["builtins"][-1] == "Container.Refresh"   # from installing the addon, not the dialog


def test_extended_info_actions(server, settings, info_dialog, monkeypatch):
    from kodi_ui import infodialog

    install(server)
    server.routes["/meta/movie/tt1.json"] = MOVIE_META
    server.routes["/meta/series/tt5.json"] = SHOW
    monkeypatch.setattr(infodialog, "get_mdblist", lambda: None)
    builtins = info_dialog["builtins"]

    from kodi_ui import searchwindow

    searched = []
    monkeypatch.setattr(searchwindow, "search_window",
                        lambda plugin, query, type=None, person=None: searched.append((query, person)))
    info_dialog["script"] = [(50, 1)]  # click the second actor
    call("extended_info", handle=-1, type="movie", id="tt1")
    assert searched == [("Jacob Tremblay", "1")]
    assert "similar" not in info_dialog["dialog"]._actions  # no MDBList: no Similar titles

    info_dialog["script"] = ["director"]  # same director
    call("extended_info", handle=-1, type="movie", id="tt1")
    assert searched == [("Jacob Tremblay", "1"), ("Janus Metz", "1")]

    common.get_watchstate().record(PlaybackEntry(video_id="tt1", type="movie"), 900, 6000)
    monkeypatch.setattr(xbmcgui.Dialog, "contextmenu", lambda self, items: 0)   # "Resume from 15:00"
    info_dialog["script"] = ["play"]
    call("extended_info", handle=-1, type="movie", id="tt1")
    assert builtins[-1] == f"PlayMedia({BASE}?action=play&type=movie&id=tt1&resume=1)"

    info_dialog["script"] = ["play"]                         # a show: Play opens its seasons
    call("extended_info", handle=-1, type="series", id="tt5")
    assert builtins[-1] == f"ActivateWindow(Videos,{BASE}?action=meta&type=series&id=tt5,return)"

    # With autoplay, a Streams tile next to Play shows the stream list; "Play from
    # beginning" is passed on, so the play route doesn't ask again.
    assert "streams" not in info_dialog["dialog"]._actions
    settings["autoplay"] = True
    monkeypatch.setattr(xbmcgui.Dialog, "contextmenu", lambda self, items: 1)   # "Play from beginning"
    info_dialog["script"] = ["streams"]
    call("extended_info", handle=-1, type="movie", id="tt1")
    assert info_dialog["dialog"]._actions[:2] == ["play", "streams"]
    assert builtins[-1] == f"PlayMedia({BASE}?action=play&type=movie&id=tt1&resume=0&pick=1)"
    info_dialog["script"] = ["play"]                         # shows have no Streams tile
    call("extended_info", handle=-1, type="series", id="tt5")
    assert "streams" not in info_dialog["dialog"]._actions


@pytest.fixture
def kodi_ui_state(monkeypatch):
    """Simulated Kodi GUI for refresh_when_idle: busy or idle, and which folder is shown."""
    state = {"idle": True, "folder": "plugin://plugin.video.stremiobridge/?action=catalog", "refreshes": 0}
    monkeypatch.setattr(common, "ADDON_ID", "plugin.video.stremiobridge")
    monkeypatch.setattr(common.xbmc.Monitor, "waitForAbort", lambda self, timeout=0: False)
    monkeypatch.setattr(common.xbmc, "getCondVisibility", lambda cond: state["idle"])
    monkeypatch.setattr(common.xbmc, "getInfoLabel", lambda label: state["folder"])
    monkeypatch.setattr(common, "refresh_container", lambda: state.__setitem__("refreshes", state["refreshes"] + 1))
    return state


def test_refresh_when_idle(kodi_ui_state):
    assert common.refresh_when_idle() is True and kodi_ui_state["refreshes"] == 1
    kodi_ui_state["idle"] = False                              # still loading/playing: give up, don't refresh
    assert common.refresh_when_idle(timeout=2) is False and kodi_ui_state["refreshes"] == 1
    kodi_ui_state.update(idle=True, folder="videodb://movies/")  # user left our add-on meanwhile
    assert common.refresh_when_idle() is False and kodi_ui_state["refreshes"] == 1


def test_after_playback_only_refreshes_on_change(kodi_ui_state, settings, monkeypatch):
    from kodi_ui import service

    synced = []
    monkeypatch.setattr(service, "sync_mdblist", lambda force=False: synced.append(1))
    service.after_playback(None)                               # stopped after a few seconds: nothing to do
    assert kodi_ui_state["refreshes"] == 0
    service.after_playback("resume")
    assert kodi_ui_state["refreshes"] == 1 and synced == []
    service.after_playback("watched")
    assert kodi_ui_state["refreshes"] == 2 and synced == [1]


def test_extended_info_toggles_watched_in_place(server, settings, info_dialog, kodi_ui_state, monkeypatch):
    from kodi_ui import infodialog

    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    server.routes["/meta/movie/tt1.json"] = MOVIE_META
    monkeypatch.setattr(infodialog, "get_mdblist", lambda: None)
    builtins = info_dialog["builtins"]
    builtins.clear()

    info_dialog["script"] = ["watched"]                       # show: mark every aired episode watched
    call("extended_info", handle=-1, type="series", id="tt5")
    assert common.get_watchstate().watched_episodes("tt5") == {(1, 1), (2, 1)}
    assert info_dialog["dialog"].watched is True
    assert info_dialog["dialog"]._look("watched") == ("#30191", "unwatched")
    assert builtins == [] and kodi_ui_state["refreshes"] == 1  # listing refreshed once, no navigation

    # Already watched: the button opens as "Mark as unwatched" and cycles on each press.
    common.get_watchstate().set_watched([PlaybackEntry(video_id="tt1", type="movie")], True)
    labels = []
    info_dialog["script"] = ["watched", "watched", "watched"]
    real_style = infodialog.InfoDialog._style
    monkeypatch.setattr(infodialog.InfoDialog, "_style",
                        lambda self, item, action: (action == "watched" and labels.append(self._look(action)[0]),
                                                    real_style(self, item, action)))
    builtins.clear()
    call("extended_info", handle=-1, type="movie", id="tt1")
    assert labels == ["#30191", "#30190", "#30191", "#30190"]   # initial, then one per press
    assert common.get_watchstate().get("tt1").watched is False
    assert kodi_ui_state["refreshes"] == 2                    # net change: watched -> unwatched

    builtins.clear()
    info_dialog["script"] = ["watched", "watched"]            # toggled twice: nothing changed overall
    call("extended_info", handle=-1, type="movie", id="tt1")
    assert kodi_ui_state["refreshes"] == 2


# ------------------------------------------------------------------ search results window

class FakeAction:
    def __init__(self, action_id):
        self.action_id = action_id

    def getId(self):
        return self.action_id


class FakeList(FakeControl):
    def addItems(self, items):          # Kodi's list control appends
        self.items = self.items + list(items)

    def size(self):
        return len(self.items)

    def selectItem(self, position):
        self.selected = position

    def getSelectedPosition(self):
        return self.selected

    def getListItem(self, position):
        return self.items[position]


@pytest.fixture
def search_ui(monkeypatch):
    """Runs the results window headless. `script` holds one list of steps per
    opening: ("click"|"info", row, position) or ("menu", row, position, pick)."""
    from kodi_ui import searchwindow

    state = {"openings": [], "script": [], "builtins": [], "menu_pick": 0}

    def open_window(self):
        controls, focus = {}, {"id": None}
        self.getControl = lambda cid: controls.setdefault(cid, FakeList())
        self.setProperty = lambda k, v: None
        self.getFocusId = lambda: focus["id"]
        self.setFocusId = lambda cid: focus.__setitem__("id", cid)
        self.close = lambda: None
        self.onInit()
        state["openings"].append({"controls": controls, "focus": self.focus})
        for step in (state["script"].pop(0) if state["script"] else []):
            kind, row, position = step[:3]
            focus["id"] = searchwindow.LIST_BASE + row
            controls[focus["id"]].selected = position
            if kind == "click":
                self.onClick(focus["id"])
            elif kind == "info":
                self.onAction(FakeAction(searchwindow.ACTION_SHOW_INFO))
            elif kind == "menu":
                state["menu_pick"] = step[3]
                self.onAction(FakeAction(searchwindow.ACTION_CONTEXT_MENU))
            elif kind == "right":
                self.onAction(FakeAction(searchwindow.ACTION_MOVE_RIGHT))

    monkeypatch.setattr(searchwindow.SearchWindow, "doModal", open_window)
    monkeypatch.setattr(xbmcgui.Dialog, "contextmenu", lambda self, options: state["menu_pick"])
    monkeypatch.setattr(xbmcgui.DialogProgress, "iscanceled", lambda self: False)
    monkeypatch.setattr(searchwindow, "L", lambda string_id, **kw: f"#{string_id}")
    monkeypatch.setattr(common.xbmc, "executebuiltin",
                        lambda cmd, *a: "busydialog" in cmd or state["builtins"].append(cmd))
    return state


def test_search_window_rows_and_actions(server, settings, search_ui, monkeypatch):
    from kodi_ui import searchwindow

    server.routes["/manifest.json"] = SEARCHABLE
    call("add_addon", handle=-1, url=server.url)
    server.routes["/catalog/movie/top/search=dune.json"] = metas("tt1", "tt2")
    server.routes["/catalog/series/top/search=dune.json"] = {"metas": [{"id": "tt3", "type": "series", "name": "S"}]}
    builtins = search_ui["builtins"]
    builtins.clear()

    search_ui["script"] = [[("click", 1, 0)]]               # open the show in row 2
    call("search_window", handle=-1, query="dune")
    controls = search_ui["openings"][0]["controls"]
    assert [len(controls[1001 + i].items) for i in range(2)] == [2, 1]
    assert controls[1003].visible is False and controls[2012].visible is False   # unused rows hidden
    assert "Popular" in controls[2001].label
    assert builtins == [f"ActivateWindow(Videos,{BASE}?action=meta&type=series&id=tt3,return)"]

    # Info on a movie, back out of Extended info without choosing: results reopen where we were.
    seen = []
    monkeypatch.setattr(searchwindow, "extended_info", lambda plugin, type_, id_: seen.append(id_) or False)
    search_ui["openings"].clear()
    search_ui["script"] = [[("info", 0, 1)], [("click", 0, 1)]]
    call("search_window", handle=-1, query="dune")
    assert seen == ["tt2"] and len(search_ui["openings"]) == 2
    assert search_ui["openings"][1]["focus"] == (0, 1)
    assert builtins[-1] == f"PlayMedia({BASE}?action=play&type=movie&id=tt2)"

    # Context menu: mark watched in place (the window stays open), then Back.
    search_ui["script"] = [[("menu", 0, 0, 1)]]
    call("search_window", handle=-1, query="dune")
    assert common.get_watchstate().get("tt1").watched


def test_search_catalog_manager(server, listing, settings, monkeypatch):
    server.routes["/manifest.json"] = SEARCHABLE
    call("add_addon", handle=-1, url=server.url)
    addon = common.get_registry().all()[0]
    items, _ = listing
    call("search_catalogs")
    assert [(p["action"], p["catalog"]) for p, _ in items] == [
        ("search_catalog_actions", "movie/top"), ("search_catalog_actions", "series/top")]

    call("move_search_catalog", handle=-1, addon=addon.key, catalog="series/top", delta=-1)
    call("toggle_search_catalog", handle=-1, addon=addon.key, catalog="movie/top")
    entries = common.get_registry().search_entries()
    assert [(c.key, enabled) for _, c, enabled in entries] == [("series/top", True), ("movie/top", False)]

    picks = iter([0, 5])                                     # "Enable", then "Move to bottom"
    monkeypatch.setattr(xbmcgui.Dialog, "select", lambda self, heading, options, **kw: next(picks))
    call("search_catalog_actions", handle=-1, addon=addon.key, catalog="movie/top")
    call("search_catalog_actions", handle=-1, addon=addon.key, catalog="series/top")
    entries = common.get_registry().search_entries()
    assert [(c.key, enabled) for _, c, enabled in entries] == [("movie/top", True), ("series/top", True)]



# ------------------------------------------------------------------ autoplay fallback

def test_autoplay_falls_back_until_a_stream_works(server, playback, settings, monkeypatch):
    from kodi_ui import player

    monkeypatch.setattr(player, "L", lambda string_id, **kw: f"#{string_id} {kw}" if kw else f"#{string_id}")
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams(
        {"url": "https://cdn/a.mkv", "name": "2160p"},
        {"url": "https://cdn/b.mkv", "name": "1080p"},
        {"url": "https://cdn/c.mkv", "name": "720p"},
    )
    playback["broken"] = {"https://cdn/a.mkv", "https://cdn/b.mkv"}
    call("play", type="movie", id="tt1")
    assert playback["probed"] == ["https://cdn/a.mkv", "https://cdn/b.mkv", "https://cdn/c.mkv"]
    assert resolved_path(playback)[:2] == (True, "https://cdn/c.mkv")
    trying = [m for m in playback["progress"] if m.startswith("#30177")]
    assert trying[0] == "#30177 {'number': 1, 'total': 3}[CR]Example Streams · 4K"
    assert trying[-1].startswith("#30177 {'number': 3, 'total': 3}")


def test_autoplay_cancel_and_all_broken(server, playback, settings):
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams(
        {"url": "https://cdn/a.mkv", "name": "2160p"}, {"url": "https://cdn/b.mkv", "name": "1080p"})
    playback["broken"] = {"https://cdn/a.mkv", "https://cdn/b.mkv"}

    call("play", type="movie", id="tt1")                      # nothing works
    assert playback["resolved"][-1][0] is False and len(playback["notes"]) == 1

    playback["probed"].clear()
    playback["cancel_after"] = 1                              # cancel while trying the first
    call("play", type="movie", id="tt1")
    assert playback["resolved"][-1][0] is False and playback["probed"] == ["https://cdn/a.mkv"]
    assert len(playback["notes"]) == 1                        # cancelling isn't an error


def test_same_source_broken_falls_back_to_picker(server, playback, settings, window):
    server.routes["/s/stream/series/tt5%3A1%3A2.json"] = streams(
        {"url": "https://cdn/best.mkv", "name": "2160p"},
        {"url": "https://cdn/same.mkv", "name": "720p", "behaviorHints": {"bingeGroup": "grp-A"}})
    common.get_watchstate().touch(PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1,
                                                episode=1, binge_group="grp-A"))
    playback["broken"] = {"https://cdn/same.mkv"}
    call("play", type="series", id="tt5:1:2", meta="tt5")
    assert playback["probed"] == ["https://cdn/same.mkv"] and len(playback["picker"]) == 1
    assert resolved_path(playback)[1] == "https://cdn/best.mkv"



def test_show_in_catalog_offers_unwatch_once_started(server, listing, settings, monkeypatch):
    install(server)
    server.routes["/catalog/series/top.json"] = {"metas": [{"id": "tt5", "type": "series", "name": "Show"}]}
    menus = []
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems", lambda self, items: menus.append(items))
    key = common.get_registry().all()[0].key

    call("catalog", addon=key, type="series", id="top")
    assert [cmd.split("value=")[1][0] for _, cmd in menus[-1] if "set_watched" in cmd] == ["1"]
    common.get_watchstate().set_watched(
        [PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1)], True)
    call("catalog", addon=key, type="series", id="top")
    assert [cmd.split("value=")[1][0] for _, cmd in menus[-1] if "set_watched" in cmd] == ["1", "0"]



def test_stale_parameters_are_ignored(server, playback, settings):
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams({"url": "https://cdn/a.mkv", "name": "1080p"})
    call("play", type="movie", id="tt1", folder=1)            # link from the reverted simplified menu
    assert resolved_path(playback)[:2] == (True, "https://cdn/a.mkv")


def test_context_menu_labels_are_coloured(server, listing, settings, monkeypatch):
    install(server)
    server.routes["/catalog/movie/top.json"] = {"metas": [{"id": "tt1", "type": "movie", "name": "M"}]}
    menus = []
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems", lambda self, items: menus.append(items))
    call("catalog", addon=common.get_registry().all()[0].key, type="movie", id="top")
    call("manage")
    labels = [label for menu in menus for label, _ in menu]
    assert labels and all(l.startswith("[COLOR FFFF8080]") and l.endswith("[/COLOR]") for l in labels)



def test_rename_search_catalog(server, listing, settings, search_ui, monkeypatch):
    from kodi_ui import searchwindow

    server.routes["/manifest.json"] = SEARCHABLE
    call("add_addon", handle=-1, url=server.url)
    addon = common.get_registry().all()[0]
    names = iter(["  Films  ", ""])
    asked = []
    monkeypatch.setattr(xbmcgui.Dialog, "input",
                        lambda self, heading, defaultt="", **kw: asked.append(defaultt) or next(names))

    call("rename_search_catalog", handle=-1, addon=addon.key, catalog="movie/top")
    installed = common.get_registry().get(addon.key)
    movie_top = next(c for c in installed.manifest.catalogs if c.key == "movie/top")
    assert installed.search_title(movie_top) == "Films" and asked == ["Popular"]
    assert installed.prefs_for(movie_top).search            # still enabled, other prefs untouched

    # The new name is used for the result row title (but not for browsing).
    server.routes["/catalog/movie/top/search=x.json"] = metas("tt1")
    search_ui["script"] = [[]]
    call("search_window", handle=-1, query="x")
    assert "Films" in search_ui["openings"][0]["controls"][2001].label
    items, _ = listing
    call("type", type="movie")
    assert all("Films" not in str(p) for p, _ in items)

    call("rename_search_catalog", handle=-1, addon=addon.key, catalog="movie/top")   # empty: original back
    installed = common.get_registry().get(addon.key)
    assert asked[-1] == "Films" and installed.search_title(movie_top) == "Popular"



def test_episodes_in_their_own_show_offer_browse_show(server, listing, settings, monkeypatch):
    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    menus = []
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems", lambda self, items: menus.append(items))
    call("season", type="series", id="tt5", season=1)
    assert all(any("action=meta&type=series&id=tt5" in cmd for _, cmd in menu) for menu in menus)



def test_watch_changes_bump_widget_reload_token(server, listing, settings, window, kodi_ui_state, monkeypatch):
    from kodi_ui import service

    install(server)
    token = lambda: window.get(common.WIDGETS_RELOAD)
    assert token() is None
    call("set_watched", handle=-1, type="movie", id="tt1", value=1)
    first = token()
    assert first
    monkeypatch.setattr(common.time, "time", lambda: 9_999_999_999.0)
    service.after_playback(None)                              # nothing changed: token unchanged
    assert token() == first
    service.after_playback("resume")
    assert token() != first


# ------------------------------------------------------------------ retry after a playback error

def test_play_announces_retry_and_start_skips(server, playback, settings, window):
    settings["autoplay"] = True
    server.routes["/s/stream/movie/tt1.json"] = streams(
        {"url": "https://cdn/a.mkv", "name": "2160p"}, {"url": "https://cdn/b.mkv", "name": "1080p"},
        {"url": "https://cdn/c.mkv", "name": "720p"})
    call("play", type="movie", id="tt1")
    retry = common.take_announcement()["retry"]
    assert (retry["next"], retry["total"], retry["tries"], retry["resume"]) == (1, 3, 0, False)

    playback["resolved"].clear()
    call("play", type="movie", id="tt1", start=1, tries=1)       # what the service asks for
    assert resolved_path(playback)[1] == "https://cdn/b.mkv"
    retry = common.take_announcement()["retry"]
    assert (retry["next"], retry["tries"]) == (2, 1)
    assert server.requests.count("/s/stream/movie/tt1.json") == 1  # stream list reused from cache


def test_service_retries_next_stream_on_error(settings, window, monkeypatch):
    from kodi_ui import service

    for module in (service, common):
        monkeypatch.setattr(module, "ADDON_ID", "plugin.video.stremiobridge")
    worker = FakeWorker()
    tracker = service.Tracker(worker)
    retry = {"type": "movie", "id": "tt1", "meta": None, "binge": "g", "next": 1, "total": 3, "tries": 0,
             "resume": True}
    common.announce_playback(PlaybackEntry(video_id="tt1", type="movie"), 0, retry)
    tracker.onPlayBackError()                                 # failed before the video started
    assert worker.jobs == [("retry_next_stream", (retry,))]

    played = []
    monkeypatch.setattr(service.xbmc, "executebuiltin", lambda cmd, *a: played.append(cmd))
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda *a, **k: None)
    service.retry_next_stream(retry)
    assert played == [f"PlayMedia({BASE}?action=play&type=movie&id=tt1&binge=g&resume=1&start=1&tries=1)"]

    service.retry_next_stream({**retry, "next": 3})            # nothing left to try
    service.retry_next_stream({**retry, "tries": service.MAX_STREAM_RETRIES})
    assert len(played) == 1

    worker.jobs.clear()                                       # Stop before starting: the user cancelled
    common.announce_playback(PlaybackEntry(video_id="tt1", type="movie"), 0, retry)
    tracker.onPlayBackStopped()
    assert worker.jobs == []



def test_search_rows_page_on_scroll(server, settings, search_ui):
    paged = {**SEARCHABLE, "catalogs": [{"type": "movie", "id": "top", "name": "Popular",
                                         "extra": [{"name": "search"}, {"name": "skip"}]}]}
    server.routes["/manifest.json"] = paged
    call("add_addon", handle=-1, url=server.url)
    server.routes["/catalog/movie/top/search=x.json"] = metas(*[f"tt{i}" for i in range(20)])
    server.routes["/catalog/movie/top/search=x&skip=20.json"] = metas(*[f"tt{i}" for i in range(20, 32)])

    # Far from the end: nothing loads. Near the end: page 2 is appended; then the row is exhausted.
    search_ui["script"] = [[("right", 0, 3), ("right", 0, 16), ("right", 0, 30)]]
    call("search_window", handle=-1, query="x")
    controls = search_ui["openings"][0]["controls"]
    assert len(controls[1001].items) == 32
    assert server.requests.count("/catalog/movie/top/search=x&skip=20.json") == 1
    assert "(32)" in controls[2001].label                       # no "+" once there's no next page


# ------------------------------------------------------------------ library

@pytest.fixture
def kodi_library(tmp_path, monkeypatch):
    """A fake Kodi video library (JSON-RPC), sources.xml and builtins."""
    from kodi_ui import library

    state = {"movies": [], "episodes": [], "set": [], "builtins": []}
    special = {"special://profile/sources.xml": str(tmp_path / "sources.xml")}
    real_profile = common.profile_dir()

    def translate(path):
        if path in special:
            return special[path]
        prefix = "special://profile/addon_data/plugin.video.stremiobridge/"
        return os.path.join(real_profile, path[len(prefix):]) if path.startswith(prefix) else path

    def jsonrpc(method, **params):
        if method == "VideoLibrary.GetMovies":
            return {"movies": state["movies"]}
        if method == "VideoLibrary.GetEpisodes":
            return {"episodes": state["episodes"]}
        if method in ("VideoLibrary.SetMovieDetails", "VideoLibrary.SetEpisodeDetails"):
            state["set"].append((method, params))
            return "OK"
        if method == "VideoLibrary.GetMovieDetails":
            item = next(m for m in state["movies"] if m["movieid"] == params["movieid"])
            return {"moviedetails": {"file": item["file"]}}
        return {}

    monkeypatch.setattr(library.xbmcvfs, "translatePath", translate)
    monkeypatch.setattr(library, "jsonrpc", jsonrpc)
    monkeypatch.setattr(library.ADDON, "getAddonInfo",
                        lambda key: "plugin.video.stremiobridge" if key == "id" else "")
    monkeypatch.setattr(library.xbmc, "executebuiltin",
                        lambda cmd, *a: "busydialog" in cmd or state["builtins"].append(cmd))
    monkeypatch.setattr(common, "_library", None)
    monkeypatch.setattr(common, "ADDON_ID", "plugin.video.stremiobridge")
    state["sources"] = special["special://profile/sources.xml"]
    return state


def test_library_setup_writes_sources(settings, kodi_library, monkeypatch):
    from kodi_ui import library

    monkeypatch.setattr(xbmcgui.Dialog, "ok", lambda *a: True)
    monkeypatch.setattr(xbmcgui.Dialog, "yesno", lambda *a: False)        # don't restart
    with open(kodi_library["sources"], "w") as f:
        f.write('<sources><video><default pathversion="1"/><source><name>Films</name>'
                '<path pathversion="1">/media/films/</path></source></video><music/></sources>')
    assert not library.sources_configured()
    call("library_setup", handle=-1)
    assert library.sources_configured()
    text = open(kodi_library["sources"]).read()
    assert "/media/films/" in text and text.count("<source>") == 3 and "Stremio Bridge TV" in text
    call("library_setup", handle=-1)                                      # idempotent
    assert open(kodi_library["sources"]).read().count("<source>") == 3


def test_library_add_remove_and_watched_sync(server, settings, kodi_library, monkeypatch):
    from kodi_ui import library

    install(server)
    library.add_sources()
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1", "type": "movie", "name": "Alien", "releaseInfo": "1979"}}
    server.routes["/meta/series/tt5.json"] = SHOW
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda *a, **k: None)

    call("library_add", handle=-1, type="movie", id="tt1")
    call("library_add", handle=-1, type="series", id="tt5")
    lib = common.get_library()
    episodes = lib.get("series", "tt5")["episodes"]
    assert lib.contains("movie", "tt1") and sorted(episodes) == ["tt5:0:1", "tt5:1:1", "tt5:2:1"]  # aired only
    assert any(cmd.startswith("UpdateLibrary(video,special://profile/addon_data/plugin.video.stremiobridge/"
                              "Library/Movies/Alien (1979)/") for cmd in kodi_library["builtins"])

    # Kodi scanned them; our watch state is pushed to its copies.
    movie_file = [p for p, ids in lib.file_map().items() if ids[0] == "tt1"][0]
    kodi_library["movies"] = [{"movieid": 7, "file": movie_file, "playcount": 0, "resume": {"position": 0}}]
    common.get_watchstate().record(PlaybackEntry(video_id="tt1", type="movie"), 900, 6000)
    assert library.sync_library_watched() == 1
    assert kodi_library["set"][-1] == ("VideoLibrary.SetMovieDetails",
                                       {"movieid": 7, "playcount": 0, "resume": {"position": 900.0, "total": 6000.0}})
    kodi_library["movies"][0]["resume"] = {"position": 900}
    assert library.sync_library_watched() == 0                            # already in step

    # Kodi's own "Mark as watched" on the library copy comes back to us.
    library.on_library_update({"item": {"id": 7, "type": "movie"}, "playcount": 1})
    assert common.get_watchstate().get("tt1").watched

    call("library_remove", handle=-1, type="movie", id="tt1")
    assert not lib.contains("movie", "tt1") and "CleanLibrary(video,false)" in kodi_library["builtins"]


def test_library_update_and_watchlist(server, settings, kodi_library, monkeypatch):
    from kodi_ui import library

    install(server)
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1", "type": "movie", "name": "Alien"}}
    server.routes["/meta/movie/tt2.json"] = {"meta": {"id": "tt2", "type": "movie", "name": "Aliens"}}
    server.routes["/meta/series/tt5.json"] = SHOW
    watchlist = [{"type": "movie", "id": "tt1", "title": "Alien", "year": 1979, "ids": {"imdb": "tt1"}}]

    class FakeMDBList:
        def watchlist(self):
            return list(watchlist)

    monkeypatch.setattr(library, "get_mdblist", lambda: FakeMDBList())
    settings["library_watchlist"] = True
    library.add_title("movie", "tt2")                                     # added by hand
    summary = library.update_library()
    lib = common.get_library()
    assert summary["added"] == 1 and lib.get("movie", "tt1")["source"] == "watchlist"

    watchlist.clear()                                                      # taken off the watchlist
    summary = library.update_library()
    assert summary["removed"] == 1 and not lib.contains("movie", "tt1") and lib.contains("movie", "tt2")

    library.add_title("series", "tt5")
    assert library.update_library()["episodes"] == 0                       # nothing new has aired



# ------------------------------------------------------------------ default views

def test_skin_views_are_read_from_the_skin(tmp_path, monkeypatch):
    from kodi_ui import views

    skin = tmp_path / "skin.test"
    (skin / "xml").mkdir(parents=True)
    (skin / "addon.xml").write_text('<addon><extension point="xbmc.gui.skin"><res folder="xml"/></extension></addon>')
    (skin / "xml" / "View_50_List.xml").write_text(
        '<includes><include name="v"><control type="list" id="50"><viewtype label="List">list</viewtype>'
        '</control><control type="panel" id="$PARAM[id]"><viewtype label="Dynamic">x</viewtype></control>'
        '</include></includes>')
    (skin / "xml" / "View_52_Wall.xml").write_text(
        '<includes><include name="w"><control type="panel" id="52"><viewtype label="31100">wall</viewtype>'
        '</control></include></includes>')
    (skin / "xml" / "broken.xml").write_text("<oops")
    monkeypatch.setattr(views.xbmcvfs, "translatePath", lambda path: str(skin) + "/")
    monkeypatch.setattr(views.xbmc, "getSkinDir", lambda: "skin.test")
    monkeypatch.setattr(views.xbmc, "getLocalizedString", lambda n: "")
    monkeypatch.setattr(views.xbmcaddon.Addon, "getLocalizedString", lambda self, n: {31100: "Wall"}.get(n, ""))
    assert views.skin_views() == [(50, "List"), (52, "Wall")]          # dynamic/broken ones skipped


def test_choose_and_apply_view(settings, monkeypatch, kodi_ui_state, tmp_path):
    from kodi_ui import views

    monkeypatch.setattr(views, "skin_views", lambda: [(50, "List"), (52, "Wall")])
    (tmp_path / "extras" / "views" / "light").mkdir(parents=True)
    for name in ("52.jpg", "cars.jpg", "50Music.jpg", "light/52.jpg"):
        (tmp_path / "extras" / "views" / name).write_bytes(b"x")
    monkeypatch.setattr(views.xbmcvfs, "translatePath", lambda p: str(tmp_path) + "/")
    assert views.view_previews() == {52: str(tmp_path / "extras" / "views" / "52.jpg")}   # top level wins

    saved, shown = {}, {}
    monkeypatch.setattr(views.ADDON, "setSettingString", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(views.ADDON, "openSettings", lambda: None)

    def pick(self):
        shown.update(options=self.options, preselect=self.preselect)
        self.choice = 2

    monkeypatch.setattr(views.ViewPicker, "doModal", pick)
    call("choose_view", handle=-1, content="movies")
    assert saved == {"view_movies": "Wall (52)"}
    assert [o[2] for o in shown["options"]] == ["", "", str(tmp_path / "extras" / "views" / "52.jpg")]

    settings["view_movies"] = "Wall (52)"
    applied = []
    monkeypatch.setattr(views.xbmc, "executebuiltin", lambda cmd, *a: applied.append(cmd))
    monkeypatch.setattr(views.xbmc.Monitor, "waitForAbort", lambda self, timeout=0: False)
    monkeypatch.setattr(views.xbmc, "getCondVisibility", lambda cond: kodi_ui_state["idle"])
    assert views.apply_view("movies") and applied == ["Container.SetViewMode(52)"]
    assert not views.apply_view("episodes")                            # nothing chosen for episodes

    # Page 2: wait until Kodi shows the new page, not the one it was opened from.
    page1, page2 = BASE + "?action=catalog&id=top", BASE + "?action=catalog&id=top&skip=20"
    shown_paths = iter([page1, page1, page1, page2])
    monkeypatch.setattr(views.xbmc, "getInfoLabel", lambda label: next(shown_paths))
    assert views.apply_view("movies", path=page2) and len(applied) == 2

    kodi_ui_state["idle"] = False                                       # e.g. a widget on Home: not applied
    assert not views.apply_view("movies", wait=0.3) and len(applied) == 2


# ------------------------------------------------------------------ watchlist

class FakeWatchlistMDBList:
    def __init__(self):
        self.items, self.calls = [], []

    def watchlist(self):
        return [dict(i) for i in self.items]

    def watchlist_add(self, type_, ids):
        self.calls.append(("add", type_, ids))
        self.items.append({"type": type_, "id": ids["imdb"], "title": "T", "year": 2000, "ids": ids,
                           "poster": "", "description": ""})

    def watchlist_remove(self, type_, ids):
        self.calls.append(("remove", type_, ids))
        self.items = [i for i in self.items if i["id"] != ids["imdb"]]


def test_watchlist_menu_list_and_changes(server, listing, settings, monkeypatch):
    from kodi_ui import watchlist

    install(server)
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1", "type": "movie", "name": "Alien"}}
    server.routes["/catalog/movie/top.json"] = {"metas": [{"id": "tt1", "type": "movie", "name": "Alien"}]}
    fake = FakeWatchlistMDBList()
    monkeypatch.setattr(watchlist, "get_mdblist", lambda: fake)
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda *a, **k: None)
    monkeypatch.setattr(watchlist, "_membership", {})
    menus = []
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems", lambda self, items: menus.append(items))
    key = common.get_registry().all()[0].key

    call("catalog", addon=key, type="movie", id="top")
    assert any("action=watchlist_add&type=movie&id=tt1" in cmd for _, cmd in menus[-1])

    call("watchlist_add", handle=-1, type="movie", id="tt1")
    assert fake.calls == [("add", "movie", {"imdb": "tt1"})]
    watchlist._membership.clear()
    call("catalog", addon=key, type="movie", id="top")
    assert any("action=watchlist_remove&type=movie&id=tt1" in cmd for _, cmd in menus[-1])

    items, _ = listing
    items.clear()
    call("watchlist")
    assert [(p["action"], p["id"]) for p, _ in items] == [("play", "tt1")]

    call("watchlist_remove", handle=-1, type="movie", id="tt1")
    items.clear()
    call("watchlist")
    assert items == [] and fake.calls[-1][0] == "remove"


def test_watchlist_without_mdblist(server, listing, settings, monkeypatch):
    from kodi_ui import watchlist

    install(server)
    monkeypatch.setattr(watchlist, "get_mdblist", lambda: None)
    monkeypatch.setattr(watchlist, "_membership", {})
    assert watchlist.watchlist_menu(router.Plugin([BASE, "1", ""]), "movie", "tt1") == []
    items, _ = listing
    call("root")
    assert all(p["action"] != "watchlist" for p, _ in items)



# ------------------------------------------------------------------ optional genre filter

def test_genre_filter_when_browsing_not_on_widgets(server, listing, settings, monkeypatch):
    from kodi_ui import browse

    install(server)
    key = common.get_registry().all()[0].key
    server.routes["/catalog/movie/top.json"] = metas("tt1")
    server.routes["/catalog/movie/top/genre=Sci-Fi.json"] = metas("tt9")
    settings["genre_filter"] = True
    in_videos = {"value": True}
    monkeypatch.setattr(browse, "browsing_in_videos_window", lambda: in_videos["value"])
    items, _ = listing

    call("catalog", addon=key, type="movie", id="top")
    (picker, folder), movie = items
    assert picker["action"] == "choose_filter" and picker["name"] == "genre" and folder is False
    assert movie[0]["id"] == "tt1"                                 # unfiltered by default

    in_videos["value"] = False                                     # a widget on the home screen
    items.clear()
    call("catalog", addon=key, type="movie", id="top")
    assert [p["action"] for p, _ in items] == ["play"]

    updates = []
    monkeypatch.setattr(browse.xbmc, "executebuiltin", lambda cmd, *a: updates.append(cmd))
    picks = iter([2, 0])                                           # "Sci-Fi" (after "All", "Action"), then "All"
    monkeypatch.setattr(xbmcgui.Dialog, "select", lambda self, heading, labels, preselect=-1: next(picks))
    call("choose_filter", handle=-1, addon=key, type="movie", id="top", name="genre")
    assert updates[-1] == f"Container.Update({BASE}?action=catalog&addon={key}&type=movie&id=top&f_genre=Sci-Fi)"
    call("choose_filter", handle=-1, addon=key, type="movie", id="top", name="genre", f_genre="Sci-Fi")
    assert updates[-1] == f"Container.Update({BASE}?action=catalog&addon={key}&type=movie&id=top)"


def test_required_genre_offers_no_all(server, listing, settings, monkeypatch):
    from kodi_ui import browse

    server.routes["/manifest.json"] = {**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "tvdb", "name": "TVDB Genres",
         "extra": [{"name": "genre", "isRequired": True, "options": ["Action", "Drama"]}]}]}
    call("add_addon", handle=-1, url=server.url)
    key = common.get_registry().all()[0].key
    offered = []
    monkeypatch.setattr(browse.xbmc, "executebuiltin", lambda cmd, *a: None)
    monkeypatch.setattr(xbmcgui.Dialog, "select",
                        lambda self, heading, labels, preselect=-1: offered.append(labels) or -1)
    call("choose_filter", handle=-1, addon=key, type="movie", id="tvdb", name="genre")
    assert offered == [["Action", "Drama"]]


def test_filter_and_next_page_tiles(server, listing, settings, monkeypatch):
    from kodi_ui import browse

    install(server)
    key = common.get_registry().all()[0].key
    server.routes["/catalog/movie/top/genre=Horror.json"] = metas(*[f"tt{i}" for i in range(20)])
    settings["genre_filter"] = True
    monkeypatch.setattr(browse, "browsing_in_videos_window", lambda: True)
    monkeypatch.setattr(browse, "L", lambda string_id, **kw: f"#{string_id}{kw or ''}")
    monkeypatch.setattr(browse.ADDON, "getAddonInfo", lambda key: "/addon" if key == "path" else "")
    labels, arts = [], []
    monkeypatch.setattr(xbmcgui.ListItem, "__init__", lambda self, label="", *a, **k: labels.append(label))
    monkeypatch.setattr(xbmcgui.ListItem, "setArt", lambda self, art: arts.append(art))

    call("catalog", addon=key, type="movie", id="top", f_genre="Horror")
    assert labels[0] == "#30295{'name': 'Genre'}  [COLOR FF999999]· Horror[/COLOR]"
    assert arts[0]["poster"] == os.path.join("/addon", "resources", "media", "filter_genre.png")
    assert labels[-1] == "#30050" and arts[-1]["poster"].endswith("next_page.png")


def test_toggle_kodi_parent_items(settings, monkeypatch):
    from kodi_ui import menus

    value = {"v": True}
    calls = []

    def jsonrpc(method, **params):
        calls.append((method, params))
        if method == "Settings.GetSettingValue":
            return {"value": value["v"]}
        value["v"] = params["value"]
        return True

    monkeypatch.setattr(menus, "jsonrpc", jsonrpc)
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda *a, **k: None)
    answers = iter([True, False, True])
    monkeypatch.setattr(xbmcgui.Dialog, "yesno", lambda *a, **k: next(answers))
    call("toggle_parent_items", handle=-1)
    assert value["v"] is False and calls[-1] == ("Settings.SetSettingValue",
                                                 {"setting": "filelists.showparentdiritems", "value": False})
    call("toggle_parent_items", handle=-1)                     # declined: unchanged
    assert value["v"] is False
    call("toggle_parent_items", handle=-1)
    assert value["v"] is True



# ------------------------------------------------------------------ person searches

def test_person_search_targets(server, settings, monkeypatch):
    from kodi_ui import search as search_module
    from stremio.meta import cinemeta_search_targets
    from stremio.registry import InstalledAddon

    server.routes["/manifest.json"] = {**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "search.movie", "name": "Movies Search", "extra": [{"name": "search", "isRequired": True}]}]}
    call("add_addon", handle=-1, url=server.url)
    settings["cinemeta_fallback"] = True                       # person searches add Cinemeta only if allowed
    monkeypatch.setattr(xbmcgui.DialogProgress, "iscanceled", lambda self: False)
    monkeypatch.setattr(xbmcgui.Dialog, "notification", lambda *a, **k: None)
    fake_cinemeta = [(InstalledAddon(server.url + "/cm/manifest.json", a.manifest), c)
                     for a, c in cinemeta_search_targets()]
    monkeypatch.setattr(search_module, "cinemeta_search_targets",
                        lambda type_=None: [t for t in fake_cinemeta if type_ is None or t[1].type == type_])
    server.routes["/catalog/movie/search.movie/search=Russell%20Crowe.json"] = metas("tt9")    # a stray title match
    server.routes["/cm/catalog/movie/top/search=Russell%20Crowe.json"] = metas("tt0172495", "tt0268978")

    groups, _ = search_module.find_results("Russell Crowe")            # typed search: own catalogs only
    assert [[p.id for p in previews] for _, _, previews in groups] == [["tt9"]]
    groups, _ = search_module.find_results("Russell Crowe", person=True)  # actor: Cinemeta added
    assert [[p.id for p in previews] for _, _, previews in groups] == [["tt9"], ["tt0172495", "tt0268978"]]

    # With AIOMetadata people search enabled, only that is used for people.
    server.routes["/manifest.json"] = {**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "search.movie", "name": "Movies Search", "extra": [{"name": "search", "isRequired": True}]},
        {"type": "movie", "id": "people_search.people_search_movie", "name": "People Search",
         "extra": [{"name": "search", "isRequired": True}]}]}
    addon = common.get_registry().all()[0]
    common.get_registry().update_manifest(addon.key, common.get_client().fetch_manifest(server.url)[1])
    server.routes["/catalog/movie/people_search.people_search_movie/search=Russell%20Crowe.json"] = metas("tt0120586")
    groups, _ = search_module.find_results("Russell Crowe", person=True)
    assert [[p.id for p in previews] for _, _, previews in groups] == [["tt0120586"]]


# ------------------------------------------------------------------ usability batch

def test_select_opens_info_setting(server, listing, settings):
    install(server)
    server.routes["/catalog/movie/top.json"] = metas("tt1")
    items, _ = listing
    key = common.get_registry().all()[0].key
    call("catalog", addon=key, type="movie", id="top")
    assert items == [({"action": "play", "type": "movie", "id": "tt1"}, False)]
    settings["select_opens_info"] = True
    items.clear()
    call("catalog", addon=key, type="movie", id="top")
    assert items == [({"action": "extended_info", "type": "movie", "id": "tt1"}, False)]


def test_widgets_folder(server, listing, settings, monkeypatch):
    from kodi_ui import menus

    install(server)
    monkeypatch.setattr(menus, "get_mdblist", lambda: object())
    monkeypatch.setattr(menus, "WIDGETS_RELOAD", "plugin.video.stremiobridge.widgets.reload")
    urls = []
    monkeypatch.setattr(xbmcplugin, "addDirectoryItem",
                        lambda handle, url, item, isFolder=False, totalItems=0: urls.append(url))
    call("widgets")
    token = "&reload=$INFO[Window(Home).Property(plugin.video.stremiobridge.widgets.reload)]"
    assert all(url.endswith(token) for url in urls)
    assert [url.split("action=")[1].split("&")[0] for url in urls] == [
        "continue", "next_up", "watchlist", "catalog", "catalog"]

    items, _ = listing
    settings["show_widgets_folder"] = True
    monkeypatch.setattr(xbmcplugin, "addDirectoryItem", lambda handle, url, item, isFolder=False, totalItems=0:
                        items.append((dict(parse_qsl(urlsplit(url).query)), isFolder)))
    call("root")
    assert items[-1][0]["action"] == "widgets"


def test_continue_watching_combined_and_time_left(server, listing, settings, monkeypatch):
    from kodi_ui import watching

    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    clock = {"now": 1000.0}
    monkeypatch.setattr(common, "get_watchstate", lambda: WatchStateAt(clock))
    state = common.get_watchstate()
    state.set_watched([PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2)], True)
    clock["now"] = 2000.0
    state.record(PlaybackEntry(video_id="tt1", type="movie", title="Movie"), 1200, 6000)   # newer
    labels2 = []
    monkeypatch.setattr(xbmcgui.ListItem, "setProperty",
                        lambda self, k, v: k == "TimeLeft" and labels2.append(v))
    monkeypatch.setattr(watching, "L", lambda string_id, **kw: f"#{string_id}{kw}")
    items, _ = listing

    call("continue")
    assert [p["id"] for p, _ in items] == ["tt1"]                      # setting off: part-watched only
    assert labels2 == ["#30320{'time': '1 h 20 min'}"]

    settings["continue_with_next_up"] = True
    items.clear()
    call("continue")
    assert [p["id"] for p, _ in items] == ["tt1", "tt5:2:1"]            # newest first, then the next episode


class WatchStateAt:
    """A WatchState on the test profile with a controllable clock."""

    def __new__(cls, clock):
        from stremio.watchstate import WatchState

        return WatchState(os.path.join(common.profile_dir(), "watch.db"), clock=lambda: clock["now"])


def test_show_progress_and_hide_watched(server, listing, settings, monkeypatch):
    from kodi_ui import listitems

    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    server.routes["/catalog/series/top.json"] = {"metas": [{"id": "tt5", "type": "series", "name": "Show"}]}
    server.routes["/catalog/movie/top.json"] = metas("tt1", "tt2")
    state = common.get_watchstate()
    state.set_watched([PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1)], True)
    props = {}
    monkeypatch.setattr(xbmcgui.ListItem, "setProperty", lambda self, k, v: props.__setitem__(k, v))
    key = common.get_registry().all()[0].key
    items, _ = listing

    call("catalog", addon=key, type="series", id="top")
    assert (props["WatchedEpisodes"], props["TotalEpisodes"]) == ("1", "2")   # aired 1x01, 2x01 (specials not counted)

    settings["hide_watched"] = True
    state.set_watched([PlaybackEntry(video_id="tt1", type="movie")], True)
    items.clear()
    call("catalog", addon=key, type="movie", id="top")
    assert [p["id"] for p, _ in items] == ["tt2"]
    state.set_watched([PlaybackEntry(video_id="tt5:2:1", type="series", meta_id="tt5", season=2, episode=1)], True)
    items.clear()
    call("catalog", addon=key, type="series", id="top")
    assert items == []                                                 # every aired episode watched


def test_prewarm_refreshes_cached_catalogs(server, settings, monkeypatch):
    from kodi_ui import service

    install(server)
    settings.update(prewarm=True, cache_catalog_minutes=60)
    server.routes["/catalog/movie/top.json"] = metas("tt1")
    server.routes["/catalog/series/top.json"] = metas("tt5")
    monkeypatch.setattr(service.xbmc.Player, "isPlaying", lambda self: False)
    service.prewarm()
    service.prewarm()                                                  # refreshes despite a fresh cache
    assert server.requests.count("/catalog/movie/top.json") == 2
    monkeypatch.setattr(service.xbmc.Player, "isPlaying", lambda self: True)
    service.prewarm()                                                  # never while something plays
    assert server.requests.count("/catalog/movie/top.json") == 2


def test_open_on_next_episode(server, listing, settings, monkeypatch):
    from kodi_ui import details

    install(server)
    server.routes["/meta/series/tt5.json"] = SHOW
    focused = []
    monkeypatch.setattr(details, "set_focus", lambda position: focused.append(position))
    state = common.get_watchstate()
    state.set_watched([PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1)], True)

    call("meta", type="series", id="tt5")          # seasons 1, 2, 0: next aired after 1x01 is... 1x02 unaired
    assert focused == []
    state.set_watched([PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2)], True)
    call("meta", type="series", id="tt5")          # next is 2x01 -> season 2 (index 1)
    call("season", type="series", id="tt5", season=2)   # and in season 2, episode 1 (index 0: no focus needed)
    assert focused == [1, 0]


def test_focus_item_allows_for_parent_entry(settings, monkeypatch):
    from kodi_ui import common as kodi_common, views

    commands = []
    monkeypatch.setattr(views.xbmc, "executebuiltin", lambda cmd, *a: commands.append(cmd))
    monkeypatch.setattr(views, "_wait_for_listing", lambda path, content=None, wait=3.0: True)
    monkeypatch.setattr(views.xbmcgui.Window, "getFocusId", lambda self: 52)
    parent = {"value": True}
    monkeypatch.setattr(kodi_common, "jsonrpc", lambda method, **kw: parent)
    assert views.focus_item(3)
    parent["value"] = False
    assert views.focus_item(3)
    assert commands == ["SetFocus(52,4,absolute)", "SetFocus(52,3,absolute)"]


# ------------------------------------------------------------------ hubs

HUB_ADDON = {**CINEMETA_LIKE, "catalogs": [
    {"type": "movie", "id": "trending", "name": "Trending 🍿", "extra": [{"name": "genre", "options": ["Day", "Week"]}]},
    {"type": "movie", "id": "popular", "name": "Popular",
     "extra": [{"name": "genre", "options": ["None", "Action", "Comedy", "Drama", "Horror", "Sci-Fi"]}]},
    {"type": "series", "id": "shows", "name": "Shows"},
    {"type": "movie", "id": "search", "name": "Search", "extra": [{"name": "search", "isRequired": True}]},
]}


def test_hub_view_and_genres(server, listing, settings):
    server.routes["/manifest.json"] = HUB_ADDON
    call("add_addon", handle=-1, url=server.url)
    key = common.get_registry().all()[0].key
    items, _ = listing

    call("hub", hub="movies")
    assert [(p["action"], p.get("id")) for p, _ in items] == [
        ("new_search", None), ("hub_genres", None), ("catalog", "trending"), ("catalog", "popular")]

    items.clear()
    call("hub_genres", hub="movies")                    # Day/Week isn't a genre list; "None" isn't a genre
    assert [(p["f_genre"], p["id"]) for p, _ in items] == [
        ("Action", "popular"), ("Comedy", "popular"), ("Drama", "popular"), ("Horror", "popular"),
        ("Sci-Fi", "popular")]
    assert all(p["addon"] == key for p, _ in items)


def test_organise_catalogs(server, listing, settings, monkeypatch):
    from kodi_ui import hubs

    server.routes["/manifest.json"] = HUB_ADDON
    call("add_addon", handle=-1, url=server.url)
    key = common.get_registry().all()[0].key
    items, _ = listing
    call("organise")
    assert [p.get("hub") for p, _ in items] == ["movies", "tvshows", None]  # then "New hub…"
    items.clear()
    call("organise_hub", hub="movies")
    assert [p["catalog"] for p, _ in items] == ["movie/trending", "movie/popular"]

    call("organise_do", handle=-1, addon=key, catalog="movie/popular", do="up")
    call("organise_do", handle=-1, addon=key, catalog="movie/popular", do="pin")
    monkeypatch.setattr(xbmcgui.Dialog, "input", lambda self, heading, defaultt="", **kw: "Hot right now")
    call("organise_do", handle=-1, addon=key, catalog="movie/trending", do="rename")
    monkeypatch.setattr(xbmcgui.Dialog, "select", lambda self, heading, options, **kw: options.index("#30343"))
    monkeypatch.setattr(hubs, "L", lambda string_id, **kw: f"#{string_id}")
    call("organise_do", handle=-1, addon=key, catalog="series/shows", do="hub")      # to "More"
    call("organise_do", handle=-1, addon=key, catalog="movie/trending", do="toggle")  # hide

    registry = common.get_registry()
    assert [c.id for _, c in registry.hub_catalogs("movies")] == ["popular"]
    assert [c.id for _, c in registry.hub_catalogs("more")] == ["shows"]
    assert registry.get(key).display_name(next(c for c in registry.get(key).manifest.catalogs
                                                if c.id == "trending")) == "Hot right now"
    items.clear()
    call("root")                                          # pinned catalog first, then hubs in use
    assert [(p["action"], p.get("id") or p.get("hub")) for p, _ in items] == [
        ("catalog", "popular"), ("hub", "movies"), ("hub", "more"), ("search_menu", None)]


def test_catalog_names_are_tidied(server, listing, settings, monkeypatch):
    server.routes["/manifest.json"] = HUB_ADDON
    call("add_addon", handle=-1, url=server.url)
    labels = []
    monkeypatch.setattr(xbmcgui.ListItem, "__init__", lambda self, label="", *a, **k: labels.append(label))
    call("hub", hub="movies")
    assert any(label.startswith("Trending  [COLOR") for label in labels)
    labels.clear()
    settings["tidy_names"] = False
    call("hub", hub="movies")
    assert any(label.startswith("Trending 🍿") for label in labels)


def test_settings_buttons_wait_for_dialogs_then_open(monkeypatch):
    """Kodi refuses ActivateWindow while the settings dialog is closing."""
    modal = iter([True, True, False])
    ran = []
    monkeypatch.setattr(xbmc, "getCondVisibility", lambda cond: next(modal))
    monkeypatch.setattr(xbmc.Monitor, "waitForAbort", lambda self, timeout=0: False)
    monkeypatch.setattr(xbmc.Monitor, "abortRequested", lambda self: False)
    monkeypatch.setattr(xbmc, "executebuiltin", ran.append)
    call("open", handle=-1, target="organise")
    assert ran == [f"ActivateWindow(Videos,{BASE}?action=organise,return)"]
    call("open", handle=-1, target="play")  # only our settings folders
    assert len(ran) == 1


def test_organise_hubs(server, listing, settings, monkeypatch):
    server.routes["/manifest.json"] = HUB_ADDON
    call("add_addon", handle=-1, url=server.url)
    key = common.get_registry().all()[0].key
    items, _ = listing
    monkeypatch.setattr(xbmcgui.Dialog, "input", lambda self, heading, defaultt="", **kw: "Kids")
    call("hub_create", handle=-1)
    call("organise")                                       # the new, empty hub is listed, then "New hub…"
    assert [(p["action"], p.get("hub")) for p, _ in items] == [
        ("organise_hub", "movies"), ("organise_hub", "tvshows"), ("organise_hub", "c1"), ("hub_create", None)]

    # Moving a catalog can create the hub on the way: the last option is "New hub…".
    monkeypatch.setattr(xbmcgui.Dialog, "select", lambda self, heading, options, **kw: len(options) - 1)
    monkeypatch.setattr(xbmcgui.Dialog, "input", lambda self, heading, defaultt="", **kw: "Weekend")
    call("organise_do", handle=-1, addon=key, catalog="movie/trending", do="hub")
    registry = common.get_registry()
    assert registry.hubs()[-1] == "c2" and [c.id for _, c in registry.hub_catalogs("c2")] == ["trending"]

    monkeypatch.setattr(xbmcgui.Dialog, "input", lambda self, heading, defaultt="", **kw: "Films")
    call("hub_rename", handle=-1, hub="movies")
    monkeypatch.setattr(xbmcgui.Dialog, "yesno", lambda self, heading, message, **kw: True)
    call("hub_remove", handle=-1, hub="c2")
    call("hub_remove", handle=-1, hub="movies")            # ignored: built-in
    registry = common.get_registry()
    assert registry.hub_name("movies") == "Films" and "c2" not in registry.hubs()
    assert [c.id for _, c in registry.hub_catalogs("movies")] == ["trending", "popular"]

    labels = []
    monkeypatch.setattr(xbmcgui.ListItem, "__init__", lambda self, label="", *a, **k: labels.append(label))
    call("root")
    assert "Films" in labels


def test_continue_watching_has_descriptions(server, listing, settings, monkeypatch):
    install(server)
    server.routes["/meta/movie/tt1.json"] = {"meta": {"id": "tt1", "type": "movie", "name": "Movie",
                                                      "description": "A movie plot.", "logo": "http://logo"}}
    server.routes["/meta/series/tt5.json"] = {"meta": {
        "id": "tt5", "type": "series", "name": "Show", "description": "The show.", "videos": [
            {"id": "tt5:1:1", "season": 1, "episode": 1, "title": "One", "overview": "Episode one plot."},
            {"id": "tt5:1:2", "season": 1, "episode": 2, "title": "Two"}]}}
    state = common.get_watchstate()
    state.record(PlaybackEntry(video_id="tt1", type="movie", title="Movie"), 1200, 6000)
    state.record(PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1,
                               title="One", show_title="Show"), 600, 3000)
    state.record(PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2,
                               title="Two", show_title="Show"), 600, 3000)
    plots, arts = [], []
    monkeypatch.setattr(xbmc.InfoTagVideo, "setPlot", lambda self, plot: plots.append(plot))
    monkeypatch.setattr(xbmcgui.ListItem, "setArt", lambda self, art: arts.append(art))
    call("continue")
    # Newest first; an episode without its own overview gets the show's description.
    assert plots == ["The show.", "Episode one plot.", "A movie plot."]
    assert arts[-1]["clearlogo"] == "http://logo"


def test_removed_episode_does_not_come_back_as_next_up(server, listing, settings, monkeypatch):
    """Ted Lasso: with next episodes in Continue Watching, removing the part-watched
    episode used to leave the show there as its Next Up entry."""
    install(server)
    server.routes["/meta/series/tt5.json"] = {"meta": {"id": "tt5", "type": "series", "name": "Show", "videos": [
        {"id": "tt5:1:1", "season": 1, "episode": 1, "title": "One"},
        {"id": "tt5:1:2", "season": 1, "episode": 2, "title": "Two"},
        {"id": "tt5:1:3", "season": 1, "episode": 3, "title": "Three"}]}}
    settings["continue_with_next_up"] = True
    state = common.get_watchstate()
    state.set_watched([PlaybackEntry(video_id="tt5:1:1", type="series", meta_id="tt5", season=1, episode=1)], True)
    state.record(PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2), 600, 3000)
    items, _ = listing
    call("continue")
    assert [p["id"] for p, _ in items] == ["tt5:1:2"]

    call("clear_resume", handle=-1, id="tt5:1:2")
    items.clear()
    call("continue")
    assert items == []
    call("next_up")
    assert items == []

    # Watching on brings it back; Next Up entries can be removed on their own too.
    state.set_watched([PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2)], True)
    call("next_up")
    assert [p["id"] for p, _ in items] == ["tt5:1:3"]
    call("dismiss_show", handle=-1, id="tt5")
    items.clear()
    call("next_up")
    call("continue")
    assert items == []


def test_show_playable_streams_with_autoplay(server, playback, settings, monkeypatch, listing):
    menus = []
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems", lambda self, entries: menus.extend(entries))
    server.routes["/catalog/movie/top.json"] = {"metas": [{"id": "tt1", "type": "movie", "name": "M"}]}
    addon = common.get_registry().all()[0]
    call("catalog", addon=addon.key, type="movie", id="top")
    assert not any("pick=1" in cmd for _, cmd in menus)              # autoplay off: the picker shows anyway

    settings["autoplay"] = True
    menus.clear()
    call("catalog", addon=addon.key, type="movie", id="top")
    picks = [cmd for _, cmd in menus if "pick=1" in cmd]
    assert picks == [f"PlayMedia({BASE}?action=play&type=movie&id=tt1&pick=1)"]

    server.routes["/s/stream/movie/tt1.json"] = streams(
        {"url": "https://cdn/720.mkv", "name": "720p"}, {"url": "https://cdn/1080.mkv", "name": "1080p"})
    playback["choice"] = 1
    call("play", type="movie", id="tt1", pick=1)                     # autoplay on, but we choose
    assert len(playback["picker"]) == 1 and playback["probed"] == []
    assert resolved_path(playback)[1] == "https://cdn/720.mkv"

    # Part-watched: asks "Resume from…" first (Kodi doesn't for context-menu playback).
    common.get_watchstate().record(PlaybackEntry(video_id="tt1", type="movie"), 600, 6000)
    asked = []
    monkeypatch.setattr(xbmcgui.Dialog, "contextmenu", lambda self, options: asked.append(options) or -1)
    playback["resolved"].clear()
    call("play", type="movie", id="tt1", pick=1)                     # backed out
    assert len(asked) == 1 and len(asked[0]) == 2                  # Resume from 10:00 / from beginning
    assert [ok for ok, _ in playback["resolved"]] == [False] and len(playback["picker"]) == 1
    # Already asked by Extended info (resume=0: from the beginning): no second question.
    asked.clear()
    call("play", type="movie", id="tt1", pick=1, resume=0)
    assert asked == [] and len(playback["picker"]) == 2
