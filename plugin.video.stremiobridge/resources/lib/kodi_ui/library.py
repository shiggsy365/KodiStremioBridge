"""My Library: titles the user chose, exported to Kodi's library.

- Add/remove from the context menu or Extended info.
- Shows in the library get newly aired episodes daily (service).
- Optionally, the MDBList watchlist decides what's in it (added/removed with it).
- Watched state: this add-on's watch database is the source of truth; Kodi's
  library copies are updated to match (sync_library_watched), and Kodi's own
  "Mark as watched" on library items is passed back (on_library_update).
"""

import datetime
import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcgui
import xbmcvfs

from mdblist import MDBListError
from stremio import StremioError
from stremio.library import WATCHLIST

from .common import (
    ADDON, L, busy, get_library, get_mdblist, get_watchstate, jsonrpc, library_enabled, log, notify,
    refresh_container,
)
from .details import load_meta
from .router import route

LIBRARY_TYPES = ("movie", "series")
SOURCE_NAMES = {"Movies": "Stremio Bridge Movies", "TV": "Stremio Bridge TV"}


def _today():
    return datetime.date.today().isoformat()


def _library_path(subfolder=""):
    """special:// form of a library folder (what Kodi sources and scans use)."""
    base = f"special://profile/addon_data/{ADDON.getAddonInfo('id')}/Library/"
    return base + (subfolder.replace(os.sep, "/").strip("/") + "/" if subfolder else "")


def scan(folder=""):
    xbmc.executebuiltin(f"UpdateLibrary(video,{_library_path(folder)})")


def clean():
    xbmc.executebuiltin("CleanLibrary(video,false)")


# ------------------------------------------------------------------ setup

def sources_xml_path():
    return xbmcvfs.translatePath("special://profile/sources.xml")


def sources_configured():
    try:
        root = ET.parse(sources_xml_path()).getroot()
    except (OSError, ET.ParseError):
        return False
    paths = {(p.text or "").rstrip("/") for p in root.iter("path")}
    return all(_library_path(folder).rstrip("/") in paths for folder in SOURCE_NAMES)


def add_sources():
    """Add the two library folders to Kodi's video sources (sources.xml).
    Kodi reads that file at start-up, so a restart is needed afterwards."""
    path = sources_xml_path()
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        root = ET.Element("sources")
        tree = ET.ElementTree(root)
    video = root.find("video")
    if video is None:
        video = ET.SubElement(root, "video")
        ET.SubElement(video, "default", pathversion="1")
    existing = {(p.text or "").rstrip("/") for p in video.iter("path")}
    for folder, name in SOURCE_NAMES.items():
        source_path = _library_path(folder)
        os.makedirs(xbmcvfs.translatePath(source_path), exist_ok=True)
        if source_path.rstrip("/") in existing:
            continue
        source = ET.SubElement(video, "source")
        ET.SubElement(source, "name").text = name
        ET.SubElement(source, "path", pathversion="1").text = source_path
        ET.SubElement(source, "allowsharing").text = "true"
    if os.path.exists(path):
        with open(path, "rb") as original, open(path + ".stremiobridge.bak", "wb") as backup:
            backup.write(original.read())
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=False)


def _library_off():
    """True (and says so) when library integration is off for our skin."""
    if library_enabled():
        return False
    xbmcgui.Dialog().ok(L(30260), L(30378))
    return True


@route("library_setup")
def library_setup(plugin):
    if _library_off():
        return
    if sources_configured():
        xbmcgui.Dialog().ok(L(30260), L(30274))
        return
    add_sources()
    xbmcgui.Dialog().ok(L(30260), L(30270))
    if xbmcgui.Dialog().yesno(L(30260), L(30271)):
        xbmc.executebuiltin("RestartApp")


def _ensure_setup():
    """True if the library folders are Kodi sources; offers setup if not."""
    if sources_configured():
        return True
    if xbmcgui.Dialog().yesno(L(30260), L(30273)):
        library_setup(None)
    return False


# ------------------------------------------------------------------ add / remove

def add_title(type_, id_, source="manual", meta=None):
    """Export a movie or show; returns the folder written, or None."""
    library = get_library()
    meta = meta or load_meta(type_, id_, quiet=True)
    if meta is None:
        return None
    if type_ == "movie":
        entry = library.add_movie(meta.id, meta.name, meta.year, dict(meta.external_ids), source=source)
    else:
        library.add_show(meta, _today(), source=source)
        entry = library.get(type_, meta.id)
    return entry["folder"]


@route("library_add")
def library_add(plugin, type, id):
    if _library_off() or not _ensure_setup():
        return
    with busy():
        folder = add_title(type, id)
    if folder is None:
        notify(L(30063), icon=xbmcgui.NOTIFICATION_ERROR)
        return
    notify(L(30268, name=get_library().get(type, id)["title"]))
    scan(folder)
    refresh_container()


@route("library_remove")
def library_remove(plugin, type, id):
    entry = get_library().remove(type, id)
    if entry is None:
        return
    notify(L(30269, name=entry["title"]))
    clean()
    refresh_container()


# ------------------------------------------------------------------ keeping it current

