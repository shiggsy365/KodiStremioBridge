"""Kodi library export for titles the user chooses ("My Library").

Movies become ``Movies/<Title (Year)>/<Title (Year)>.strm`` (+ ``.nfo``); shows
become ``TV/<Title (Year)>/tvshow.nfo`` plus ``Season N/<Title> SxxEyy.strm``
for every aired episode. A ``.strm`` holds this add-on's play URL, so library
playback goes through the usual stream search. Each ``.nfo`` holds the title's
IMDb/TMDB/TVDB web address, which Kodi's scrapers use to match exactly and fill
in artwork and metadata. ``library.json`` records what's in the library, where
it came from ("manual" or "watchlist") and which file is which video.
"""

import json
import os
import re
import shutil

MANUAL, WATCHLIST = "manual", "watchlist"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(text):
    """A file/folder name that works on every OS Kodi runs on."""
    name = _UNSAFE.sub("", text).strip().rstrip(".")
    return re.sub(r"\s+", " ", name) or "Untitled"


def folder_name(title, year=None):
    return safe_name(f"{title} ({year})" if year else title)


def nfo_url(type_, ids):
    """The web address Kodi's scrapers recognise for these ids, or None."""
    if ids.get("imdb"):
        return f"https://www.imdb.com/title/{ids['imdb']}/"
    if ids.get("tmdb"):
        return f"https://www.themoviedb.org/{'movie' if type_ == 'movie' else 'tv'}/{ids['tmdb']}"
    if ids.get("tvdb") and type_ != "movie":
        return f"https://thetvdb.com/?tab=series&id={ids['tvdb']}"
    return None


class Library:
    """`root` is the library folder; `play_url(type, id, meta=None)` builds the
    add-on URL written into each .strm file."""

    def __init__(self, root, play_url):
        self.root = root
        self.movies_dir = os.path.join(root, "Movies")
        self.tv_dir = os.path.join(root, "TV")
        self.index_path = os.path.join(root, "library.json")
        self._play_url = play_url
        try:
            with open(self.index_path, encoding="utf-8") as f:
                self._entries = json.load(f).get("entries", {})
        except (OSError, ValueError, AttributeError):
            self._entries = {}

    # ------------------------------------------------------------ index

    def _save(self):
        os.makedirs(self.root, exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": self._entries}, f, indent=1)
        os.replace(tmp, self.index_path)

    @staticmethod
    def key(type_, id_):
        return f"{'movie' if type_ == 'movie' else 'series'}:{id_}"

    def get(self, type_, id_):
        return self._entries.get(self.key(type_, id_))

    def contains(self, type_, id_):
        return self.key(type_, id_) in self._entries

    def entries(self, source=None, type_=None):
        return [e for e in self._entries.values()
                if (source is None or e["source"] == source) and (type_ is None or e["type"] == type_)]

    def file_map(self):
        """``{absolute .strm path: (video_id, show_id or None)}`` for everything in the library."""
        files = {}
        for entry in self._entries.values():
            folder = os.path.join(self.root, entry["folder"])
            if entry["type"] == "movie":
                files[os.path.join(folder, entry["file"])] = (entry["id"], None)
            else:
                for video_id, relative in entry["episodes"].items():
                    files[os.path.join(folder, relative)] = (video_id, entry["id"])
        return files

    # ------------------------------------------------------------ writing

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def add_movie(self, id_, title, year=None, ids=None, source=MANUAL):
        """Write a movie. Re-adding keeps it (and keeps "manual" if it was manual)."""
        key = self.key("movie", id_)
        existing = self._entries.get(key)
        name = folder_name(title, year)
        folder = os.path.join("Movies", name)
        self._write(os.path.join(self.root, folder, name + ".strm"), self._play_url("movie", id_) + "\n")
        url = nfo_url("movie", ids or {})
        if url:
            self._write(os.path.join(self.root, folder, name + ".nfo"), url + "\n")
        self._entries[key] = {
            "type": "movie", "id": id_, "title": title, "year": year, "folder": folder, "file": name + ".strm",
            "source": MANUAL if (existing and existing["source"] == MANUAL) else source,
        }
        self._save()
        return self._entries[key]

    def add_show(self, meta, today, source=MANUAL):
        """Write a show and its aired episodes. Returns the number of episode files added."""
        key = self.key("series", meta.id)
        existing = self._entries.get(key)
        folder = existing["folder"] if existing else os.path.join("TV", folder_name(meta.name, meta.year))
        url = nfo_url("series", dict(meta.external_ids))
        if url:
            self._write(os.path.join(self.root, folder, "tvshow.nfo"), url + "\n")
        entry = {
            "type": "series", "id": meta.id, "title": meta.name, "year": meta.year, "folder": folder,
            "source": MANUAL if (existing and existing["source"] == MANUAL) else source,
            "episodes": dict(existing["episodes"]) if existing else {},
        }
        self._entries[key] = entry
        added = self._write_episodes(entry, meta, today)
        self._save()
        return added

    def update_show(self, meta, today):
        """Add newly aired episodes of a show already in the library."""
        entry = self._entries.get(self.key("series", meta.id))
        if entry is None:
            return 0
        added = self._write_episodes(entry, meta, today)
        if added:
            self._save()
        return added

    def _write_episodes(self, entry, meta, today):
        show_name = safe_name(meta.name)
        added = 0
        for video in meta.videos:
            if video.season is None or video.episode is None or not video.is_released(today):
                continue
            if video.id in entry["episodes"]:
                continue
            relative = os.path.join(f"Season {video.season}",
                                    f"{show_name} S{video.season:02d}E{video.episode:02d}.strm")
            self._write(os.path.join(self.root, entry["folder"], relative),
                        self._play_url(meta.type, video.id, meta.id) + "\n")
            entry["episodes"][video.id] = relative
            added += 1
        return added

    def remove(self, type_, id_):
        """Delete a title's files and forget it. Returns the removed entry or None."""
        entry = self._entries.pop(self.key(type_, id_), None)
        if entry is None:
            return None
        folder = os.path.join(self.root, entry["folder"])
        if os.path.commonpath([os.path.abspath(folder), os.path.abspath(self.root)]) == os.path.abspath(self.root):
            shutil.rmtree(folder, ignore_errors=True)
        self._save()
        return entry
