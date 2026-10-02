import pytest

from conftest import CINEMETA_LIKE, STREAM_ADDON
from stremio import ManifestError
from stremio.models import Manifest, Meta, MetaPreview, Video, parse_runtime


def test_parses_basic_manifest():
    m = Manifest.from_dict(CINEMETA_LIKE)
    assert m.id == "com.example.meta"
    assert m.resource_names == ("catalog", "meta")
    assert [c.key for c in m.catalogs] == ["movie/top", "series/top", "movie/search"]


def test_catalog_extras():
    m = Manifest.from_dict(CINEMETA_LIKE)
    popular, _, search = m.catalogs
    assert popular.is_browsable and not popular.is_searchable
    assert popular.extra_prop("genre").options == ("Action", "Sci-Fi")
    assert search.is_searchable and not search.is_browsable


def test_legacy_extra_fields():
    m = Manifest.from_dict({
        "id": "x", "name": "X", "resources": ["catalog"], "types": ["movie"],
        "catalogs": [{"type": "movie", "id": "c", "extraSupported": ["search", "genre"],
                      "extraRequired": ["search"], "genres": ["Drama"]}],
    })
    (c,) = m.catalogs
    assert c.name == "c"
    assert c.extra_prop("search").is_required
    assert c.extra_prop("genre").options == ("Drama",)
    assert not c.is_browsable


def test_supports_string_resources_inherit_manifest_prefixes():
    m = Manifest.from_dict(CINEMETA_LIKE)
    assert m.supports("meta", "movie", "tt0111161")
    assert not m.supports("meta", "movie", "kitsu:1")
    assert not m.supports("meta", "channel", "tt0111161")
    assert not m.supports("stream", "movie", "tt0111161")


def test_supports_object_resources():
    m = Manifest.from_dict(STREAM_ADDON)
    assert m.supports("stream", "series", "tt0903747:1:1")
    assert m.supports("stream", "movie", "kitsu:42")
    # Resource-level types override the manifest's broader list.
    assert not m.supports("stream", "anime", "kitsu:42")
    assert m.p2p and m.configurable


def test_no_id_prefixes_means_any_id():
    m = Manifest.from_dict({"id": "x", "name": "X", "resources": ["stream"], "types": ["movie"]})
    assert m.supports("stream", "movie", "anything:1")


def test_malformed_catalogs_are_skipped():
    m = Manifest.from_dict({**CINEMETA_LIKE, "catalogs": [{"name": "no type"}, CINEMETA_LIKE["catalogs"][0]]})
    assert [c.key for c in m.catalogs] == ["movie/top"]


@pytest.mark.parametrize("bad", [
    [], {"name": "X", "resources": []}, {"id": "x", "resources": []}, {"id": "x", "name": "X"},
])
def test_rejects_invalid_manifest(bad):
    with pytest.raises(ManifestError):
        Manifest.from_dict(bad)


def test_browsable_allows_required_filters_with_options():
    m = Manifest.from_dict({**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "year", "name": "By year",
         "extra": [{"name": "genre", "isRequired": True, "options": ["2026", "2025"]}]},
    ]})
    (c,) = m.catalogs
    assert c.is_browsable
    assert [e.name for e in c.filters] == ["genre"]


def test_filters_exclude_search_and_skip():
    popular = Manifest.from_dict(CINEMETA_LIKE).catalogs[0]
    assert [e.name for e in popular.filters] == ["genre"]
    assert popular.supports_paging


def test_meta_preview_parsing():
    p = MetaPreview.from_dict({
        "id": "tt0111161", "name": "The Shawshank Redemption", "poster": "http://p",
        "releaseInfo": "1994", "imdbRating": "9.3", "genres": ["Drama"], "runtime": "142 min",
    }, default_type="movie")
    assert p.type == "movie" and p.year == 1994 and p.imdb_rating == 9.3
    assert p.genres == ("Drama",) and p.runtime_seconds == 142 * 60


def test_meta_preview_tolerates_junk():
    assert MetaPreview.from_dict({"id": "x"}, "movie") is None
    assert MetaPreview.from_dict("nope", "movie") is None
    p = MetaPreview.from_dict({"id": "x", "name": "X", "imdbRating": "", "releaseInfo": "2008–2013"}, "series")
    assert p.imdb_rating is None and p.year == 2008


@pytest.mark.parametrize("text,seconds", [
    ("142 min", 8520), ("2h 22min", 8520), ("1h", 3600), ("45", 2700), ("", None), ("N/A", None),
])
def test_parse_runtime(text, seconds):
    assert parse_runtime(text) == seconds


SERIES_META = {
    "id": "tt0903747", "type": "series", "name": "Breaking Bad", "released": "2008-01-20T00:00:00.000Z",
    "cast": ["Bryan Cranston", "Aaron Paul"], "director": "Vince Gilligan",
    "links": [{"name": "Vince Gilligan", "category": "Writers", "url": "x"}],
    "trailers": [{"source": "abc123", "type": "Trailer"}],
    "videos": [
        {"id": "tt0903747:2:1", "season": 2, "episode": 1, "title": "Seven Thirty-Seven"},
        {"id": "tt0903747:1:2", "season": 1, "episode": 2, "name": "Cat's in the Bag...",
         "released": "2008-01-27T00:00:00.000Z"},
        {"id": "tt0903747:0:1", "season": 0, "episode": 1, "title": "Special"},
        {"id": "tt0903747:1:1", "season": 1, "number": 1, "title": "Pilot"},
        {"title": "no id"},
    ],
}


