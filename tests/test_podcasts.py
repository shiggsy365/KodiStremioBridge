"""Podcasts (plugin.audio.shiggsy365.podcasts): Apple, feeds, local state and gPodder sync."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin.audio.shiggsy365.podcasts", "resources", "lib"))

from podcasts import PodcastError, apple, feed  # noqa: E402
from podcasts.gpodder import GPODDER, NEXTCLOUD, Client, from_iso, iso  # noqa: E402
from podcasts.library import Library  # noqa: E402
from podcasts.models import Episode, Podcast  # noqa: E402
from podcasts.store import PLAYED, RESUME, Store, find, is_played  # noqa: E402
from podcasts.sync import sync  # noqa: E402

DAY = 86400
NOW = 1_790_000_000  # Sept 2026

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>The Example Show</title>
  <itunes:author>Example Media</itunes:author>
  <itunes:image href="https://img.example/show.jpg"/>
  <description>A show &amp; more</description>
  <item>
    <title>Episode 1</title>
    <guid isPermaLink="false">ep-1</guid>
    <pubDate>Mon, 07 Sep 2026 06:00:00 +0000</pubDate>
    <itunes:duration>45:30</itunes:duration>
    <enclosure url="https://cdn.example/ep1.mp3" type="audio/mpeg" length="1"/>
  </item>
  <item>
    <title><![CDATA[Episode <b>2</b>]]></title>
    <guid>ep-2</guid>
    <pubDate>Mon, 14 Sep 2026 06:00:00 +0000</pubDate>
    <itunes:duration>3725</itunes:duration>
    <itunes:image href="https://img.example/ep2.jpg"/>
    <content:encoded><![CDATA[<p>Line one</p><p>Line&nbsp;two</p>]]></content:encoded>
    <enclosure url="https://cdn.example/ep2.mp3" type="audio/mpeg" length="1"/>
  </item>
  <item>
    <title>Trailer with no audio</title>
    <guid>no-audio</guid>
  </item>
</channel>
</rss>"""

FEED = "https://feeds.example/show.xml"


# ---------------------------------------------------------------- feeds


def test_parse_rss_newest_first_skipping_items_without_audio():
    podcast, episodes = feed.parse(FEED, RSS)
    assert podcast.title == "The Example Show"
    assert podcast.author == "Example Media"
    assert podcast.image == "https://img.example/show.jpg"
    assert podcast.description == "A show & more"
    assert [e.guid for e in episodes] == ["ep-2", "ep-1"]
    newest, oldest = episodes
    assert newest.title == "Episode 2"
    assert newest.duration == 3725 and oldest.duration == 45 * 60 + 30
    assert newest.image == "https://img.example/ep2.jpg"
    assert oldest.image == "https://img.example/show.jpg"  # falls back to the show's artwork
    assert newest.description == "Line one\nLine two"
    assert newest.published - oldest.published == 7 * DAY
    assert newest.podcast_title == "The Example Show"
    assert newest.mime == "audio/mpeg"


