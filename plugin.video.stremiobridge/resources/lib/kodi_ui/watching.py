"""Watch state in the UI: Continue Watching, Next Up, mark watched, MDBList."""

import dataclasses
import datetime
import json
import os
import time

import xbmcgui
import xbmcplugin

from mdblist import MDBListAuthError, MDBListError, sync as mdblist_sync
from stremio import StremioError
from stremio.aggregate import gather
from stremio.meta import CINEMETA_URL, cinemeta_fallback, fetch_meta_source, slim_meta
from stremio.models import Meta, MetaPreview, external_ids
from stremio.watchstate import PlaybackEntry, next_episode

from .common import (
    ADDON, L, add_context_menu, busy, get_cache, get_client, get_mdblist, get_registry, get_watchstate, log, notify,
    notify_widgets, profile_dir, refresh_container,
)
from .details import PAGE, index_if_long, load_meta, show_index
from .listitems import (
    PLAYABLE_TYPES, apply_info_actions, apply_meta_info, apply_title_actions, apply_watch, content_for, episode_item,
    item_menu, playable_entry, preview_items,
)
from .router import route
from .views import end_listing, set_content

NEXT_UP_SHOWS = 200
CONTINUE_PAGE = 20      # Continue Watching shows this many, then a Next page tile


# ------------------------------------------------------------------ lists

@route("continue")
def continue_watching(plugin, page="1"):
    """Part-watched movies and episodes, newest first; with the setting on, also
    the next episode of shows you're watching (Netflix-style single row).
    CONTINUE_PAGE at a time; a widget pages in place (see browse.row_page)."""
    from .browse import add_next_page, browsing_in_videos_window, row_key, row_start, row_state

    handle = plugin.handle
    page = max(1, int(page))
    key = row_key("continue", "", "") if page == 1 and not browsing_in_videos_window() else None
    row = row_state(key) if key else None
    if row and row["pages"]:
        page = row["pages"][-1][0]
    state = get_watchstate()
    rows = state.continue_watching()
    metas = resume_metas(rows)
    entries = [(row.updated_at, _resume_item(plugin, row, metas.get((_owner(row), row.video_id))), row.is_episode)
               for row in rows]
    if ADDON.getSettingBool("continue_with_next_up"):
        started = {row.meta_id for row in rows if row.is_episode}
        activity = {show_id: when for show_id, _, when in state.recent_shows(NEXT_UP_SHOWS, with_time=True)}
        for meta, video in next_up(state):
            if meta.id not in started:  # its in-progress episode is listed already
                entries.append((activity.get(meta.id, 0.0),
                                _next_episode_item(plugin, meta, video, state, remove_label=L(30192)), True))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    xbmcplugin.setPluginCategory(handle, L(30180))
    more = len(entries) > page * CONTINUE_PAGE
    entries = entries[(page - 1) * CONTINUE_PAGE:page * CONTINUE_PAGE]
    items = [item for _, item, _ in entries]
    if row is not None:
        row_start(plugin, key, row, 0, list="continue")
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    if more and key:
        add_next_page(plugin, list="continue", skip=page + 1)
    elif more:
        add_next_page(plugin, url=plugin.url_for("continue", page=page + 1))
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


# Continue Watching and Next Up keep slim copies of the metas they show (the
# show plus the one episode), so they don't parse long-running shows' whole
# episode lists every time a list or widget opens.
SLIM_SECONDS = 6 * 3600


def resume_metas(rows):
    """``{(owner id, video id): Meta}`` for Continue Watching rows: watch state
    keeps only titles and art, so descriptions, cast etc. come from here."""
    cache = get_cache()
    metas, missing = {}, []
    for owner, type_, video_id in dict.fromkeys((_owner(row), row.type, row.video_id) for row in rows):
        hit = cache.get(f"slim:{type_}:{owner}:{video_id}")
        meta = Meta.from_dict(hit[0], type_) if hit else None
        if meta is not None:
            metas[(owner, video_id)] = meta
        else:
            missing.append((owner, type_, video_id))
    if missing:
        wanted = {}
        for owner, _, video_id in missing:
            wanted.setdefault(owner, set()).add(video_id)
        found = fetch_slim_metas(list(dict.fromkeys((owner, type_) for owner, type_, _ in missing)),
                                 lambda meta: [v for v in meta.videos if v.id in wanted.get(meta.id, ())])
        for owner, type_, video_id in missing:
            if owner in found:
                slim = slim_meta(found[owner], video_id)
                cache.set(f"slim:{type_}:{owner}:{video_id}", slim, SLIM_SECONDS)
                metas[(owner, video_id)] = Meta.from_dict(slim, type_)
    return metas


