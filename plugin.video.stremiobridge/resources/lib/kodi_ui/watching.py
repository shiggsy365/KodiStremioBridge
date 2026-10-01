"""Watch state in the UI: Continue Watching, Next Up, mark watched, MDBList."""

import datetime
import json
import os
import time

import xbmcgui
import xbmcplugin

from mdblist import MDBListAuthError, MDBListError, sync as mdblist_sync
from stremio import StremioError
from stremio.aggregate import gather
from stremio.meta import cinemeta_fallback, fetch_meta
from stremio.models import MetaPreview, external_ids
from stremio.watchstate import PlaybackEntry, next_episode

from .common import (
    ADDON, L, add_context_menu, busy, get_client, get_mdblist, get_registry, get_watchstate, log, notify,
    notify_widgets, profile_dir, refresh_container,
)
from .details import load_meta
from .listitems import (
    PLAYABLE_TYPES, apply_meta_info, apply_watch, content_for, episode_item, item_menu, playable_entry, preview_items,
)
from .router import route
from .views import end_listing, set_content

NEXT_UP_SHOWS = 30


# ------------------------------------------------------------------ lists

@route("continue")
def continue_watching(plugin):
    """Part-watched movies and episodes, newest first; with the setting on, also
    the next episode of shows you're watching (Netflix-style single row)."""
    handle = plugin.handle
    state = get_watchstate()
    rows = state.continue_watching()
    metas = resume_metas(rows)
    entries = [(row.updated_at, _resume_item(plugin, row, metas.get(_owner(row))), row.is_episode) for row in rows]
    if ADDON.getSettingBool("continue_with_next_up"):
        started = {row.meta_id for row in rows if row.is_episode}
        activity = {show_id: when for show_id, _, when in state.recent_shows(NEXT_UP_SHOWS, with_time=True)}
        for meta, video in next_up(state):
            if meta.id not in started:  # its in-progress episode is listed already
                entries.append((activity.get(meta.id, 0.0),
                                _next_episode_item(plugin, meta, video, state, remove_label=L(30192)), True))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    xbmcplugin.setPluginCategory(handle, L(30180))
    items = [item for _, item, _ in entries]
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    episodes = sum(1 for _, _, is_episode in entries if is_episode)
    set_content(handle, "episodes" if entries and episodes == len(entries)
                else "movies" if not episodes else "videos")
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle, cacheToDisc=False)


def time_left(row):
    """"42 min left" / "1 h 05 min left" for a part-watched row, or ""."""
    if not row.duration or row.position <= 0:
        return ""
    minutes = max(1, round((row.duration - row.position) / 60))
    hours, minutes = divmod(minutes, 60)
    return L(30320, time=f"{hours} h {minutes:02d} min" if hours else f"{minutes} min")


def _owner(row):
    """The movie or show a Continue Watching row belongs to."""
    return row.meta_id or row.video_id


def resume_metas(rows):
    """Metas (cached) for Continue Watching rows: watch state keeps only titles
    and art, so descriptions, cast etc. come from here."""
    return fetch_metas(list(dict.fromkeys((_owner(row), row.type) for row in rows)))


def _resume_item(plugin, row, meta=None):
    if row.is_episode:
        label = f"{row.show_title} – {row.season}x{row.episode:02d}. {row.title}".strip(" –.")
    else:
        label = row.title or row.video_id
    left = time_left(row)
    item = xbmcgui.ListItem(label, label2=left)
    item.setProperty("TimeLeft", left)
    poster = row.poster or (meta.poster if meta else "")
    art = {"thumb": row.thumb or poster, "poster": poster, "tvshow.poster": poster,
           "fanart": row.fanart or (meta.background if meta else ""), "clearlogo": meta.logo if meta else ""}
    item.setArt({k: v for k, v in art.items() if v})
    tag = item.getVideoInfoTag()
    tag.setMediaType("episode" if row.is_episode else "movie")
    tag.setTitle(row.title or label)
    tag.setTagLine(left)
    if meta is not None:
        apply_meta_info(tag, meta, next((v for v in meta.videos if v.id == row.video_id), None))
    if row.is_episode:
        tag.setTvShowTitle(row.show_title)
        tag.setSeason(row.season)
        tag.setEpisode(row.episode)
    apply_watch(item, row)
    add_context_menu(item, [(L(30192), plugin.run_url("clear_resume", id=row.video_id))]
                     + item_menu(plugin, row.type, row.video_id, row.watched, {"meta": row.meta_id or None},
                                 show_id=row.meta_id or None, browse_show=row.is_episode))
    return playable_entry(plugin, item, row.type, row.video_id, meta=row.meta_id or None)


