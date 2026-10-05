"""Podcasts' Kodi layer, run against Kodistubs (no real Kodi needed)."""

import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

pytest.importorskip("xbmc")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin.audio.shiggsy365.podcasts", "resources", "lib"))

import xbmc  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402

from podcasts import apple, library as library_module  # noqa: E402
from podcasts.models import Episode, Podcast  # noqa: E402
from podcasts_ui import common, menus, router, service  # noqa: E402

BASE = "plugin://plugin.audio.shiggsy365.podcasts/"
FEED = "https://feeds.example/show.xml"
SETTINGS = {"country": "gb", "feed_cache_minutes": 30, "latest_days": 30, "ask_resume": True,
            "sync_enabled": False, "sync_type": 0, "sync_server": "", "sync_username": "", "sync_password": "",
            "sync_device": "kodi", "sync_minutes": 15}


def make_feed(url=FEED):
    podcast = Podcast(title="The Example Show", feed_url=url, author="Example Media", image="https://img/show.jpg")
    episodes = [Episode(feed_url=url, guid=f"ep-{n}", title=f"Episode {n}", url=f"https://cdn.example/{n}.mp3",
                        published=1_790_000_000 - n * 86400, duration=3600, podcast_title=podcast.title)
                for n in (1, 2)]
    return podcast, episodes


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "profile_dir", lambda: str(tmp_path))
    values = dict(SETTINGS)
    monkeypatch.setattr(common.ADDON, "getSettingBool", lambda key: bool(values[key]))
    monkeypatch.setattr(common.ADDON, "getSettingInt", lambda key: int(values[key]))
    monkeypatch.setattr(common.ADDON, "getSettingString", lambda key: str(values[key]))
    monkeypatch.setattr(library_module.feed, "fetch", lambda url: make_feed(url))
    monkeypatch.setattr(common, "notify", lambda *a, **k: None)
    monkeypatch.setattr(menus, "notify", lambda *a, **k: None)
    return values


@pytest.fixture
def listing(monkeypatch):
    items, ends = [], []

    def add(handle, url, item, isFolder=False, totalItems=0):
        items.append((dict(parse_qsl(urlsplit(url).query)), isFolder, item))
        return True

    monkeypatch.setattr(xbmcplugin, "addDirectoryItem", add)
    monkeypatch.setattr(xbmcplugin, "endOfDirectory", lambda handle, succeeded=True, **kw: ends.append(succeeded))
    return items, ends


def call(action, handle=1, resume=None, **params):
    argv = [BASE, str(handle), "?" + urlencode({"action": action, **params})]
    if resume is not None:
        argv.append(f"resume:{str(resume).lower()}")
    router.run(argv)


def test_root_lists_the_six_sections(listing):
    items, ends = listing
    call("root")
    assert [p["action"] for p, folder, _ in items] == ["my_podcasts", "unplayed_podcasts", "latest", "trending",
                                                       "top", "search"]
    assert all(folder for _, folder, _ in items) and ends == [True]


def test_trending_lists_apple_genres(listing):
    items, _ = listing
    call("trending")
    assert len(items) == len(apple.GENRES)
    assert items[0][0] == {"action": "genre", "id": "1301"}


def test_top_chart_items_open_by_apple_id_and_offer_subscribe(listing, monkeypatch):
    items, _ = listing
    monkeypatch.setattr(apple.Directory, "top", lambda self: [Podcast(title="Chart Show", apple_id="42")])
    call("top")
    ((params, folder, item),) = items
    assert params == {"action": "podcast", "id": "42"} and folder


def test_podcast_lists_episodes_as_playable(listing):
    items, ends = listing
    call("podcast", feed=FEED)
    assert [p["key"] for p, folder, _ in items] == ["ep-1", "ep-2"]
    assert all(p["action"] == "play" and not folder for p, folder, _ in items)
    assert ends == [True]


def test_subscribe_then_my_podcasts(listing, monkeypatch):
    items, _ = listing
    monkeypatch.setattr(apple.Directory, "lookup",
                        lambda self, ids: [Podcast(title="", feed_url=FEED, apple_id="42")])
    requests = []
    monkeypatch.setattr(menus, "request_sync", lambda: requests.append(1))
    call("subscribe", handle=-1, id="42")
    store = common.get_store()
    (sub,) = store.subscriptions()
    assert (sub["feed_url"], sub["title"], sub["apple_id"], sub["dirty"]) == (FEED, "The Example Show", "42", 1)
    assert requests == [1]
    call("my_podcasts")
    assert items[-1][0] == {"action": "podcast", "feed": FEED, "id": "42"}
    call("unsubscribe", handle=-1, id="42")
    assert store.subscriptions() == []


def test_play_resolves_and_announces_resume_point(listing, monkeypatch):
    resolved = []
    monkeypatch.setattr(xbmcplugin, "setResolvedUrl", lambda handle, ok, item: resolved.append((ok, item)))
    monkeypatch.setattr(xbmcgui.Dialog, "contextmenu", lambda self, options: 0)  # "Resume from ..."
    _, episodes = make_feed()
    common.get_store().record(episodes[0], 900, 3600)
    announced = {}
    monkeypatch.setattr(menus, "announce_playback", lambda e, offset: announced.update(episode=e, offset=offset))
    call("play", feed=FEED, key="ep-1")
    assert resolved and resolved[0][0] is True
    assert announced["episode"].url == "https://cdn.example/1.mp3" and announced["offset"] == 900