def update_library():
    """New episodes for shows in the library, and the MDBList watchlist (if
    enabled). Returns ``{"episodes", "added", "removed"}``."""
    library = get_library()
    summary = {"episodes": 0, "added": 0, "removed": 0}
    to_scan = []

    for entry in library.entries(type_="series"):
        meta = load_meta("series", entry["id"], quiet=True)
        if meta is not None:
            added = library.update_show(meta, _today())
            if added:
                summary["episodes"] += added
                to_scan.append(entry["folder"])

    client = get_mdblist()
    if client is not None and ADDON.getSettingBool("library_watchlist"):
        try:
            wanted = client.watchlist()
        except MDBListError as exc:
            log(f"MDBList watchlist unavailable: {exc}")
            wanted = None
        if wanted is not None:
            wanted_keys = {library.key(item["type"], item["id"]) for item in wanted}
            for item in wanted:
                if not library.contains(item["type"], item["id"]):
                    folder = add_title(item["type"], item["id"], source=WATCHLIST)
                    if folder:
                        summary["added"] += 1
                        to_scan.append(folder)
            for entry in library.entries(source=WATCHLIST):
                if library.key(entry["type"], entry["id"]) not in wanted_keys:
                    library.remove(entry["type"], entry["id"])
                    summary["removed"] += 1

    for folder in to_scan:
        scan(folder)
    if summary["removed"]:
        clean()
    if any(summary.values()):
        log(f"Library update: {summary}")
    return summary


@route("library_update")
def library_update(plugin):
    if _library_off() or not _ensure_setup():
        return
    with busy():
        summary = update_library()
    notify(L(30272, **summary))


# ------------------------------------------------------------------ watched state

def _library_items():
    """``(kind, id_key, setter, item)`` for every movie/episode in Kodi's library
    whose file belongs to our export."""
    for kind, getter, id_key, setter in (
            ("movies", "VideoLibrary.GetMovies", "movieid", "VideoLibrary.SetMovieDetails"),
            ("episodes", "VideoLibrary.GetEpisodes", "episodeid", "VideoLibrary.SetEpisodeDetails")):
        for item in jsonrpc(getter, properties=["file", "playcount", "resume"]).get(kind) or []:
            yield kind, id_key, setter, item


def _normalise(path):
    return os.path.normcase(os.path.abspath(xbmcvfs.translatePath(path)))


def sync_library_watched(state=None):
    """Make Kodi's library copies match our watched state and resume points.
    Returns the number of library items changed."""
    files = {_normalise(path): ids for path, ids in get_library().file_map().items()}
    if not files:
        return 0
    state = state or get_watchstate()
    rows = state.lookup([video_id for video_id, _ in files.values()])
    changed = 0
    for kind, id_key, setter, item in _library_items():
        ids = files.get(_normalise(item.get("file") or ""))
        if ids is None:
            continue
        row = rows.get(ids[0])
        watched = bool(row and row.watched)
        position = row.position if row and not watched else 0.0
        total = row.duration if row else 0.0
        resume = item.get("resume") or {}
        if (item.get("playcount", 0) > 0) == watched and abs(resume.get("position", 0) - position) < 30:
            continue
        jsonrpc(setter, **{id_key: item[id_key], "playcount": 1 if watched else 0,
                           "resume": {"position": position, "total": total}})
        changed += 1
    return changed


def on_library_update(data):
    """Kodi's own "Mark as watched/unwatched" (or its playback tracking) on a
    library item from our export: pass the change back to our watch state."""
    item = data.get("item") or {}
    if "playcount" not in data or item.get("type") not in ("movie", "episode"):
        return
    method, id_key, details = (("VideoLibrary.GetMovieDetails", "movieid", "moviedetails")
                               if item["type"] == "movie" else
                               ("VideoLibrary.GetEpisodeDetails", "episodeid", "episodedetails"))
    path = (jsonrpc(method, **{id_key: item.get("id"), "properties": ["file"]}).get(details) or {}).get("file")
    if not path:
        return
    files = {_normalise(p): ids for p, ids in get_library().file_map().items()}
    ids = files.get(_normalise(path))
    if ids is None:
        return
    video_id, show_id = ids
    watched = data["playcount"] > 0
    row = get_watchstate().get(video_id)
    if row is not None and row.watched == watched:
        return  # already in step (e.g. this was our own sync)
    from .watching import apply_watched, entries_for

    try:
        entries = entries_for("series" if show_id else "movie", video_id, show_id)
    except StremioError as exc:
        log(f"Library watched change for {video_id} not applied: {exc}")
        return
    if entries:
        apply_watched(entries, watched)


def in_library(type_, id_):
    return type_ in LIBRARY_TYPES and get_library().contains(type_, id_)


def library_menu(plugin, type_, id_):
    """Context-menu entry: Add to / Remove from library (movies and shows)."""
    if type_ not in LIBRARY_TYPES or not library_enabled():
        return []
    if in_library(type_, id_):
        return [(L(30267), plugin.run_url("library_remove", type=type_, id=id_))]
    return [(L(30266), plugin.run_url("library_add", type=type_, id=id_))]