def _next_episode_item(plugin, meta, video, state, remove_label=None):
    row = state.get(video.id)
    url, item, folder = episode_item(plugin, meta, video, True, row)
    item.setLabel(f"{meta.name} – {item.getLabel()}")
    add_context_menu(item, [(remove_label or L(30364), plugin.run_url("dismiss_show", id=meta.id))])
    return url, item, folder


def fetch_metas(shows):
    """``{show_id: Meta}`` for ``[(show_id, type), ...]``, fetched in parallel (cached)."""
    registry, client = get_registry(), get_client()
    use_cinemeta = ADDON.getSettingBool("cinemeta_fallback")

    def task(show_id, type_):
        def run():
            fallbacks = cinemeta_fallback(type_, show_id) if use_cinemeta else []
            return fetch_meta(client, registry.addons_for("meta", type_, show_id), type_, show_id, fallbacks)[1]
        return run

    results, errors, _ = gather([(show_id, task(show_id, type_)) for show_id, type_ in shows])
    for label, exc in errors:
        log(f"No meta for {label}: {exc}")
    return {meta.id: meta for _, meta in results}


def next_up(state, limit=NEXT_UP_SHOWS):
    """``[(meta, next_video)]`` for recently watched shows, in recency order."""
    shows = state.recent_shows(limit)
    metas = fetch_metas(shows)
    today = datetime.date.today().isoformat()
    found = []
    for show_id, _ in shows:
        meta = metas.get(show_id)
        video = next_episode(meta, state.watched_episodes(meta.id), today) if meta else None
        if video is not None:
            found.append((meta, video))
    return found


def show_progress(state, shows):
    """``{show_id: (watched, aired)}`` for shows in ``[(show_id, type)]`` that
    have watched episodes (others are skipped: nothing to show)."""
    started = [(show_id, type_) for show_id, type_ in shows if state.watched_episodes(show_id)]
    if not started:
        return {}
    today = datetime.date.today().isoformat()
    progress = {}
    for show_id, meta in fetch_metas(started).items():
        aired = {(v.season, v.episode) for v in meta.videos if v.season and v.is_released(today)}
        progress[show_id] = (len(aired & state.watched_episodes(show_id)), len(aired))
    return progress


@route("next_up")
def next_up_view(plugin):
    handle = plugin.handle
    state = get_watchstate()
    xbmcplugin.setPluginCategory(handle, L(30181))
    entries = next_up(state)
    rows = state.lookup([video.id for _, video in entries])
    items = [_next_episode_item(plugin, meta, video, state) for meta, video in entries
             if not (rows.get(video.id) and rows[video.id].position > 0)]  # those are in Continue Watching
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    set_content(handle, "episodes")
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle, cacheToDisc=False)


# ------------------------------------------------------------------ marking

def _episode_numbers(video_id):
    parts = video_id.split(":")
    try:
        return int(parts[-2]), int(parts[-1])
    except (IndexError, ValueError):
        return None, None


def entries_for(type_, id_, meta_id=None, season=None):
    """PlaybackEntries to mark: a movie, one episode, a season or a whole show."""
    today = datetime.date.today().isoformat()
    whole_show = meta_id is None and type_ not in PLAYABLE_TYPES
    if season is not None or whole_show:
        meta = load_meta(type_, id_)
        if meta is None:
            return []
        ids = dict(meta.external_ids)
        return [
            PlaybackEntry(video_id=v.id, type=meta.type, meta_id=meta.id, season=v.season, episode=v.episode,
                          title=v.title, show_title=meta.name, poster=meta.poster, ids=ids)
            for v in meta.videos
            if v.season and v.episode is not None and v.is_released(today)
            and (season is None or v.season == int(season))
        ]
    meta = load_meta(type_, meta_id or id_, quiet=True)
    ids = dict(meta.external_ids) if meta else external_ids(meta_id or id_)
    if meta_id and meta_id != id_:
        video = next((v for v in meta.videos if v.id == id_), None) if meta else None
        s, e = (video.season, video.episode) if video else _episode_numbers(id_)
        return [PlaybackEntry(video_id=id_, type=type_, meta_id=meta_id, season=s, episode=e,
                              title=video.title if video else "", show_title=meta.name if meta else "",
                              poster=meta.poster if meta else "", ids=ids)]
    return [PlaybackEntry(video_id=id_, type=type_, title=meta.name if meta else "",
                          poster=meta.poster if meta else "", ids=ids)]


