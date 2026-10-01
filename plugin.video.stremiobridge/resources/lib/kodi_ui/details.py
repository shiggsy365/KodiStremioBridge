"""Item details: show -> seasons -> episodes, and the info dialog."""

import datetime

import xbmc
import xbmcgui
import xbmcplugin

from stremio import StremioError
from stremio.meta import cinemeta_fallback, fetch_meta
from stremio.models import CAST, DIRECTOR, WRITER
from stremio.watchstate import next_episode

from .common import ADDON, L, get_client, get_registry, get_watchstate, log, notify
from .listitems import YOUTUBE_PLUGIN, episode_item, season_item, season_label, single_video_item
from .router import route
from .views import end_listing, set_content, set_focus


def load_meta(type_, id_, quiet=False):
    """``Meta`` from the first addon that has it, or None (after telling the user,
    unless `quiet`)."""
    registry = get_registry()
    fallbacks = cinemeta_fallback(type_, id_) if ADDON.getSettingBool("cinemeta_fallback") else []
    try:
        source, meta = fetch_meta(
            get_client(), registry.addons_for("meta", type_, id_), type_, id_, fallbacks
        )
    except StremioError as exc:
        log(str(exc))
        if not quiet:
            notify(L(30063), icon=xbmcgui.NOTIFICATION_ERROR)
        return None
    log(f"Meta for {type_} {id_} from {source}")
    return meta


def visible_seasons(meta):
    seasons = meta.seasons
    if not ADDON.getSettingBool("show_specials") and len(seasons) > 1:
        seasons = [s for s in seasons if s != 0]
    return seasons


@route("meta")
def meta_view(plugin, type, id):
    handle = plugin.handle
    meta = load_meta(type, id)
    if meta is None:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    xbmcplugin.setPluginCategory(handle, meta.name)

    seasons = visible_seasons(meta)
    state = get_watchstate()
    if not meta.videos:
        video_id = meta.default_video_id or meta.id
        xbmcplugin.addDirectoryItem(handle, *single_video_item(plugin, meta, state.lookup([video_id]).get(video_id)))
        set_content(handle, "videos")
    elif len(seasons) > 1:
        watched = state.watched_episodes(meta.id)
        items = [season_item(plugin, meta, s, len(meta.episodes(s)),
                             sum(1 for v in meta.episodes(s) if (v.season, v.episode) in watched))
                 for s in seasons]
        xbmcplugin.addDirectoryItems(handle, items, len(items))
        set_content(handle, "seasons")
        upcoming = _next_to_watch(meta, state)
        if upcoming is not None and upcoming.season in seasons:
            set_focus(seasons.index(upcoming.season))  # open on the season you're in
    else:
        # One season, or a channel whose videos have no seasons: skip the season level.
        _add_episodes(plugin, meta, seasons[0] if seasons else None, state)
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle)


@route("season")
def season_view(plugin, type, id, season):
    handle = plugin.handle
    meta = load_meta(type, id)
    if meta is None:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    season = int(season)
    xbmcplugin.setPluginCategory(handle, f"{meta.name} / {season_label(season)}")
    _add_episodes(plugin, meta, season, get_watchstate())
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle)


@route("people")
def people(plugin, type, id):
    """Cast & crew, with photos. Selecting someone searches for their name,
    which is what Stremio's cast links do."""
    handle = plugin.handle
    meta = load_meta(type, id)
    if meta is None:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    xbmcplugin.setPluginCategory(handle, f"{meta.name} / {L(30066)}")
    jobs = {DIRECTOR: L(30059), WRITER: L(30058)}
    # One entry per person: crew jobs are combined ("Director · Writer"); the
    # first photo found is used.
    entries = {}
    for person in sorted(meta.people, key=lambda p: {DIRECTOR: 0, WRITER: 1, CAST: 2}[p.job]):
        entry = entries.setdefault(person.name, {"roles": [], "photo": ""})
        role = person.role or jobs.get(person.job, "")
        if role and role not in entry["roles"]:
            entry["roles"].append(role)
        entry["photo"] = entry["photo"] or person.photo
    items = []
    for name, entry in entries.items():
        item = xbmcgui.ListItem(name, label2=" · ".join(entry["roles"]))
        art = entry["photo"] or "DefaultActor.png"
        item.setArt({"thumb": art, "icon": art, "poster": art})
        items.append((plugin.url_for("search_window", query=name, person=1), item, False))
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.endOfDirectory(handle)


@route("play_trailer")
def play_trailer(plugin, type, id, yt=None):
    if not yt:
        meta = load_meta(type, id, quiet=True)
        yt = meta.trailer if meta else ""
    if not yt:
        notify(L(30068))
    elif not xbmc.getCondVisibility("System.HasAddon(plugin.video.youtube)"):
        notify(L(30069), icon=xbmcgui.NOTIFICATION_WARNING)
    else:
        xbmc.executebuiltin(f"PlayMedia({YOUTUBE_PLUGIN.format(yt)})")


def _next_to_watch(meta, state):
    """The next aired episode after the furthest one watched, or None."""
    return next_episode(meta, state.watched_episodes(meta.id), datetime.date.today().isoformat())


def _add_episodes(plugin, meta, season, state):
    today = datetime.date.today().isoformat()
    hide_unreleased = ADDON.getSettingBool("hide_unaired")
    videos = meta.episodes(season)
    rows = state.lookup([v.id for v in videos])
    items, shown = [], []
    for video in videos:
        released = video.is_released(today)
        if released or not hide_unreleased:
            items.append(episode_item(plugin, meta, video, released, rows.get(video.id)))
            shown.append(video.id)
    xbmcplugin.addDirectoryItems(plugin.handle, items, len(items))
    upcoming = _next_to_watch(meta, state)
    if upcoming is not None and upcoming.id in shown:
        set_focus(shown.index(upcoming.id))  # open on the next episode to watch
    set_content(plugin.handle, "episodes" if season is not None else "videos")