def test_meta_parsing():
    m = Meta.from_dict(SERIES_META)
    assert m.cast == ("Bryan Cranston", "Aaron Paul")
    assert m.director == ("Vince Gilligan",) and m.writer == ("Vince Gilligan",)
    assert m.trailer == "abc123" and m.premiered == "2008-01-20" and m.year == 2008
    assert len(m.videos) == 4


def test_meta_seasons_and_episodes():
    m = Meta.from_dict(SERIES_META)
    assert m.seasons == [1, 2, 0]
    assert [v.id for v in m.episodes(1)] == ["tt0903747:1:1", "tt0903747:1:2"]
    assert m.episodes(1)[1].title == "Cat's in the Bag..."


def test_channel_videos_keep_order():
    m = Meta.from_dict({"id": "yt:x", "type": "channel", "name": "C",
                        "videos": [{"id": "b", "title": "B"}, {"id": "a", "title": "A"}]})
    assert m.seasons == []
    assert [v.id for v in m.episodes(None)] == ["b", "a"]


def test_video_release():
    v = Video.from_dict({"id": "x", "released": "2030-05-01T00:00:00.000Z"})
    assert v.air_date == "2030-05-01"
    assert not v.is_released("2026-09-30") and v.is_released("2030-05-01")
    assert Video.from_dict({"id": "y"}).is_released("2026-09-30")


def test_meta_default_video_id():
    m = Meta.from_dict({"id": "x", "type": "movie", "name": "X", "behaviorHints": {"defaultVideoId": "x:1"}})
    assert m.default_video_id == "x:1" and m.videos == ()


def test_show_in_home_and_page_size():
    cats = Manifest.from_dict({**CINEMETA_LIKE, "catalogs": [
        {"type": "movie", "id": "a", "showInHome": True, "pageSize": 50},
        {"type": "movie", "id": "b", "showInHome": False, "pageSize": "bad"},
        {"type": "movie", "id": "c"},
        {"type": "movie", "id": "d", "extra": [{"name": "search", "isRequired": True}]},
    ]}).catalogs
    assert [(c.show_in_home, c.page_size) for c in cats] == [(True, 50), (False, None), (None, None), (None, None)]
    assert [c.default_front_page for c in cats] == [True, False, True, False]


def test_people_from_aiometadata_style_extras():
    m = Meta.from_dict({
        "id": "tt0111161", "type": "movie", "name": "The Shawshank Redemption",
        "director": "Frank Darabont", "writer": "Frank Darabont, Stephen King",
        "app_extras": {"cast": [{"name": "Tim Robbins", "character": "Andy Dufresne", "photo": "http://p/tim.jpg"}],
                       "directors": [{"name": "Frank Darabont", "character": "Frank Darabont", "photo": "http://p/fd.jpg"}]},
        "links": [{"category": "Cast", "name": "Tim Robbins"}, {"category": "Cast", "name": "Morgan Freeman"}],
        "trailers": [{"source": "abc", "type": "Trailer", "ytId": "PLl99DlL6b4"}],
    })
    assert m.cast == ("Tim Robbins", "Morgan Freeman")
    assert m.director == ("Frank Darabont",) and m.writer == ("Frank Darabont", "Stephen King")
    tim = next(p for p in m.people if p.name == "Tim Robbins")
    assert (tim.role, tim.photo) == ("Andy Dufresne", "http://p/tim.jpg")
    darabont = next(p for p in m.people if p.job == "director")
    assert darabont.role == "" and darabont.photo == "http://p/fd.jpg"   # "character" == name is dropped
    assert m.trailer == "PLl99DlL6b4"


def test_people_and_trailer_in_catalog_previews():
    p = MetaPreview.from_dict({"id": "tt1", "type": "movie", "name": "X", "cast": ["Jake Gyllenhaal", "Viola Davis"],
                               "director": ["Someone"], "trailers": [{"source": "yszyHq-S9W8", "type": "Trailer"}]})
    assert p.cast == ("Jake Gyllenhaal", "Viola Davis") and p.director == ("Someone",)
    assert p.trailer == "yszyHq-S9W8"
    assert MetaPreview.from_dict({"id": "tt1", "type": "movie", "name": "X",
                                  "trailerStreams": [{"ytId": "zz"}]}).trailer == "zz"
    assert MetaPreview.from_dict({"id": "tt1", "type": "movie", "name": "X"}).people == ()


def test_trim_meta_keeps_what_videos_use():
    from stremio.models import Meta, trim_meta

    response = {"meta": {"id": "tt5", "type": "series", "name": "Show", "app_extras": {"cast": []}, "videos": [
        {"id": "tt5:1:1", "title": "One", "season": 1, "episode": 1, "released": "2020-01-01T00:00:00Z",
         "thumbnail": "t", "overview": "O", "description": "O", "available": True, "runtime": "40m"},
        {"id": "tt5:1:2", "name": "Two", "season": 1, "number": 2, "description": "D"}]}}
    trimmed = trim_meta(response)
    assert trimmed["meta"]["app_extras"] == {"cast": []}                          # only videos are cut down
    assert trimmed["meta"]["videos"][0] == {"id": "tt5:1:1", "title": "One", "season": 1, "episode": 1,
                                             "released": "2020-01-01T00:00:00Z", "thumbnail": "t", "overview": "O"}
    assert trimmed["meta"]["videos"][1]["description"] == "D"                     # no overview: kept
    assert Meta.from_dict(trimmed["meta"]) == Meta.from_dict(response["meta"])
    assert trim_meta({"meta": None}) == {"meta": None}