def test_parse_atom_feed():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Cast</title>
      <entry><title>One</title><id>urn:1</id><updated>2026-09-01T10:00:00Z</updated>
        <link rel="enclosure" type="audio/mpeg" href="https://a.example/1.mp3"/></entry></feed>"""
    podcast, episodes = feed.parse(FEED, atom)
    assert podcast.title == "Atom Cast"
    assert [(e.guid, e.url) for e in episodes] == [("urn:1", "https://a.example/1.mp3")]
    assert episodes[0].published == from_iso("2026-09-01T10:00:00")


def test_episode_key_falls_back_to_audio_url():
    rss = b"""<rss><channel><title>T</title><item><title>x</title>
      <enclosure url="https://a.example/x.mp3"/></item></channel></rss>"""
    _, (episode,) = feed.parse(FEED, rss)
    assert episode.guid == "" and episode.key == "https://a.example/x.mp3"


def test_parse_rejects_broken_xml():
    with pytest.raises(PodcastError):
        feed.parse(FEED, b"<rss><channel>")


@pytest.mark.parametrize("value, seconds", [("90", 90), ("1:30", 90), ("1:00:05", 3605), ("", 0), ("soon", 0),
                                            ("1:2:3:4", 0), ("812.5", 812)])
def test_parse_duration(value, seconds):
    assert feed.parse_duration(value) == seconds


def test_parse_date_formats():
    assert feed.parse_date("Mon, 14 Sep 2026 06:00:00 +0000") == feed.parse_date("2026-09-14T06:00:00Z")
    assert feed.parse_date("Mon, 14 Sep 2026 07:00:00 +0100") == feed.parse_date("2026-09-14T06:00:00+00:00")
    assert feed.parse_date("whenever") == 0


# ---------------------------------------------------------------- Apple


class Recorder:
    """Stands in for podcasts.http.get_json: canned responses by URL substring."""

    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        for fragment, body in self.routes.items():
            if fragment in url:
                return body
        raise PodcastError(f"no route for {url}")


def test_artwork_upsizes_apple_images():
    assert apple.artwork("https://is1.example/a/100x100bb.jpg") == "https://is1.example/a/600x600bb.jpg"
    assert apple.artwork("https://is1.example/a/170x170bb.png") == "https://is1.example/a/600x600bb.png"
    assert apple.artwork("") == ""


def test_search_lookup_and_charts(monkeypatch, tmp_path):
    lookup_result = {"collectionId": 11, "kind": "podcast", "collectionName": "Show A", "artistName": "A Ltd",
                     "feedUrl": "https://a.example/feed", "artworkUrl600": "https://img/a600.jpg",
                     "primaryGenreName": "History"}
    genre_entry = {"feed": {"entry": {
        "im:name": {"label": "Show B"}, "im:artist": {"label": "B Ltd"},
        "im:image": [{"label": "https://img/b/55x55bb.png"}, {"label": "https://img/b/170x170bb.png"}],
        "id": {"attributes": {"im:id": "22"}}, "category": {"attributes": {"label": "Comedy"}}}}}
    recorder = Recorder({
        "/search?": {"results": [lookup_result, {"collectionId": 12, "kind": "podcast-episode"}]},
        "/lookup?": {"results": [lookup_result]},
        "/podcasts/top/": {"feed": {"results": [
            {"id": "11", "name": "Show A", "artistName": "A Ltd", "artworkUrl100": "https://img/x/100x100bb.jpg",
             "genres": [{"name": "History"}, {"name": "Podcasts"}]}]}},
        "/rss/toppodcasts/": genre_entry,
    })
    monkeypatch.setattr(apple.http, "get_json", recorder)
    store = Store(str(tmp_path / "p.db"), clock=lambda: NOW)
    directory = apple.Directory(country="GB", cache=store)

    (found,) = directory.search("history")
    assert (found.title, found.feed_url, found.apple_id, found.genre) == ("Show A", "https://a.example/feed", "11", "History")
    assert "country=GB" in recorder.urls[0] and "term=history" in recorder.urls[0]

    assert [p.apple_id for p in directory.lookup(["99", "11", "not-an-id"])] == ["11"]

    (top,) = directory.top()  # the RSS chart first...
    assert (top.title, top.apple_id, top.feed_url) == ("Show B", "22", "")
    assert recorder.urls[-1].endswith("/gb/rss/toppodcasts/limit=100/json")

    del recorder.routes["/rss/toppodcasts/"]
    (top,) = apple.Directory(country="GB").top()  # ...Apple's current chart if that fails
    assert (top.apple_id, top.feed_url, top.genre) == ("11", "", "History")
    assert top.image == "https://img/x/600x600bb.jpg"
    assert "/api/v2/gb/podcasts/top/" in recorder.urls[-1]
    recorder.routes["/rss/toppodcasts/"] = genre_entry

    (chart,) = directory.genre_chart(1303)  # a single entry comes as an object, not a list
    assert (chart.title, chart.author, chart.apple_id, chart.genre) == ("Show B", "B Ltd", "22", "Comedy")
    assert chart.image == "https://img/b/600x600bb.png"
    assert "/gb/rss/toppodcasts/limit=100/genre=1303/json" in recorder.urls[-1]

    calls = len(recorder.urls)
    directory.genre_chart(1303)
    assert len(recorder.urls) == calls  # cached


# ---------------------------------------------------------------- store


@pytest.fixture
def clock():
    class Clock:
        now = NOW

        def __call__(self):
            return self.now
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return Store(str(tmp_path / "podcasts.db"), clock=clock)


def ep(guid="ep-2", url="https://cdn.example/ep2.mp3", duration=3600, published=NOW - DAY, feed_url=FEED):
    return Episode(feed_url=feed_url, guid=guid, title=f"Title {guid}", url=url, duration=duration,
                   published=published, podcast_title="The Example Show")


def test_is_played():
    assert is_played(3500, 3600) and is_played(3420, 3600)
    assert not is_played(3000, 3600)
    assert is_played(100, 150)  # less than a minute left
    assert not is_played(100, 0)


def test_record_resume_played_and_short(store):
    assert store.record(ep(), 10, 3600) is None
    assert store.progress(FEED, "ep-2") is None
    assert store.record(ep(), 600, 3600) == RESUME
    row = store.progress(FEED, "ep-2")
    assert (row["position"], row["played"], row["dirty"]) == (600, 0, 1)
    assert store.record(ep(), 3550, 3600) == PLAYED
    row = store.progress(FEED, "ep-2")
    assert (row["position"], row["played"]) == (3600, 1)
    assert store.in_progress() == []


def test_set_played_and_unplayed(store):
    store.record(ep(), 600, 3600)
    store.set_played(ep(), False)
    row = store.progress(FEED, "ep-2")
    assert (row["position"], row["played"]) == (0, 0)
    store.set_played(ep(), True)
    assert store.progress(FEED, "ep-2")["played"] == 1


def test_subscriptions_and_pending_changes(store):
    store.subscribe(Podcast(title="A", feed_url="https://a/feed", apple_id="1"))
    store.subscribe(Podcast(title="B", feed_url="https://b/feed"))
    store.unsubscribe("https://b/feed")
    assert [s["feed_url"] for s in store.subscriptions()] == ["https://a/feed"]
    assert store.pending_subscriptions() == (["https://a/feed"], ["https://b/feed"])
    store.subscriptions_pushed(["https://a/feed"], ["https://b/feed"])
    assert store.pending_subscriptions() == ([], [])
    assert store.is_subscribed("https://a/feed") and not store.is_subscribed("https://b/feed")
    # Details from the feed don't wipe what's there with blanks
    store.update_details(Podcast(title="", feed_url="https://a/feed", image="https://img/a.jpg"))
    (row,) = store.subscriptions()
    assert (row["title"], row["image"], row["apple_id"]) == ("A", "https://img/a.jpg", "1")


def test_apply_remote_newer_wins(store, clock):
    store.record(ep(), 600, 3600)  # updated = NOW
    assert not store.apply_remote(FEED, "https://cdn.example/ep2.mp3", "ep-2", 900, 3600, NOW - 10)
    assert store.progress(FEED, "ep-2")["position"] == 600
    assert store.apply_remote(FEED, "https://cdn.example/ep2.mp3", "", 900, 3600, NOW + 10)  # matched by URL
    row = store.progress(FEED, "ep-2")
    assert (row["position"], row["dirty"], row["updated"]) == (900, 0, NOW + 10)
    assert store.apply_remote(FEED, "https://cdn.example/ep2.mp3", "ep-2", 3590, 3600, NOW + 20)
    assert store.progress(FEED, "ep-2")["played"] == 1


def test_remote_episode_without_guid_then_local_play_shares_a_row(store, clock):
    store.apply_remote(FEED, "https://cdn.example/ep2.mp3", "", 300, 3600, NOW - 50)
    clock.now = NOW + 100
    store.record(ep(), 700, 3600)
    rows = store.progress_for(FEED)
    assert len(rows) == 1
    (row,) = rows.values()
    assert row["position"] == 700
    assert find(rows, ep())["position"] == 700


def test_cache_expiry(store, clock):
    store.set("k", {"a": 1}, 60)
    assert store.get("k") == {"a": 1}
    clock.now += 61
    assert store.get("k") is None


# ---------------------------------------------------------------- library


class Feeds:
    def __init__(self, feeds):
        self.feeds, self.calls = feeds, []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.feeds:
            raise PodcastError("404")
        return self.feeds[url]


def show(feed_url, title, published_days_ago):
    podcast = Podcast(title=title, feed_url=feed_url, image=f"{feed_url}/art.jpg")
    episodes = [Episode(feed_url=feed_url, guid=f"{title}-{d}", title=f"{title} {d}", url=f"{feed_url}/{d}.mp3",
                        published=NOW - d * DAY, duration=1800, podcast_title=title)
                for d in published_days_ago]
    return podcast, episodes


def test_latest_in_progress_first_then_newest_per_subscription(store, clock):
    feeds = Feeds({
        "https://a/feed": show("https://a/feed", "A", [1, 8, 15]),
        "https://b/feed": show("https://b/feed", "B", [3, 10]),
        "https://c/feed": show("https://c/feed", "C", [90]),        # never played, nothing recent
        "https://d/feed": show("https://d/feed", "D", [2, 400]),    # played the newest: nothing new
    })
    library = Library(store, directory=None, feed_ttl=0, log=lambda m: None, fetch_feed=feeds)
    for url in feeds.feeds:
        store.subscribe(Podcast(title="", feed_url=url), dirty=False)
    store.subscribe(Podcast(title="", feed_url="https://gone/feed"), dirty=False)  # broken feed: skipped

    a_old = feeds.feeds["https://a/feed"][1][1]
    store.record(a_old, 300, 1800)                                  # A: part-way through the 8-day-old one
    store.set_played(feeds.feeds["https://d/feed"][1][0], True)

    entries = library.latest(NOW)
    assert [(e.title, bool(row)) for e, row in entries] == [("A 8", True), ("A 1", False), ("B 3", False)]


def test_latest_respects_last_played_even_if_old(store):
    feeds = Feeds({"https://a/feed": show("https://a/feed", "A", [60, 120, 200])})
    library = Library(store, directory=None, feed_ttl=0, fetch_feed=feeds)
    store.subscribe(Podcast(title="", feed_url="https://a/feed"), dirty=False)
    store.set_played(feeds.feeds["https://a/feed"][1][1], True)     # played the 120-day-old one
    assert [e.title for e, _ in library.latest(NOW)] == ["A 60"]


def test_my_podcasts_fills_in_synced_bare_urls(store):
    feeds = Feeds({"https://a/feed": show("https://a/feed", "A", [1])})
    library = Library(store, directory=None, feed_ttl=600, fetch_feed=feeds)
    store.subscribe(Podcast(title="", feed_url="https://a/feed"), dirty=False)
    (podcast,) = library.my_podcasts()
    assert (podcast.title, podcast.image) == ("A", "https://a/feed/art.jpg")
    library.my_podcasts()
    assert feeds.calls == ["https://a/feed"]  # details kept; no second fetch


def test_episode_lookup_refreshes_a_stale_feed(store):
    feeds = Feeds({"https://a/feed": show("https://a/feed", "A", [5])})
    library = Library(store, directory=None, feed_ttl=600, fetch_feed=feeds)
    library.feed("https://a/feed")
    feeds.feeds["https://a/feed"] = show("https://a/feed", "A", [0, 5])
    assert library.episode("https://a/feed", "A-0").title == "A 0"
    with pytest.raises(PodcastError):
        library.episode("https://a/feed", "A-99")


# ---------------------------------------------------------------- gPodder


class FakeGpodder:
    """A tiny gPodder server: the v2 API and the Nextcloud app's API, sharing one state."""

    def __init__(self, user="me", password="secret"):
        self.user, self.password = user, password
        self.subs, self.sub_log, self.actions, self.clock = set(), [], [], 1000
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, body=None):
                raw = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null") if length else None
                parts = urlsplit(self.path)
                owner.requests.append((method, parts.path))
                import base64
                expected = "Basic " + base64.b64encode(f"{owner.user}:{owner.password}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    return self._reply(401)
                since = int((parse_qs(parts.query).get("since") or ["0"])[0])
                status, reply = owner.route(method, parts.path, body, since)
                self._reply(status, reply)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tick(self):
        self.clock += 1
        return self.clock

    def route(self, method, path, body, since):
        user = self.user
        subs_paths = (f"/api/2/subscriptions/{user}/kodi.json", "/index.php/apps/gpoddersync/subscriptions",
                      "/index.php/apps/gpoddersync/subscription_change/create")
        action_paths = (f"/api/2/episodes/{user}.json", "/index.php/apps/gpoddersync/episode_action",
                        "/index.php/apps/gpoddersync/episode_action/create")
        if path in (f"/api/2/auth/{user}/login.json", f"/api/2/devices/{user}/kodi.json"):
            return 200, {}
        if path in subs_paths and method == "POST":
            stamp = self.tick()
            for url in body.get("add", []):
                self.subs.add(url)
                self.sub_log.append((stamp, "add", url))
            for url in body.get("remove", []):
                self.subs.discard(url)
                self.sub_log.append((stamp, "remove", url))
            return 200, {"timestamp": stamp, "update_urls": []}
        if path in subs_paths:
            changes = {}
            for stamp, kind, url in self.sub_log:
                if stamp > since:
                    changes[url] = kind
            return 200, {"add": [u for u, k in changes.items() if k == "add"],
                         "remove": [u for u, k in changes.items() if k == "remove"], "timestamp": self.clock}
        if path in action_paths and method == "POST":
            stamp = self.tick()
            self.actions += [(stamp, a) for a in body]
            return 200, {"timestamp": stamp}
        if path in action_paths:
            return 200, {"actions": [a for stamp, a in self.actions if stamp > since], "timestamp": self.clock}
        return 404, {}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def gpodder_server():
    server = FakeGpodder()
    yield server
    server.close()


def test_iso_round_trip():
    assert iso(0) == "1970-01-01T00:00:00"
    assert from_iso(iso(NOW)) == NOW
    assert from_iso("2026-09-01T10:00:00Z") == from_iso("2026-09-01T10:00:00")
    assert from_iso("garbage") == 0


def test_client_needs_server_and_user():
    with pytest.raises(PodcastError):
        Client("", "me", "x")


def test_bad_password_is_reported(gpodder_server):
    client = Client(gpodder_server.url, "me", "wrong")
    with pytest.raises(PodcastError, match="401"):
        client.get_subscriptions()


@pytest.mark.parametrize("flavour", [GPODDER, NEXTCLOUD])
def test_two_devices_share_subscriptions_and_positions(tmp_path, gpodder_server, flavour):
    pc = Store(str(tmp_path / "pc.db"), clock=lambda: NOW)
    stick_clock = type("C", (), {"now": NOW + 60, "__call__": lambda self: self.now})()
    stick = Store(str(tmp_path / "stick.db"), clock=stick_clock)

    def client():
        return Client(gpodder_server.url, "me", "secret", device="kodi", flavour=flavour)

    pc.subscribe(Podcast(title="Show", feed_url=FEED))
    pc.record(ep(), 600, 3600)
    summary = sync(pc, client())
    assert summary["subscriptions_sent"] == 1 and summary["positions_sent"] == 1
    assert pc.pending_subscriptions() == ([], []) and pc.dirty_progress() == []
    (action,) = [a for _, a in gpodder_server.actions]
    assert action["action"] == ("play" if flavour == GPODDER else "PLAY")
    assert (action["podcast"], action["episode"], action["guid"], action["position"], action["total"]) == \
        (FEED, "https://cdn.example/ep2.mp3", "ep-2", 600, 3600)
    assert (action.get("device") == "kodi") == (flavour == GPODDER)

    summary = sync(stick, client())
    assert summary["subscriptions_received"] == 1 and summary["positions_received"] == 1
    assert stick.is_subscribed(FEED)
    assert stick.progress(FEED, "ep-2")["position"] == 600

    # The stick carries on listening; the PC picks it up, and its own older position loses.
    stick.record(ep(), 1500, 3600)
    stick.unsubscribe(FEED)
    sync(stick, client())
    summary = sync(pc, client())
    assert pc.progress(FEED, "ep-2")["position"] == 1500
    assert not pc.is_subscribed(FEED)
    assert summary["positions_received"] == 1

    # Nothing new: a second sync sends and receives nothing.
    assert sync(pc, client()) == {"subscriptions_sent": 0, "subscriptions_received": 0,
                                  "positions_sent": 0, "positions_received": 0}


def test_sync_ignores_other_action_kinds_and_bad_totals(tmp_path, gpodder_server):
    gpodder_server.actions = [(5, {"podcast": FEED, "episode": "https://cdn.example/x.mp3", "action": "download",
                                   "timestamp": iso(NOW)}),
                              (6, {"podcast": FEED, "episode": "https://cdn.example/y.mp3", "action": "play",
                                   "timestamp": iso(NOW), "position": 120, "total": -1})]
    store = Store(str(tmp_path / "s.db"), clock=lambda: NOW)
    summary = sync(store, Client(gpodder_server.url, "me", "secret"))
    assert summary["positions_received"] == 1
    (row,) = store.progress_for(FEED).values()
    assert (row["url"], row["position"], row["played"]) == ("https://cdn.example/y.mp3", 120, 0)