def preferred_previews(previews):
    """Cinemeta's lists come with Cinemeta's own, thinner details (no cast
    photos, for one). With another meta addon first in line for a type (e.g.
    AIOMetadata), its details replace them, as everywhere else. Each title's
    details are cached (slimmed); titles it can't describe stay as they were."""
    registry, cache = get_registry(), get_cache()
    preferred = {}
    for type_ in {p.type for p in previews}:
        addons = registry.addons_for("meta", type_, "tt0000001")
        preferred[type_] = bool(addons) and addons[0].transport_url != CINEMETA_URL
    wanted = [p for p in previews if preferred.get(p.type) and p.id.startswith("tt")]
    if not wanted:
        return previews
    metas, missing = {}, []
    for preview in wanted:
        hit = cache.get(f"slim:{preview.type}:{preview.id}:")
        meta = Meta.from_dict(hit[0], preview.type) if hit else None
        if meta is not None:
            metas[preview.id] = meta
        else:
            missing.append((preview.id, preview.type))
    if missing:
        types = dict(missing)
        for show_id, data in fetch_slim_metas(missing).items():
            slim = data
            cache.set(f"slim:{types[show_id]}:{show_id}:", slim, SLIM_SECONDS)
            meta = Meta.from_dict(slim, types[show_id])
            if meta is not None:
                metas[show_id] = meta
    # The richer meta, keeping the list's own id and type (what its links use).
    return [dataclasses.replace(metas[p.id], id=p.id, type=p.type) if p.id in metas else p for p in previews]


def _resume_item(plugin, row, meta=None):
    if row.is_episode:
        label = f"{row.show_title} – {row.season}x{row.episode:02d}. {row.title}".strip(" –.")
    else:
        label = row.title or row.video_id
    left = time_left(row)
    item = xbmcgui.ListItem(label, label2=left, offscreen=True)
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
    episode = None
    if row.is_episode:
        tag.setTvShowTitle(row.show_title)
        tag.setSeason(row.season)
        tag.setEpisode(row.episode)
        episode = next((v for v in meta.videos if v.id == row.video_id), None) if meta is not None else None
    apply_watch(item, row)
    if meta is not None and (episode is not None or not row.is_episode):
        apply_info_actions(item, plugin, meta, episode, row.watched)
    else:  # no meta for it: the info page's buttons still act on this movie or episode
        owner = row.meta_id or row.video_id
        apply_title_actions(item, plugin, row.type, owner, row.watched,
                            row.video_id if owner != row.video_id else None, browse=row.is_episode)
    add_context_menu(item, [(L(30192), plugin.run_url("clear_resume", id=row.video_id))]
                     + item_menu(plugin, row.type, row.video_id, row.watched, {"meta": row.meta_id or None},
                                 show_id=row.meta_id or None))
    return playable_entry(plugin, item, row.type, row.video_id, meta=row.meta_id or None)


def _next_episode_item(plugin, meta, video, state, remove_label=None):
    row = state.get(video.id)
    url, item, folder = episode_item(plugin, meta, video, True, row)
    item.setLabel(f"{meta.name} – {item.getLabel()}")
    add_context_menu(item, [(remove_label or L(30364), plugin.run_url("dismiss_show", id=meta.id))])
    return url, item, folder


def fetch_metas(shows):
    """``{show_id: Meta}`` for ``[(show_id, type), ...]``, fetched in parallel (cached).
    A long-running show's comes from its index: episode numbers and dates only."""
    registry, client = get_registry(), get_client()
    metas, rest = {}, []
    for show_id, type_ in shows:
        index = show_index(type_, show_id, registry, client)
        if index is not None:
            metas[index.meta.id] = index.meta
        else:
            rest.append((show_id, type_))
    for _, _, meta in _fetch_sources(rest, registry, client).values():
        metas[meta.id] = meta
    return metas


