"""Where Global Search looks, and how results become rows. No Kodi imports:
everything goes through an `rpc(method, params)` callable (Kodi's JSON-RPC),
so it can be tested.

A row is one source's results (e.g. "Library · Movies", one Stremio Bridge
search catalog, "Spotify · Albums"). Add-ons are searched by listing their
own search folders, the way Kodi browses them, so nothing here depends on
their code.
"""

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

STREMIO_BRIDGE = "plugin.video.stremiobridge"
SPOTIFY = "plugin.audio.spotify2"
YOUTUBE = "plugin.video.youtube"
POSTER, SQUARE, WIDE = "poster", "square", "wide"
FILE_PROPERTIES = ["title", "art", "thumbnail", "plot", "year", "genre", "playcount", "file", "artist", "album",
                   "showtitle", "season", "episode"]


@dataclass
class Item:
    label: str
    path: str = ""
    action: str = "play"   # "play" (PlayMedia), "run" (RunPlugin) or "open" (ActivateWindow)
    window: str = "Videos"  # for "open": Videos or Music
    art: dict = field(default_factory=dict)
    plot: str = ""
    year: int = 0
    genre: str = ""
    watched: bool = False

    def builtin(self):
        """The Kodi built-in that opens or plays this item."""
        if self.action == "open":
            return f"ActivateWindow({self.window},{self.path},return)"
        if self.action == "run":
            return f"RunPlugin({self.path})"
        return f"PlayMedia({self.path})"


@dataclass
class Row:
    title: str
    items: list
    shape: str = POSTER
    counted: bool = True   # show "(n)" after the title (not for shortcut rows)


# ------------------------------------------------------------------ the library

def _contains(field_name, query):
    return {"field": field_name, "operator": "contains", "value": query}


def _art(art, *keys):
    for key in keys:
        if art.get(key):
            return art[key]
    return ""


def library_video_rows(rpc, query, limit):
    rows = []
    found = rpc("VideoLibrary.GetMovies", {
        "filter": _contains("title", query), "limits": {"end": limit},
        "properties": ["title", "year", "art", "plot", "file", "playcount", "genre"]}).get("movies") or []
    rows.append(("movies", [Item(m["title"], m.get("file", ""), art=_poster_art(m.get("art") or {}),
                                 plot=m.get("plot", ""), year=m.get("year") or 0, genre=" / ".join(m.get("genre") or []),
                                 watched=(m.get("playcount") or 0) > 0) for m in found]))
    found = rpc("VideoLibrary.GetTVShows", {
        "filter": _contains("title", query), "limits": {"end": limit},
        "properties": ["title", "year", "art", "plot", "playcount", "genre"]}).get("tvshows") or []
    rows.append(("tvshows", [Item(s["title"], f"videodb://tvshows/titles/{s['tvshowid']}/", action="open",
                                  art=_poster_art(s.get("art") or {}), plot=s.get("plot", ""),
                                  year=s.get("year") or 0, genre=" / ".join(s.get("genre") or []),
                                  watched=(s.get("playcount") or 0) > 0) for s in found]))
    found = rpc("VideoLibrary.GetEpisodes", {
        "filter": _contains("title", query), "limits": {"end": limit},
        "properties": ["title", "showtitle", "season", "episode", "art", "plot", "file", "playcount"]}).get("episodes") or []
    rows.append(("episodes", [
        Item(f"{e.get('showtitle', '')} {e.get('season', 0)}x{e.get('episode', 0):02d}. {e['title']}".strip(),
             e.get("file", ""), art=_poster_art(e.get("art") or {}), plot=e.get("plot", ""),
             watched=(e.get("playcount") or 0) > 0) for e in found]))
    return rows


def _poster_art(art):
    poster = _art(art, "poster", "tvshow.poster", "thumb")
    return {"poster": poster, "thumb": _art(art, "thumb", "poster"), "fanart": _art(art, "fanart", "tvshow.fanart")}


def _square_art(thumbnail, fanart=""):
    return {"thumb": thumbnail, "fanart": fanart}


