import os

from stremio.library import MANUAL, WATCHLIST, Library, folder_name, nfo_url, safe_name
from stremio.models import Meta


def play_url(type_, id_, meta=None):
    return f"plugin://x/?action=play&type={type_}&id={id_}" + (f"&meta={meta}" if meta else "")


SHOW = Meta.from_dict({
    "id": "tt0903747", "type": "series", "name": "Breaking Bad: Pilot?", "releaseInfo": "2008", "tvdb_id": 81189,
    "videos": [
        {"id": "tt0903747:1:1", "season": 1, "episode": 1, "released": "2008-01-20T00:00:00.000Z"},
        {"id": "tt0903747:1:2", "season": 1, "episode": 2, "released": "2008-01-27T00:00:00.000Z"},
        {"id": "tt0903747:2:1", "season": 2, "episode": 1, "released": "2099-01-01T00:00:00.000Z"},
    ]})


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_names_and_nfo_urls():
    assert safe_name('Mission: Impossible / "Dead Reckoning"?.') == "Mission Impossible Dead Reckoning"
    assert folder_name("Alien", 1979) == "Alien (1979)" and folder_name("X", None) == "X"
    assert nfo_url("movie", {"imdb": "tt1", "tmdb": 2}) == "https://www.imdb.com/title/tt1/"
    assert nfo_url("series", {"tmdb": 1396}) == "https://www.themoviedb.org/tv/1396"
    assert nfo_url("series", {"tvdb": 81189}) == "https://thetvdb.com/?tab=series&id=81189"
    assert nfo_url("movie", {}) is None


def test_add_movie_and_remove(tmp_path):
    lib = Library(str(tmp_path), play_url)
    lib.add_movie("tt0111161", "The Shawshank Redemption", 1994, {"imdb": "tt0111161"})
    folder = tmp_path / "Movies" / "The Shawshank Redemption (1994)"
    assert read(folder / "The Shawshank Redemption (1994).strm") == "plugin://x/?action=play&type=movie&id=tt0111161\n"
    assert read(folder / "The Shawshank Redemption (1994).nfo") == "https://www.imdb.com/title/tt0111161/\n"
    lib = Library(str(tmp_path), play_url)                       # index persisted
    assert lib.contains("movie", "tt0111161") and lib.get("movie", "tt0111161")["source"] == MANUAL
    assert list(lib.file_map().values()) == [("tt0111161", None)]
    assert lib.remove("movie", "tt0111161")["title"] == "The Shawshank Redemption"
    assert not folder.exists() and not Library(str(tmp_path), play_url).contains("movie", "tt0111161")
    assert lib.remove("movie", "tt0111161") is None


def test_add_and_update_show(tmp_path):
    lib = Library(str(tmp_path), play_url)
    assert lib.add_show(SHOW, today="2026-10-01") == 2                # the unaired episode is skipped
    show_dir = tmp_path / "TV" / "Breaking Bad Pilot (2008)"
    assert read(show_dir / "tvshow.nfo") == "https://www.imdb.com/title/tt0903747/\n"
    episode = show_dir / "Season 1" / "Breaking Bad Pilot S01E02.strm"
    assert read(episode) == "plugin://x/?action=play&type=series&id=tt0903747:1:2&meta=tt0903747\n"
    assert not (show_dir / "Season 2").exists()

    assert lib.update_show(SHOW, today="2026-10-01") == 0             # nothing new
    assert lib.update_show(SHOW, today="2099-02-01") == 1             # it aired
    files = Library(str(tmp_path), play_url).file_map()
    assert files[str(show_dir / "Season 2" / "Breaking Bad Pilot S02E01.strm")] == ("tt0903747:2:1", "tt0903747")
    assert lib.update_show(Meta.from_dict({"id": "tt9", "type": "series", "name": "Not added"}), "2026-10-01") == 0


def test_sources_manual_wins(tmp_path):
    lib = Library(str(tmp_path), play_url)
    lib.add_movie("tt1", "A", 2000, source=WATCHLIST)
    assert [e["id"] for e in lib.entries(source=WATCHLIST)] == ["tt1"]
    lib.add_movie("tt1", "A", 2000, source=MANUAL)                   # the user added it too
    lib.add_movie("tt1", "A", 2000, source=WATCHLIST)                # a later watchlist sync doesn't demote it
    assert lib.get("movie", "tt1")["source"] == MANUAL and lib.entries(source=WATCHLIST) == []