def fetch_slim_metas(shows, pick=None):
    """``{id: the addon's meta dict, with only some videos}`` for ``[(id, type), ...]``:
    the videos ``pick(Meta)`` returns (none without `pick`). What Continue
    Watching and Next Up keep (see slim_meta); a long-running show's comes from
    its index, without reading its whole episode list."""
    registry, client = get_registry(), get_client()
    slim, rest = {}, []
    for show_id, type_ in shows:
        index = show_index(type_, show_id, registry, client)
        videos = list(pick(index.meta)) if index is not None and pick else []
        found = []
        for season in {v.season for v in videos}:
            raw = index.raw_season(client.cache, season) or []
            found += [d for d in raw if isinstance(d, dict) and d.get("id") in {v.id for v in videos}]
        if index is None or len(found) < len(videos):  # no index, or its seasons are gone
            rest.append((show_id, type_))
        else:
            slim[show_id] = dict(index.summary, videos=found)
    for show_id, (_, raw, meta) in _fetch_sources(rest, registry, client).items():
        wanted = {v.id for v in pick(meta)} if pick else set()
        slim[show_id] = dict(raw, videos=[v for v in raw.get("videos") or []
                                          if isinstance(v, dict) and v.get("id") in wanted])
    return slim


def _fetch_sources(shows, registry=None, client=None):
    """``{id: (transport url, meta dict, Meta)}`` for ``[(id, type), ...]``, fetched
    in parallel (cached); long-running shows are indexed on the way."""
    if not shows:
        return {}
    registry, client = registry or get_registry(), client or get_client()
    use_cinemeta = ADDON.getSettingBool("cinemeta_fallback")

    def task(show_id, type_):
        def run():
            fallbacks = cinemeta_fallback(type_, show_id) if use_cinemeta else []
            _, url, raw, meta = fetch_meta_source(client, registry.addons_for("meta", type_, show_id), type_,
                                                  show_id, fallbacks)
            index_if_long(client, type_, show_id, url, raw, meta)
            return show_id, (url, raw, meta)
        return run

    results, errors, _ = gather([(show_id, task(show_id, type_)) for show_id, type_ in shows])
    for label, exc in errors:
        log(f"No meta for {label}: {exc}")
    return dict(result for _, result in results)


def next_up(state, limit=NEXT_UP_SHOWS):
    """``[(meta, next_video)]`` for recently watched shows, in recency order.
    Reused (as slim metas) until the watch state changes, the day changes or
    SLIM_SECONDS pass, whichever is first."""
    today = datetime.date.today().isoformat()
    cache = get_cache()
    key = f"nextup:{state.signature()}:{today}:{limit}"
    hit = cache.get(key)
    if hit:
        return _from_slim(hit[0])
    shows = state.recent_shows(limit)

    def upcoming(meta):
        video = next_episode(meta, state.watched_episodes(meta.id), today)
        return [video] if video is not None else []

    found = fetch_slim_metas(shows, upcoming)
    saved = [{"type": type_, "video": found[show_id]["videos"][0]["id"], "meta": found[show_id]}
             for show_id, type_ in shows if found.get(show_id, {}).get("videos")]
    cache.set(key, saved, SLIM_SECONDS)
    return _from_slim(saved)


def _from_slim(saved):
    entries = []
    for entry in saved:
        meta = Meta.from_dict(entry["meta"], entry["type"])
        video = next((v for v in meta.videos if v.id == entry["video"]), None) if meta else None
        if video is not None:
            entries.append((meta, video))
    return entries


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
        from .infodialog import refresh_info_page  # infodialog imports this module

        refresh_info_page(plugin)  # marked from the info page (e.g. its show browser): show the new state


@route("similar")
def similar(plugin, type, id, panel=None):
    """Similar titles from MDBList's recommendations. `panel=1`: for the info
    page's Find similar panel, which stays quiet (Kodi's busy dialog hides the
    page; an empty panel says enough)."""
    handle = plugin.handle
    quiet = panel == "1"
    client = get_mdblist()
    if client is None:
        if not quiet:
            notify(L(30212), icon=xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=quiet)
        return
    try:
        if quiet:
            found = client.recommendations(type, id)
        else:
            with busy():
                found = client.recommendations(type, id)
    except MDBListError as exc:
        log(f"MDBList recommendations failed: {exc}")
        if not quiet:
            notify(L(30214), icon=xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(handle, succeeded=quiet)
        return
    previews = [p for p in (MetaPreview.from_dict(entry) for entry in found) if p]
    if not previews:
        if not quiet:
            notify(L(30229))
        xbmcplugin.endOfDirectory(handle, succeeded=quiet)
        return
    xbmcplugin.setPluginCategory(handle, L(30226))
    items = preview_items(plugin, previews, get_watchstate())
    if quiet:  # the info page's panel
        for _, item, _ in items:
            item.setProperty(PAGE, id)
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
