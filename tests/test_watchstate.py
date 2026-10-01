import pytest

from stremio.models import Meta
from stremio.watchstate import RESUME, WATCHED, PlaybackEntry, WatchState, next_episode


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def state(tmp_path, clock):
    return WatchState(str(tmp_path / "watch.db"), clock=clock)


MOVIE = PlaybackEntry(video_id="tt1", type="movie", title="Movie", ids={"imdb": "tt1"})


def ep(s, e, **kw):
    return PlaybackEntry(video_id=f"tt5:{s}:{e}", type="series", meta_id="tt5", season=s, episode=e,
                         show_title="Show", ids={"imdb": "tt5"}, **kw)


def test_record_rules(state, clock):
    assert state.record(MOVIE, 60, 6000) is None               # under 2 minutes: ignored
    assert state.get("tt1") is None
    assert state.record(MOVIE, 1200, 6000) == RESUME
    row = state.get("tt1")
    assert (row.position, row.watched, row.title, row.ids) == (1200, False, "Movie", {"imdb": "tt1"})
    assert state.record(MOVIE, 30, 6000) is None               # restarted: keep the old resume point
    assert state.get("tt1").position == 1200

    clock.now += 10
    assert state.record(MOVIE, 5500, 6000) == WATCHED          # >= 90%
    row = state.get("tt1")
    assert row.watched and row.position == 0 and row.watched_at == clock.now and not row.synced
    assert state.record(MOVIE, 100, 0) is None                 # no duration: ignored


def test_continue_watching_order_and_clear(state, clock):
    state.record(MOVIE, 1200, 6000)
    clock.now += 5
    state.record(ep(1, 1), 600, 2400)
    assert [r.video_id for r in state.continue_watching()] == ["tt5:1:1", "tt1"]
    state.clear_resume("tt5:1:1")
    assert [r.video_id for r in state.continue_watching()] == ["tt1"]


def test_lookup_and_set_watched(state):
    state.set_watched([ep(1, 1), ep(1, 2)], True)
    assert set(state.lookup(["tt5:1:1", "tt5:1:2", "nope"])) == {"tt5:1:1", "tt5:1:2"}
    assert state.watched_episodes("tt5") == {(1, 1), (1, 2)}
    state.set_watched([ep(1, 2)], False)
    assert state.watched_episodes("tt5") == {(1, 1)}
    assert not state.lookup(["tt5:1:2"])["tt5:1:2"].watched


def test_recent_shows_and_binge_group(state, clock):
    state.set_watched([ep(1, 1)], True)
    clock.now += 10
    other = PlaybackEntry(video_id="tt9:1:1", type="series", meta_id="tt9", season=1, episode=1)
    state.set_watched([other], True)
    assert state.recent_shows() == [("tt9", "series"), ("tt5", "series")]

    state.touch(ep(1, 2, binge_group="rd|2160p|group"))
    assert state.binge_group("tt5", max_age=3600) == "rd|2160p|group"
    clock.now += 7200
    assert state.binge_group("tt5", max_age=3600) == ""


def test_merge_remote_adds_and_removes(state, clock):
    state.set_watched([ep(1, 1)], True)              # local only: never synced, never removed by a pull
    remote = [(ep(1, 2), 500.0), (MOVIE, 600.0), (ep(1, 3), clock.now - 60)]
    assert state.merge_remote(remote) == (3, 0, False)
    assert state.get("tt5:1:2").synced and state.get("tt1").watched_at == 600.0

    # Remotely unwatched (missing from a full pull) -> unwatched here, except local-only
    # rows, protected ids and anything watched in the last day.
    assert state.merge_remote([], protect={"tt1"}) == (0, 1, False)
    assert not state.get("tt5:1:2").watched
    assert state.get("tt5:1:1").watched and state.get("tt1").watched and state.get("tt5:1:3").watched


def test_merge_remote_refuses_mass_removal(state):
    many = [(PlaybackEntry(video_id=f"tt{i}", type="movie"), 1.0) for i in range(100, 150)]
    state.merge_remote(many)
    assert state.merge_remote([]) == (0, 0, True)    # an empty/broken response wipes nothing
    assert state.watched_count() == 50


def test_entry_json_roundtrip():
    entry = ep(2, 3, binge_group="g")
    assert PlaybackEntry.from_json(entry.to_json()) == entry


def test_next_episode():
    meta = Meta.from_dict({"id": "tt5", "type": "series", "name": "S", "videos": [
        {"id": "tt5:0:1", "season": 0, "episode": 1},
        {"id": "tt5:1:1", "season": 1, "episode": 1}, {"id": "tt5:1:2", "season": 1, "episode": 2},
        {"id": "tt5:2:1", "season": 2, "episode": 1, "released": "2026-01-01T00:00:00.000Z"},
        {"id": "tt5:2:2", "season": 2, "episode": 2, "released": "2099-01-01T00:00:00.000Z"},
    ]})
    today = "2026-09-30"
    assert next_episode(meta, set(), today) is None                         # not started
    assert next_episode(meta, {(1, 1)}, today).id == "tt5:1:2"
    assert next_episode(meta, {(1, 2)}, today).id == "tt5:2:1"              # furthest watched counts
    assert next_episode(meta, {(1, 1), (0, 1), (2, 1)}, today) is None      # next one hasn't aired
    assert next_episode(meta, {(1, 1), (2, 1)}, today) is None              # skipped 1x02 isn't "next"
