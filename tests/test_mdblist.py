import json

import pytest

from mdblist import (
    MDBListAuthError, MDBListError, MDBListClient, parse_watched, scrobble_payload, stremio_id, watched_payload,
)
from stremio.watchstate import PlaybackEntry

MOVIE = PlaybackEntry(video_id="tt1", type="movie", ids={"imdb": "tt1", "tmdb": 550, "mal": 5})
EPISODE = PlaybackEntry(video_id="tt5:1:2", type="series", meta_id="tt5", season=1, episode=2,
                        ids={"imdb": "tt5", "tvdb": 81189})


def test_scrobble_payloads():
    assert scrobble_payload(MOVIE, 42.123, "0.6.0") == {
        "movie": {"ids": {"imdb": "tt1", "tmdb": 550}}, "progress": 42.12, "app_version": "0.6.0"}
    assert scrobble_payload(EPISODE, 120) == {
        "show": {"ids": {"imdb": "tt5", "tvdb": 81189}, "season": {"number": 1, "episode": {"number": 2}}},
        "progress": 100.0}
    assert scrobble_payload(PlaybackEntry(video_id="yt:x", type="channel"), 10) is None


def test_watched_payloads():
    ep3 = PlaybackEntry(video_id="tt5:1:3", type="series", meta_id="tt5", season=1, episode=3, ids={"imdb": "tt5"})
    body = watched_payload([MOVIE, EPISODE, ep3, PlaybackEntry(video_id="x", type="movie")], remove=True)
    assert body == {"movies": [{"ids": {"imdb": "tt1", "tmdb": 550}}],
                    "shows": [{"ids": {"imdb": "tt5", "tvdb": 81189}, "seasons": [{"number": 1, "episodes": [{"number": 2}]}]},
                              {"ids": {"imdb": "tt5"}, "seasons": [{"number": 1, "episodes": [{"number": 3}]}]}]}
    stamped = watched_payload([MOVIE], watched_at=1_700_000_000)
    assert stamped["movies"][0]["watched_at"] == "2023-11-14T22:13:20Z"


def test_parse_watched():
    data = {
        "movies": [{"movie": {"title": "M", "ids": {"imdb": "tt1", "tmdb": 550}}, "last_watched_at": "2024-01-02T03:04:05Z"},
                   {"movie": {"ids": {"tmdb": 77}}}, {"movie": {"ids": {}}}],
        "episodes": [{"episode": {"season": 1, "number": 2, "title": "E", "show": {"title": "S", "ids": {"imdb": "tt5"}}},
                      "last_watched_at": "2024-01-02T03:04:05.123Z"},
                     {"episode": {"season": None, "number": 2, "show": {"ids": {"imdb": "tt5"}}}}],
    }
    items = parse_watched(data)
    assert [(e.video_id, e.type, e.meta_id, round(t)) for e, t in items] == [
        ("tt1", "movie", "", 1704164645), ("tmdb:77", "movie", "", 0), ("tt5:1:2", "series", "tt5", 1704164645)]
    assert stremio_id({"tvdb": 3}) == "tvdb:3" and stremio_id({}) is None


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append((method, url, params, json))
        return self.responses.pop(0)


def test_client_paginates_and_authenticates():
    session = FakeSession([
        FakeResponse(200, {"movies": [{"movie": {"ids": {"imdb": "tt1"}}}], "pagination": {"next_cursor": "c2"}}),
        FakeResponse(200, {"movies": [{"movie": {"ids": {"imdb": "tt2"}}}], "episodes": [], "pagination": {}}),
    ])
    data = MDBListClient("KEY", session=session).watched()
    assert [m["movie"]["ids"]["imdb"] for m in data["movies"]] == ["tt1", "tt2"]
    assert session.calls[0][1] == "https://api.mdblist.com/sync/watched"
    assert session.calls[0][2]["apikey"] == "KEY" and "cursor" not in session.calls[0][2]
    assert session.calls[1][2]["cursor"] == "c2"

    with pytest.raises(MDBListAuthError):
        MDBListClient("BAD", session=FakeSession([FakeResponse(401, {"error": "x"})])).last_activities()


def test_client_scrobble_and_batches():
    session = FakeSession([FakeResponse(201, {})] * 5)
    client = MDBListClient("K", session=session)
    client.scrobble("stop", {"movie": {"ids": {"imdb": "tt1"}}, "progress": 95})
    movies = [PlaybackEntry(video_id=f"tt{i}", type="movie", ids={"imdb": f"tt{i}"}) for i in range(150)]
    client.add_watched(movies, watched_at=1_700_000_000)
    client.remove_watched(movies[:3])
    assert [c[1].rsplit("/", 2)[-2:] for c in session.calls] == [
        ["scrobble", "stop"], ["sync", "watched"], ["sync", "watched"], ["watched", "remove"]]
    assert len(session.calls[1][3]["movies"]) == 100 and len(session.calls[2][3]["movies"]) == 50
    with pytest.raises(ValueError):
        client.scrobble("checkin", {})


