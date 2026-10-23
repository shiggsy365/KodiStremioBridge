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


def test_removing_an_episode_dismisses_its_show_until_new_activity(state, clock):
    state.set_watched([ep(1, 1)], True)
    clock.now += 10
    state.record(ep(1, 2), 600, 3000)                       # 1x02 part-watched
    assert [r.video_id for r in state.continue_watching()] == ["tt5:1:2"]
    assert state.recent_shows() == [("tt5", "series")]

    clock.now += 10
    state.clear_resume("tt5:1:2")                           # "Remove from Continue Watching"
    assert state.continue_watching() == [] and state.recent_shows() == []

    # An older resume point from another device (MDBList) doesn't bring it back...
    assert state.merge_resume([(ep(1, 2), 900, 3000, clock.now - 5)]) == 0
    assert state.continue_watching() == [] and state.recent_shows() == []
    # ...but watching or starting something newer in the show does.
    clock.now += 10
    state.record(ep(1, 2), 700, 3000)
    assert state.recent_shows() == [("tt5", "series")]

    # Next Up's own "Remove from Next Up", and new activity elsewhere, the same way.
    clock.now += 10
    state.dismiss_show("tt5")
    assert state.recent_shows() == []
    clock.now += 10
    state.merge_remote([(ep(1, 2), clock.now)], full=False)  # watched on another device
    assert state.recent_shows() == [("tt5", "series")]


def test_removing_a_movie_dismisses_nothing(state, clock):
    state.set_watched([ep(1, 1)], True)
    clock.now += 10
    state.record(MOVIE, 600, 6000)
    state.clear_resume("tt1")
    assert state.continue_watching() == [] and state.recent_shows() == [("tt5", "series")]


def test_signature_follows_what_next_up_depends_on(state, clock):
    first = state.signature()
    state.set_watched([ep(1, 1)], True)
    watched = state.signature()
    assert watched != first and state.signature() == watched                     # stable while nothing changes
    clock.now += 10
    state.set_watched([ep(1, 1)], False)
    assert state.signature() != watched
    unwatched = state.signature()
    state.dismiss_show("tt5")
    assert state.signature() != unwatched
    before_movie = state.signature()
    state.record(MOVIE, 600, 6000)                                                # movies don't affect Next Up
    assert state.signature() == before_movie


def test_continue_watching_lists_one_episode_per_show(state, clock):
    """Two part-watched episodes of a show: only the one played last is listed;
    the other keeps its resume point."""
    state.record(ep(1, 3), 900, 2400)
    clock.now += 5
    state.record(MOVIE, 1200, 6000)
    clock.now += 5
    state.record(ep(1, 1), 600, 2400)
    assert [r.video_id for r in state.continue_watching()] == ["tt5:1:1", "tt1"]
    assert state.lookup(["tt5:1:3"])["tt5:1:3"].position == 900
    assert len(state.continue_watching(1)) == 1

    clock.now += 5
    state.record(ep(1, 3), 1000, 2400)                         # back to episode 3: it's listed instead
    assert [r.video_id for r in state.continue_watching()] == ["tt5:1:3", "tt1"]
    state.clear_resume("tt5:1:3")
    assert [r.video_id for r in state.continue_watching()] == ["tt1"]  # removing the show doesn't bring 1x01 back
    assert state.lookup(["tt5:1:1"])["tt5:1:1"].position == 600      # (it keeps its resume point)

    clock.now += 5
    state.record(ep(1, 1), 700, 2400)                          # played again: the show is back
    assert [r.video_id for r in state.continue_watching()] == ["tt5:1:1", "tt1"]


def test_watched_state_follows_a_show_renamed_by_its_metadata_addon(tmp_path):
    """Episodes watched under tmdb:237243 still count once the metadata addon answers with the
    show's IMDb id (and its tmdb id among its fields): Next Up goes on from them."""
    state = WatchState(str(tmp_path / "w.db"))
    for episode in (26, 27):
        entry = PlaybackEntry(video_id=f"tmdb:237243:4:{episode}", type="series", meta_id="tmdb:237243",
                              season=4, episode=episode)
        state.record(entry, 3000, 3000)
    meta = Meta.from_dict({"id": "tt48436891", "type": "series", "name": "Big Brother", "tmdb_id": 237243,
                           "videos": [{"id": f"tt48436891:4:{n}", "season": 4, "episode": n,
                                       "released": "2026-10-%02dT12:00:00.000Z" % (n - 13)} for n in range(26, 37)]})
    assert state.watched_episodes("tt48436891") == {(4, 26), (4, 27)}
    assert next_episode(meta, state.watched_episodes(meta.id), "2026-10-23").id == "tt48436891:4:28"
    rows = state.lookup(["tt48436891:4:27", "tt48436891:4:28"])
    assert list(rows) == ["tt48436891:4:27"] and rows["tt48436891:4:27"].watched


def test_a_show_answered_under_another_id_keeps_its_watched_state(tmp_path):
    """Asked for tmdb:237243, the metadata addon answers with tt48436891 and nothing else to tie
    them: the episodes watched under the id it was asked for still count (fetch_meta_source)."""
    from stremio.meta import fetch_meta_source

    state = WatchState(str(tmp_path / "w.db"))
    state.record(PlaybackEntry(video_id="tmdb:999001:1:2", type="series", meta_id="tmdb:999001",
                               season=1, episode=2), 3000, 3000)

    class Client:
        def get_resource(self, url, resource, type_, id_):
            return {"meta": {"id": "tt99900100", "type": "series", "name": "Renamed",
                             "videos": [{"id": f"tt99900100:1:{n}", "season": 1, "episode": n,
                                         "released": "2026-01-0%dT00:00:00.000Z" % n} for n in (1, 2, 3)]}}

    class Addon:
        name, transport_url, manifest = "Meta", "https://example/manifest.json", None

    _, _, _, meta = fetch_meta_source(Client(), [Addon()], "series", "tmdb:999001")
    assert meta.id == "tt99900100" and state.watched_episodes(meta.id) == {(1, 2)}
    assert next_episode(meta, state.watched_episodes(meta.id), "2026-02-01").id == "tt99900100:1:3"