def library_music_rows(rpc, query, limit):
    rows = []
    found = rpc("AudioLibrary.GetArtists", {
        "filter": _contains("artist", query), "limits": {"end": limit},
        "properties": ["thumbnail", "fanart", "description"]}).get("artists") or []
    rows.append(("artists", [Item(a["artist"], f"musicdb://artists/{a['artistid']}/", action="open", window="Music",
                                  art=_square_art(a.get("thumbnail", ""), a.get("fanart", "")),
                                  plot=a.get("description", "")) for a in found]))
    found = rpc("AudioLibrary.GetAlbums", {
        "filter": _contains("album", query), "limits": {"end": limit},
        "properties": ["title", "artist", "year", "thumbnail", "fanart", "genre"]}).get("albums") or []
    rows.append(("albums", [Item(a["title"], f"musicdb://albums/{a['albumid']}/", action="open", window="Music",
                                 art=_square_art(a.get("thumbnail", ""), a.get("fanart", "")),
                                 plot=" / ".join(a.get("artist") or []), year=a.get("year") or 0,
                                 genre=" / ".join(a.get("genre") or [])) for a in found]))
    found = rpc("AudioLibrary.GetSongs", {
        "filter": _contains("title", query), "limits": {"end": limit},
        "properties": ["title", "artist", "album", "thumbnail", "fanart", "file", "year"]}).get("songs") or []
    rows.append(("songs", [Item(s["title"], s.get("file", ""), art=_square_art(s.get("thumbnail", ""), s.get("fanart", "")),
                                plot=" · ".join(p for p in (" / ".join(s.get("artist") or []), s.get("album", "")) if p),
                                year=s.get("year") or 0) for s in found]))
    return rows


# ------------------------------------------------------------------ add-ons

def directory(rpc, path, media="files"):
    """An add-on folder's entries, as JSON-RPC Files.GetDirectory gives them."""
    return rpc("Files.GetDirectory", {"directory": path, "media": media, "properties": FILE_PROPERTIES}).get("files") or []


def item_from_file(entry, window, skip=lambda entry: False):
    """An Item for one Files.GetDirectory entry (None for paging/filter entries)."""
    path = entry.get("file") or ""
    if not path or skip(entry):
        return None
    art = dict(entry.get("art") or {})
    if entry.get("thumbnail") and "thumb" not in art:
        art["thumb"] = entry["thumbnail"]
    art.setdefault("poster", art.get("thumb", ""))
    if entry.get("filetype") == "directory":
        action = "open"
    elif "action=extended_info" in path:  # Stremio Bridge set to open Extended info on select
        action = "run"
    else:
        action = "play"
    label = entry.get("label") or entry.get("title") or ""
    if entry.get("showtitle") and entry.get("season") not in (None, -1) and entry.get("episode") not in (None, -1):
        label = f"{entry['showtitle']} {entry['season']}x{entry['episode']:02d}. {entry.get('title') or label}"
    genre = entry.get("genre")
    return Item(label, path, action=action, window=window, art=art, plot=entry.get("plot") or "",
                year=entry.get("year") or 0, genre=" / ".join(genre) if isinstance(genre, list) else genre or "",
                watched=(entry.get("playcount") or 0) > 0)


def _stremio_skip(entry):
    """Stremio Bridge's "Next Page" and "Filter by Genre" tiles."""
    path = entry.get("file") or ""
    return "action=catalog" in path or "action=choose_filter" in path


def stremio_rows(rpc, query, limit):
    """One row per Stremio Bridge search catalog (its search folder lists a
    folder per catalog, or the results straight away when only one found any)."""
    top = directory(rpc, f"plugin://{STREMIO_BRIDGE}/?action=search&query={quote(query)}", "video")
    groups = [e for e in top if "action=catalog" in (e.get("file") or "")]
    if not groups:
        items = [i for i in (item_from_file(e, "Videos", _stremio_skip) for e in top) if i]
        return [("Stremio Bridge", items[:limit])] if items else []
    rows = []
    for group in groups:
        entries = directory(rpc, group["file"], "video")
        items = [i for i in (item_from_file(e, "Videos", _stremio_skip) for e in entries) if i][:limit]
        if items:
            rows.append((_plain(group.get("label") or ""), items))
    return rows


def _plain(label):
    """A folder label without Kodi formatting tags or a trailing "(20)" count
    (the row shows its own)."""
    label = re.sub(r"\[/?(?:COLOR[^\]]*|B|I)\]", "", label)
    return re.sub(r"\s*\(\d+\+?\)\s*$", "", label).strip()


# (row title, Spotify2 action, its search parameter, icon in Spotify2's resources)
SPOTIFY_MUSIC = [("Songs", "search_tracks", "trackid", "icon_music_songs.png"),
                 ("Artists", "search_artists", "artistid", "icon_music_artists.png"),
                 ("Albums", "search_albums", "albumid", "icon_music_albums.png"),
                 ("Playlists", "search_playlists", "playlistid", "icon_music_playlists.png")]
# "own:" icons come with Global Search (resources/media), drawn to match.
SPOTIFY_PODCASTS = [("Podcasts", "search_podcast_shows", "query", "own:podcasts.png"),
                    ("Podcast episodes", "search_podcast_episodes", "query", "own:podcast_episodes.png")]