def test_per_item_watched_at():
    from stremio.watchstate import Row

    row = Row(video_id="tt1", type="movie", ids={"imdb": "tt1"}, watched_at=1_700_000_000)
    assert watched_payload([row], watched_at=5)["movies"][0]["watched_at"] == "2023-11-14T22:13:20Z"
    assert "watched_at" not in watched_payload([row], remove=True)["movies"][0]


class FakeClient:
    def __init__(self, remote, activities):
        self.remote, self.activities, self.added = remote, activities, []

    def add_watched(self, entries, watched_at=None):
        self.added += [e.video_id for e in entries]

    def last_activities(self):
        return self.activities

    def watched(self):
        return self.remote

    def playback(self):
        return getattr(self, "sessions", [])


def test_sync_pushes_then_pulls_only_on_change(tmp_path):
    from mdblist import sync
    from stremio.watchstate import WatchState

    state = WatchState(str(tmp_path / "w.db"))
    state.set_watched([MOVIE, PlaybackEntry(video_id="yt:1", type="channel")], True)
    remote = {"episodes": [{"episode": {"season": 1, "number": 2, "show": {"ids": {"imdb": "tt5"}}}}]}
    client = FakeClient(remote, {"server_time": "t1", "watched_at": "a"})

    summary, activities = sync(client, state)
    assert client.added == ["tt1"] and summary["pushed"] == 1 and summary["pulled"]
    assert state.watched_episodes("tt5") == {(1, 2)}
    assert state.get("tt1").watched                  # just pushed: not removed though the pull lacks it
    assert state.unsynced_watched() == []            # the id-less channel won't be retried forever

    client.activities = {"server_time": "t2", "watched_at": "a"}   # only the clock moved
    summary, _ = sync(client, state, activities)
    assert not summary["pulled"] and summary["pushed"] == 0
    client.activities = {"server_time": "t3", "watched_at": "b"}
    assert sync(client, state, activities)[0]["pulled"]


def test_recommendations():
    from mdblist import _lookup_path, parse_recommendations

    assert _lookup_path("movie", "tt0111161") == "/imdb/movie/tt0111161"
    assert _lookup_path("series", "tt0903747:1:2") == "/imdb/show/tt0903747"
    assert _lookup_path("series", "tmdb:1396") == "/tmdb/show/1396"
    assert _lookup_path("channel", "yt:abc") is None
    items = parse_recommendations({"recommendations": [
        {"mediatype": "movie", "title": "The Green Mile", "ids": {"imdb": "tt0120689", "tmdb": 497},
         "poster": "http://p", "release_year": 1999},
        {"mediatype": "show", "title": "Better Call Saul", "ids": {"tmdb": 60059}},
        {"mediatype": "movie", "title": "No ids", "ids": {}},
    ]})
    assert items == [
        {"id": "tt0120689", "type": "movie", "name": "The Green Mile", "poster": "http://p", "releaseInfo": "1999",
         "description": ""},
        {"id": "tmdb:60059", "type": "series", "name": "Better Call Saul", "poster": "", "releaseInfo": "",
         "description": ""},
    ]
    session = FakeSession([FakeResponse(200, {"recommendations": []})])
    assert MDBListClient("K", session=session).recommendations("movie", "tt1") == []
    assert session.calls[0][2]["append_to_response"] == "recommendations"


def test_overall_rating():
    from mdblist import overall_rating

    shawshank = {"score": 89, "ratings": [          # real MDBList values
        {"source": "imdb", "value": 9.3}, {"source": "metacritic", "value": 82},
        {"source": "metacriticuser", "value": 9.2}, {"source": "trakt", "value": 91},
        {"source": "tomatoes", "value": 89}, {"source": "popcorn", "value": 98}, {"source": "tmdb", "value": 87},
        {"source": "letterboxd", "value": 4.6}, {"source": "rogerebert", "value": 3.5},
        {"source": "myanimelist", "value": None}]}
    score, count = overall_rating(shawshank)
    # 89, 93, 82, 91, 89, 98, 87, 92, 87.5 -> 89.8 (metacriticuser and empty MAL ignored)
    assert count == 9 and round(score, 1) == 89.8
    assert overall_rating({"score": 0, "ratings": [{"source": "imdb", "value": 0}]}) is None
    assert overall_rating({}) is None
    assert overall_rating({"ratings": [{"source": "letterboxd", "value": 5}]}) == (100.0, 1)


def test_item_lookups_are_cached(tmp_path):
    from stremio.cache import Cache

    session = FakeSession([FakeResponse(200, {"score": 1, "recommendations": []})])
    client = MDBListClient("K", session=session, cache=Cache(str(tmp_path / "c.db")))
    assert client.item("movie", "tt1")["score"] == 1
    assert client.recommendations("movie", "tt1") == []          # served from cache
    assert len(session.calls) == 1
    assert client.item("channel", "yt:x") == {}