def mark_watched(type_, id_, watched, meta_id=None, season=None):
    """Mark a movie, episode, season or show; pushes to MDBList. True if done."""
    with busy():
        entries = entries_for(type_, id_, meta_id, season)
        if not entries:
            return False
        apply_watched(entries, watched)
    return True


def apply_watched(entries, watched):
    """Store watched state for `entries` and pass it on: MDBList, Kodi's library
    copies, and widgets."""
    state = get_watchstate()
    state.set_watched(entries, watched)
    _push_to_mdblist(state, entries, watched)
    from .library import sync_library_watched  # library imports this module

    sync_library_watched(state)
    notify_widgets()


@route("set_watched")
def set_watched(plugin, type, id, value, meta=None, season=None):
    if mark_watched(type, id, bool(int(value)), meta, season):
        refresh_container()


@route("similar")
def similar(plugin, type, id):
    """Similar titles from MDBList's recommendations."""
    handle = plugin.handle
    client = get_mdblist()
    if client is None:
        notify(L(30212), icon=xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    try:
        with busy():
            found = client.recommendations(type, id)
    except MDBListError as exc:
        log(f"MDBList recommendations failed: {exc}")
        notify(L(30214), icon=xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    previews = [p for p in (MetaPreview.from_dict(entry) for entry in found) if p]
    if not previews:
        notify(L(30229))
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    xbmcplugin.setPluginCategory(handle, L(30226))
    items = preview_items(plugin, previews, get_watchstate())
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    types = {p.type for p in previews}
    set_content(handle, content_for(types.pop() if len(types) == 1 else ""))
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle)


def _push_to_mdblist(state, entries, watched):
    client = get_mdblist()
    if client is None:
        return
    try:
        if watched:
            client.add_watched(entries, watched_at=time.time())
            state.mark_synced([e.video_id for e in entries])
        else:
            client.remove_watched(entries)
    except MDBListError as exc:
        # Additions stay unsynced and are retried by the next background sync.
        log(f"MDBList update failed: {exc}")
        notify(L(30211), icon=xbmcgui.NOTIFICATION_WARNING)


@route("clear_resume")
def clear_resume(plugin, id):
    get_watchstate().clear_resume(id)  # an episode also takes its show out of Next Up
    notify_widgets()
    refresh_container()


@route("dismiss_show")
def dismiss_show(plugin, id):
    """Remove a show from Next Up (and the combined Continue Watching) until
    you watch more of it."""
    get_watchstate().dismiss_show(id)
    notify_widgets()
    refresh_container()


@route("clear_watch_history")
def clear_watch_history(plugin):
    if xbmcgui.Dialog().yesno(L(30180), L(30194)):
        get_watchstate().clear()
        notify(L(30195))


# ------------------------------------------------------------------ MDBList

ACTIVITIES_FILE = "mdblist_activities.json"


def run_mdblist_sync(force=False):
    """Sync with MDBList if configured. Returns the summary dict, or None when
    MDBList isn't set up. Raises MDBListError on failure."""
    client = get_mdblist()
    if client is None:
        return None
    path = os.path.join(profile_dir(), ACTIVITIES_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = None
    summary, activities = mdblist_sync(client, get_watchstate(), previous, force=force)
    if summary["added"] or summary["removed"] or summary["resumes"]:
        notify_widgets()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(activities, f)
    log(f"MDBList sync: {summary}")
    return summary


@route("mdblist_test")
def mdblist_test(plugin):
    client = get_mdblist()
    if client is None:
        xbmcgui.Dialog().ok("MDBList", L(30212))
        return
    try:
        with busy():
            client.last_activities()
    except MDBListAuthError:
        xbmcgui.Dialog().ok("MDBList", L(30213))
    except MDBListError as exc:
        xbmcgui.Dialog().ok("MDBList", f"{L(30214)}\n{exc}")
    else:
        xbmcgui.Dialog().ok("MDBList", L(30215))


@route("mdblist_sync")
def mdblist_sync_now(plugin):
    try:
        with busy():
            summary = run_mdblist_sync(force=True)
    except (MDBListError, StremioError) as exc:
        xbmcgui.Dialog().ok("MDBList", f"{L(30214)}\n{exc}")
        return
    if summary is None:
        xbmcgui.Dialog().ok("MDBList", L(30212))
        return
    notify(L(30216, pushed=summary["pushed"], added=summary["added"], removed=summary["removed"]))
