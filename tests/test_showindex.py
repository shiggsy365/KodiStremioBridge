from stremio.record import replace

from stremio import showindex
from stremio.cache import Cache
from stremio.models import Meta

URL = "https://addon.example/meta/series/tt5.json"
RAW = {
    "id": "tt5", "type": "series", "name": "Show", "poster": "http://p", "description": "About it",
    "behaviorHints": {"defaultVideoId": ""},
    "videos": [
        {"id": "tt5:1:1", "season": 1, "episode": 1, "title": "One", "overview": "First", "thumbnail": "http://1"},
        {"id": "tt5:1:2", "season": 1, "number": 2, "title": "Two", "released": "2999-01-01T00:00:00.000Z"},
        {"id": "tt5:2:1", "season": "2", "episode": 1, "title": "Three", "imdbRating": "8.1"},
        {"id": "tt5:0:1", "season": 0, "episode": 1, "title": "Special"},
        {"id": "tt5:extra", "title": "No season"},
        {"title": "No id: skipped"},
    ],
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def cached(tmp_path):
    clock = Clock()
    cache = Cache(str(tmp_path / "c.db"), clock=clock)
    cache.set(URL, {"meta": RAW}, ttl=3600)  # the addon's response, as StremioClient caches it
    return cache, clock


def test_index_has_every_video_and_seasons_in_full(tmp_path):
    cache, clock = cached(tmp_path)
    full = Meta.from_dict(RAW, "series")
    assert showindex.load(cache, URL, "series", clock.now) is None    # not indexed yet
    assert showindex.save(cache, URL, RAW) and showindex.is_current(cache, URL)

    index = showindex.load(cache, URL, "series", clock.now)
    assert index.url == URL and "videos" not in index.summary
    light = index.meta
    assert replace(light, videos=()) == replace(full, videos=())
    assert [(v.id, v.season, v.episode, v.released) for v in light.videos] == \
        [(v.id, v.season, v.episode, v.released) for v in full.videos]
    assert light.seasons == full.seasons and not any(v.title for v in light.videos)

    detailed = index.with_seasons(cache, {1, None})
    assert detailed.episodes(1) == full.episodes(1) and detailed.episodes(None) == full.episodes(None)
    assert [v.title for v in detailed.episodes(2)] == [""]           # not asked for
    assert index.with_seasons(cache, {2}).episodes(2) == full.episodes(2)
    assert index.with_seasons(cache, ()) is light


def test_index_is_used_only_with_the_response_it_was_made_from(tmp_path):
    cache, clock = cached(tmp_path)
    showindex.save(cache, URL, RAW)

    clock.now += 10
    cache.set(URL, {"meta": RAW}, ttl=3600)       # the response was fetched again
    assert not showindex.is_current(cache, URL)
    assert showindex.load(cache, URL, "series", clock.now) is None

    showindex.save(cache, URL, RAW)
    index = showindex.load(cache, URL, "series", clock.now)
    assert index is not None
    clock.now += 3600                              # the response is stale: read it whole (refreshing it)
    assert showindex.load(cache, URL, "series", clock.now) is None


def test_a_season_gone_from_the_cache_means_no_index(tmp_path):
    cache, clock = cached(tmp_path)
    showindex.save(cache, URL, RAW)
    index = showindex.load(cache, URL, "series", clock.now)
    cache.set(showindex.season_key(URL, 1), [], ttl=5)   # out of step with the response
    assert index.with_seasons(cache, {1}) is None and index.raw_season(cache, 1) is None
    assert index.with_seasons(cache, {2}) is not None


def test_nothing_to_index_without_the_response(tmp_path):
    cache = Cache(str(tmp_path / "c.db"), clock=Clock())
    assert not showindex.save(cache, URL, RAW) and not showindex.is_current(cache, URL)