def test_mark_played_and_latest(listing):
    items, _ = listing
    call("mark", handle=-1, feed=FEED, key="ep-2", played="1")
    row = common.get_store().progress(FEED, "ep-2")
    assert row["played"] == 1 and row["dirty"] == 1


def test_tracker_saves_position_of_announced_episode(monkeypatch):
    _, episodes = make_feed()
    payload = json.dumps({"episode": episodes[0].to_dict(), "offset": 0, "time": __import__("time").time()})
    props = {common.NOW_PLAYING: payload}
    monkeypatch.setattr(xbmcgui.Window, "getProperty", lambda self, key: props.get(key, ""))
    monkeypatch.setattr(xbmcgui.Window, "clearProperty", lambda self, key: props.pop(key, None))
    monkeypatch.setattr(service, "refresh_if_showing", lambda: None)
    changes = []
    tracker = service.Tracker(on_change=lambda: changes.append(1))
    monkeypatch.setattr(tracker, "getTotalTime", lambda: 3600.0)
    tracker.onAVStarted()
    assert tracker.episode.guid == "ep-1"
    tracker.position = 1200
    tracker.onPlayBackStopped()
    assert common.get_store().progress(FEED, "ep-1")["position"] == 1200
    assert changes == [1]


@pytest.mark.parametrize("resume, choice, expected, asked", [
    (True, None, 900, False),   # Kodi asked already (its own bookmark)
    (False, 0, 900, True),      # music items: Kodi says false without asking
    (False, 1, 0, True),        # "Play from the start"
    (None, 0, 900, True),
])
def test_play_resume_question(listing, monkeypatch, resume, choice, expected, asked):
    monkeypatch.setattr(xbmcplugin, "setResolvedUrl", lambda handle, ok, item: None)
    questions = []
    monkeypatch.setattr(xbmcgui.Dialog, "contextmenu", lambda self, options: questions.append(options) or choice)
    _, episodes = make_feed()
    common.get_store().record(episodes[0], 900, 3600)
    announced = {}
    monkeypatch.setattr(menus, "announce_playback", lambda e, offset: announced.update(offset=offset))
    call("play", resume=resume, feed=FEED, key="ep-1")
    assert announced["offset"] == expected
    assert bool(questions) == asked


@pytest.fixture
def item_details(monkeypatch):
    """Labels and context menus of the ListItems made (Kodistubs keeps neither)."""
    made = []
    original = xbmcgui.ListItem.__init__

    def init(self, label="", label2="", path="", offscreen=False):
        original(self, label, label2, path, offscreen)
        made.append({"label": label, "label2": label2, "menu": []})
        self._details = made[-1]

    monkeypatch.setattr(xbmcgui.ListItem, "__init__", init)
    monkeypatch.setattr(xbmcgui.ListItem, "addContextMenuItems",
                        lambda self, items, replaceItems=False: self._details["menu"].extend(items))
    return made


def test_played_episodes_say_so_in_their_label(listing, item_details, monkeypatch):
    monkeypatch.setattr(common.ADDON, "getLocalizedString", lambda i: {30030: "Played"}.get(i, f"#{i}"))
    _, episodes = make_feed()
    common.get_store().set_played(episodes[0], True)
    call("podcast", feed=FEED)
    played, unplayed = item_details
    assert played["label2"] == "Played" and "[COLOR" in played["label"] and "Episode 1" in played["label"]
    assert "[COLOR" not in unplayed["label"] and unplayed["label2"] != "Played"


def test_go_to_podcast_works_from_widgets(listing, item_details, monkeypatch):
    monkeypatch.setattr(common.ADDON, "getLocalizedString", lambda i: f"#{i}")
    _, episodes = make_feed()
    store = common.get_store()
    store.record(episodes[0], 900, 3600)
    call("latest")
    (entry,) = [command for label, command in item_details[0]["menu"] if label == "#30024"]
    assert entry.startswith("ActivateWindow(Music,plugin://") and entry.endswith(",return)")
    assert "action=podcast" in entry


def test_widget_folders_carry_the_reload_token_and_it_is_ignored(listing, monkeypatch):
    urls = []
    monkeypatch.setattr(xbmcplugin, "addDirectoryItem",
                        lambda handle, url, item, isFolder=False, totalItems=0: urls.append(url) or True)
    call("root")
    token = "&reload=$INFO[Window(Home).Property(" + common.WIDGETS_RELOAD + ")]"
    assert [u.endswith(token) for u in urls] == [True, True, True, False, False, False]
    # The skin evaluates the token; the router drops it rather than passing it to the handler.
    _, ends = listing
    query = urls[2].split("?", 1)[1].replace(token, "&reload=1712")
    router.run([BASE, "1", "?" + query])
    assert ends == [True, True]


def test_changes_reload_widgets(listing, monkeypatch):
    bumps = []
    monkeypatch.setattr(menus, "notify_widgets", lambda: bumps.append(1))
    monkeypatch.setattr(menus, "request_sync", lambda: None)
    call("mark", handle=-1, feed=FEED, key="ep-2", played="1")
    call("unsubscribe", handle=-1, feed=FEED)
    assert bumps == [1, 1]


def test_unplayed_podcasts_lists_subscriptions_not_started(listing):
    items, ends = listing
    store = common.get_store()
    store.subscribe(Podcast(title="The Example Show", feed_url=FEED, image="https://img/show.jpg"), dirty=False)
    call("unplayed_podcasts")
    assert [p.get("feed") for p, _, _ in items] == [FEED]
    store.record(make_feed()[1][0], 900, 3600)
    call("unplayed_podcasts")
    assert len(items) == 1 and ends == [True, True]