OWN_MEDIA = "special://home/addons/script.shiggsy365.globalsearch/resources/media"


def icon_path(icon, icon_dir):
    """A tile icon: one of Global Search's own ("own:name.png") or the source add-on's."""
    if icon.startswith("own:"):
        return f"{OWN_MEDIA}/{icon[4:]}"
    return f"{icon_dir}/{icon}" if icon_dir else ""
# Spotify2 allows itself 8 Web API requests per 30 s (shared by everything in
# Kodi), and each search page costs several (it fills a 40-item page from
# 10-item API responses). So by default Global Search only shows shortcuts,
# which cost nothing until one is opened; loading rows is opt-in.


def _spotify_skip(entry):
    """Spotify's "more results" entries."""
    return "offset=" in (entry.get("file") or "")


def spotify_url(action, param, query):
    return f"plugin://{SPOTIFY}/?action={action}&{param}={quote(query)}"


def spotify_shortcuts(query, searches, icon_dir=""):
    """Tiles that open Spotify2's own search results (no Spotify request until opened)."""
    return [Item(title, spotify_url(action, param, query), action="open", window="Music",
                 art={"thumb": icon_path(icon, icon_dir)})
            for title, action, param, icon in searches]


def spotify_rows(rpc, query, limit, searches):
    """Spotify results as rows, one search at a time (each costs several API requests)."""
    rows = []
    for title, action, param, _ in searches:
        entries = directory(rpc, spotify_url(action, param, query), "music")
        items = [i for i in (item_from_file(e, "Music", _spotify_skip) for e in entries) if i][:limit]
        for item in items:
            item.art.setdefault("thumb", item.art.get("poster", ""))
        rows.append((title, items))
    return rows


# YouTube: each search is a YouTube Data API call costing 100 of the 10,000
# quota units a day an API key gets, so only Videos is searched by default.
# (row title, YouTube search_type, icon in the YouTube add-on's resources/media)
YOUTUBE_SEARCHES = [("Videos", "video", "own:youtube_videos.png"), ("Channels", "channel", "own:youtube_channels.png"),
                    ("Playlists", "playlist", "own:youtube_playlists.png")]
YOUTUBE_CACHE_SECONDS = 3600


class TimedCache:
    """Search results remembered in a JSON file for `ttl` seconds, so searching
    for the same thing again doesn't spend API quota."""

    def __init__(self, path, ttl, clock=time.time):
        self.path, self.ttl, self.clock = path, ttl, clock

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key):
        entry = self._load().get(key)
        if not entry or self.clock() - entry.get("at", 0) > self.ttl:
            return None
        try:
            return [Item(**item) for item in entry["items"]]
        except (TypeError, KeyError):
            return None

    def set(self, key, items):
        now = self.clock()
        data = {k: v for k, v in self._load().items() if now - v.get("at", 0) <= self.ttl}
        data[key] = {"at": now, "items": [asdict(item) for item in items]}
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass  # remembering is only an optimisation


def youtube_url(query, search_type):
    """The YouTube add-on's search for one kind of result, without its
    Channels/Playlists/Live folders and without adding to its search history."""
    return (f"plugin://{YOUTUBE}/kodion/search/query/?q={quote(query)}&search_type={search_type}"
            "&hide_folders=true")


def _youtube_skip(entry):
    """YouTube's "Next page" and page-jump entries."""
    path = entry.get("file") or ""
    return "page_token=" in path or "/kodion/goto_page" in path


def youtube_rows(rpc, query, limit, searches, cache=None):
    """YouTube results as rows. With `cache` (a TimedCache), results are reused
    for the same search; nothing is remembered when YouTube found nothing (or
    refused, e.g. with the daily quota used up)."""
    rows = []
    for title, search_type, _ in searches:
        key = f"youtube:{search_type}:{query.strip().lower()}"
        items = cache.get(key) if cache else None
        if items is None:
            entries = directory(rpc, youtube_url(query, search_type), "video")
            items = [i for i in (item_from_file(e, "Videos", _youtube_skip) for e in entries) if i]
            for item in items:
                item.art.setdefault("thumb", item.art.get("poster", ""))
            if cache and items:
                cache.set(key, items)
        rows.append((title, items[:limit]))
    return rows


def youtube_shortcuts(query, searches, icon_dir=""):
    """Tiles that open the YouTube add-on's search (no API quota until opened)."""
    return [Item(title, youtube_url(query, search_type), action="open", window="Videos",
                 art={"thumb": icon_path(icon, icon_dir)})
            for title, search_type, icon in searches]
