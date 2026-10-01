"""Global Search (script.shiggsy365.globalsearch): sources and row building."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "script.shiggsy365.globalsearch", "resources", "lib"))

from globalsearch import sources  # noqa: E402

SB = "plugin://plugin.video.stremiobridge/"


class FakeRPC:
    """Answers JSON-RPC calls from a table of {(method, key): result}."""

    def __init__(self, directories=None, library=None):
        self.directories, self.library, self.calls = directories or {}, library or {}, []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "Files.GetDirectory":
            return {"files": self.directories.get(params["directory"], [])}
        return self.library.get(method, {})


def test_library_rows():
    rpc = FakeRPC(library={
        "VideoLibrary.GetMovies": {"movies": [{"movieid": 1, "title": "Batman", "year": 1989, "file": "/m/batman.mkv",
                                               "art": {"poster": "p.jpg", "fanart": "f.jpg"}, "playcount": 1,
                                               "genre": ["Action"], "plot": "Gotham."}]},
        "VideoLibrary.GetTVShows": {"tvshows": [{"tvshowid": 7, "title": "Batman Beyond", "art": {"poster": "q.jpg"}}]},
        "AudioLibrary.GetSongs": {"songs": [{"songid": 3, "title": "Batdance", "artist": ["Prince"], "album": "Batman",
                                             "file": "/s/batdance.flac", "thumbnail": "t.jpg"}]},
    })
    video = dict(sources.library_video_rows(rpc, "bat", 10))
    movie = video["movies"][0]
    assert (movie.label, movie.builtin(), movie.watched, movie.year) == ("Batman", "PlayMedia(/m/batman.mkv)", True, 1989)
    assert video["tvshows"][0].builtin() == "ActivateWindow(Videos,videodb://tvshows/titles/7/,return)"
    assert video["episodes"] == []
    assert rpc.calls[0][1]["filter"] == {"field": "title", "operator": "contains", "value": "bat"}
    music = dict(sources.library_music_rows(rpc, "bat", 10))
    assert music["songs"][0].plot == "Prince · Batman" and music["songs"][0].art["thumb"] == "t.jpg"


def test_stremio_rows_one_per_catalog_without_paging_tiles():
    search = SB + "?action=search&query=the%20batman"
    movies = SB + "?action=catalog&addon=k&type=movie&id=search&f_search=the+batman"
    rpc = FakeRPC(directories={
        search: [{"file": movies, "filetype": "directory",
                  "label": "Movies · Movies Search  [COLOR grey]AIOMetadata (20)[/COLOR]"}],
        movies: [{"file": SB + "?action=play&type=movie&id=tt1", "filetype": "file", "label": "The Batman",
                  "art": {"poster": "p"}, "year": 2022},
                 {"file": SB + "?action=extended_info&type=movie&id=tt2", "filetype": "file", "label": "Batman"},
                 {"file": SB + "?action=meta&type=series&id=tt3", "filetype": "directory", "label": "Batman Beyond"},
                 {"file": movies + "&skip=20", "filetype": "directory", "label": "Next Page"}],
    })
    (title, items), = sources.stremio_rows(rpc, "the batman", 10)
    assert title == "Movies · Movies Search  AIOMetadata"
    assert [i.action for i in items] == ["play", "run", "open"]
    assert items[2].builtin() == f"ActivateWindow(Videos,{SB}?action=meta&type=series&id=tt3,return)"


def test_stremio_single_catalog_lists_results_directly():
    search = SB + "?action=search&query=x"
    rpc = FakeRPC(directories={search: [{"file": SB + "?action=play&type=movie&id=tt1", "filetype": "file",
                                         "label": "X"}]})
    assert [(t, [i.label for i in items]) for t, items in sources.stremio_rows(rpc, "x", 10)] == [("Stremio Bridge", ["X"])]
    assert sources.stremio_rows(FakeRPC(), "x", 10) == []


def test_spotify_rows_skip_more_entries_and_limit():
    base = "plugin://plugin.audio.spotify2/"
    rpc = FakeRPC(directories={
        base + "?action=search_albums&albumid=bat%20man": [
            {"file": base + f"?action=browse_album&albumid={n}", "filetype": "directory", "label": f"Album {n}",
             "thumbnail": f"{n}.jpg"} for n in range(5)] + [
            {"file": base + "?action=search_albums&albumid=bat+man&offset=50", "filetype": "directory", "label": "Next"}],
        base + "?action=search_tracks&trackid=bat%20man": [
            {"file": base + "?action=play_track&trackid=1", "filetype": "file", "label": "Song"}],
    })
    rows = dict(sources.spotify_rows(rpc, "bat man", 3, [("Albums", "search_albums", "albumid", "a.png"),
                                                          ("Songs", "search_tracks", "trackid", "s.png")]))
    assert [i.label for i in rows["Albums"]] == ["Album 0", "Album 1", "Album 2"]
    assert rows["Albums"][0].builtin().startswith("ActivateWindow(Music,") and rows["Albums"][0].art["thumb"] == "0.jpg"
    assert rows["Songs"][0].builtin() == f"PlayMedia({base}?action=play_track&trackid=1)"


def test_spotify_shortcuts_cost_nothing_until_opened():
    tiles = sources.spotify_shortcuts("bat man", sources.SPOTIFY_MUSIC + sources.SPOTIFY_PODCASTS, "special://x")
    assert [t.label for t in tiles] == ["Songs", "Artists", "Albums", "Playlists", "Podcasts", "Podcast episodes"]
    assert tiles[0].builtin() == ("ActivateWindow(Music,plugin://plugin.audio.spotify2/?action=search_tracks"
                                  "&trackid=bat%20man,return)")
    assert tiles[4].builtin().endswith("action=search_podcast_shows&query=bat%20man,return)")
    assert tiles[0].art["thumb"] == "special://x/icon_music_songs.png"