def test_parse_playback_and_merge_resume(tmp_path):
    from mdblist import parse_playback
    from stremio.watchstate import WatchState

    sessions = [  # shapes as returned by MDBList
        {"progress": "15.11", "runtime": 97, "updated_at_ts": 2_000_000_000, "type": "movie",
         "movie": {"title": "Runner", "ids": {"imdb": "tt31349844", "tmdb": 1377237}}},
        {"progress": "0.08", "runtime": 111, "updated_at_ts": 2_000_000_000, "type": "movie",
         "movie": {"ids": {"imdb": "tt28014327"}}},                       # 5 seconds: too short
        {"progress": "40", "runtime": 50, "updated_at_ts": 2_000_000_000, "type": "episode",
         "episode": {"season": 1, "number": 3}, "show": {"title": "S", "ids": {"imdb": "tt5"}}},
        {"progress": "30", "runtime": 0, "type": "movie", "movie": {"ids": {"imdb": "tt9"}}},   # no runtime
    ]
    items = parse_playback(sessions)
    assert [(e.video_id, round(p), d) for e, p, d, _ in items] == [
        ("tt31349844", 879, 5820.0), ("tt28014327", 5, 6660.0), ("tt5:1:3", 1200, 3000.0)]

    state = WatchState(str(tmp_path / "w.db"), clock=lambda: 1_000_000_000)
    assert state.merge_resume(items) == 2
    assert round(state.get("tt31349844").position) == 879
    assert state.get("tt28014327") is None and state.get("tt5:1:3").meta_id == "tt5"

    # Older than what this device has: ignored.
    local = WatchState(str(tmp_path / "w2.db"), clock=lambda: 2_100_000_000)
    local.record(items[0][0], 3000, 5820)
    assert local.merge_resume(items[:1]) == 0 and local.get("tt31349844").position == 3000


def test_sync_pulls_resume_points(tmp_path):
    from mdblist import sync
    from stremio.watchstate import WatchState

    state = WatchState(str(tmp_path / "w.db"))
    client = FakeClient({}, {"server_time": "t"})
    client.sessions = [{"progress": "50", "runtime": 100, "updated_at_ts": 9_999_999_999, "type": "movie",
                        "movie": {"ids": {"imdb": "tt7"}}}]
    summary, _ = sync(client, state, {"server_time": "x"})
    assert summary["resumes"] == 1 and state.continue_watching()[0].video_id == "tt7"



def test_watchlist():
    session = FakeSession([
        FakeResponse(200, {"movies": [{"id": 550, "title": "Fight Club", "imdb_id": "tt0137523", "release_year": 1999,
                                       "ids": {"imdb": "tt0137523", "tmdb": 550, "mdblist": "x"}}],
                           "shows": [], "pagination": {"next_cursor": "n"}}),
        FakeResponse(200, {"movies": [{"id": 1, "title": "No ids", "imdb_id": None}],
                           "shows": [{"id": 1396, "title": "Breaking Bad", "imdb_id": "tt0903747", "tvdb_id": 81189,
                                      "release_year": 2008}], "pagination": {}}),
    ])
    items = MDBListClient("K", session=session).watchlist()
    assert items == [
        {"type": "movie", "id": "tt0137523", "title": "Fight Club", "year": 1999,
         "ids": {"imdb": "tt0137523", "tmdb": 550, "mdblist": "x"}, "poster": "", "description": ""},
        {"type": "movie", "id": "tmdb:1", "title": "No ids", "year": None, "ids": {"tmdb": 1}, "poster": "",
         "description": ""},
        {"type": "series", "id": "tt0903747", "title": "Breaking Bad", "year": 2008,
         "ids": {"imdb": "tt0903747", "tvdb": 81189, "tmdb": 1396}, "poster": "", "description": ""},
    ]
    assert session.calls[0][2]["append_to_response"] == "poster,description"
    assert session.calls[1][2]["cursor"] == "n" and session.calls[0][1].endswith("/watchlist/items")



def test_watchlist_add_remove():
    session = FakeSession([FakeResponse(200, {"added": 1}), FakeResponse(200, {"removed": 1})])
    client = MDBListClient("K", session=session)
    client.watchlist_add("movie", {"imdb": "tt1", "tmdb": 2, "mal": 3})
    client.watchlist_remove("series", {"imdb": "tt5"})
    assert [(c[1].rsplit("/", 1)[-1], c[3]) for c in session.calls] == [
        ("add", {"movies": [{"ids": {"imdb": "tt1", "tmdb": 2}}]}),
        ("remove", {"shows": [{"ids": {"imdb": "tt5"}}]})]
    with pytest.raises(MDBListError):
        client.watchlist_add("movie", {})
