"""Item details: show -> seasons -> episodes, and the info dialog."""

import datetime
import time

import xbmc
import xbmcgui
import xbmcplugin

from stremio import StremioError, showindex
from stremio.client import resource_url
from stremio.meta import cinemeta_fallback, fetch_meta_source, meta_candidates
from stremio.models import CAST, DIRECTOR, WRITER
from stremio.watchstate import next_episode

from .common import ADDON, L, add_context_menu, get_client, get_registry, get_watchstate, log, notify
from .listitems import (
    YOUTUBE_PLUGIN, apply_info_actions, apply_watch, episode_code, episode_item, episode_listitem, mark_item_watched,
    season_item, season_label, single_video_item,
)
from .router import route
from .views import end_listing, set_content, set_focus


def _meta_candidates(type_, id_, registry=None):
    fallbacks = cinemeta_fallback(type_, id_) if ADDON.getSettingBool("cinemeta_fallback") else []
    return (registry or get_registry()).addons_for("meta", type_, id_), fallbacks


def index_if_long(client, type_, id_, url, raw, meta):
    """After reading `meta` (`raw` from transport `url`): index a long-running
    show (stremio.showindex) unless it's indexed already."""
    if len(meta.videos) < showindex.MIN_VIDEOS or client.cache is None:
        return
    key = resource_url(url, "meta", type_, id_)
    if not showindex.is_current(client.cache, key) and showindex.save(client.cache, key, raw):
        log(f"Indexed {type_} {id_}: {len(meta.videos)} videos")


def load_meta(type_, id_, quiet=False):
    """``Meta`` from the first addon that has it, or None (after telling the user,
    unless `quiet`)."""
    addons, fallbacks = _meta_candidates(type_, id_)
    client = get_client()
    try:
        source, url, raw, meta = fetch_meta_source(client, addons, type_, id_, fallbacks)
    except StremioError as exc:
        log(str(exc))
        if not quiet:
            notify(L(30063), icon=xbmcgui.NOTIFICATION_ERROR)
        return None
    log(f"Meta for {type_} {id_} from {source}")
    index_if_long(client, type_, id_, url, raw, meta)
    return meta


def show_index(type_, id_, registry=None, client=None):
    """The ShowIndex of a long-running show (stremio.showindex), or None: then
    load_meta (which makes one when the show is long)."""
    addons, fallbacks = _meta_candidates(type_, id_, registry)
    candidates = meta_candidates(addons, fallbacks)
    client = client or get_client()
    if not candidates or client.cache is None:
        return None
    # The first addon's: the one load_meta reads first
    return showindex.load(client.cache, resource_url(candidates[0][1], "meta", type_, id_), type_, time.time())


def load_show(type_, id_, quiet=False, seasons=(), videos=()):
    """load_meta for the busy paths (the info page, playback). A long-running
    show comes from its index: every episode's id, numbers and air date, and
    titles, plots and pictures only for `seasons` (or ``seasons(meta)``, from
    the index's meta) and the seasons of `videos` (ids). Anything else, or
    without an index, is load_meta's whole meta."""
    index = show_index(type_, id_)
    if index is not None:
        wanted = set(seasons(index.meta) if callable(seasons) else seasons)
        if videos:
            wanted |= {v.season for v in index.meta.videos if v.id in videos}
        meta = index.with_seasons(get_client().cache, wanted)
        if meta is not None:
            return meta
    return load_meta(type_, id_, quiet)


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
        today = datetime.date.today().isoformat()
        aired = {s: [v for v in meta.episodes(s) if v.is_released(today)] for s in seasons}
        # Aired episodes only, like Mark as watched: a season is watched once all of them are.
        items = [season_item(plugin, meta, s, len(aired[s]),
                             sum(1 for v in aired[s] if (v.season, v.episode) in watched))
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


@route("info_seasons")
def info_seasons(plugin, type, id, focus=None, rev=None):
    """Season chips shown inside Arctic Zephyr Stremio's information page;
    season `focus` is selected. `rev` only changes the path, to reload it."""
    handle = plugin.handle
    meta = load_show(type, id, quiet=True)
    if meta is None:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        xbmcgui.Window(10000).setProperty(SEASONS_READY, "1")  # the episodes and Similar wait for it
        return
    seasons = visible_seasons(meta)
    watched = get_watchstate().watched_episodes(meta.id)
    today = datetime.date.today().isoformat()
    items = []
    for season in seasons:
        item = xbmcgui.ListItem(season_label(season), offscreen=True)
        item.setProperty("season", str(season))
        item.setProperty(PAGE, id)
        add_context_menu(item, [])  # the skin's hand-over marker: our menu (Mark as watched)
        aired = {(v.season, v.episode) for v in meta.episodes(season) if v.is_released(today)}
        mark_item_watched(item, bool(aired) and aired <= watched)
        # The season's own path: its context menu (contextmenu.py) is the season's.
        items.append((plugin.url_for("season", type=type, id=id, season=season), item, True))
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    positions = [str(s) for s in seasons]
    if items:
        position = positions.index(focus) if focus in positions else 0
        select_when_shown(INFO_SEASONS, position, items[position][0])
    # The episodes follow the tabs from now on (until now: the page's season; Includes_Stremio.xml)
    xbmcgui.Window(10000).setProperty(SEASONS_READY, "1")


@route("info_episodes")
def info_episodes(plugin, type, id, season="", focus=None, rev=None):
    """Episode cards embedded in the information page. Selecting one plays it
    (its play_action: autoplay or the stream list, as the settings say); resting
    on one puts it in the page's header. Episode `focus` (a video id) is selected."""
    handle = plugin.handle

    def season_shown(meta):
        """The season to show: `season`, else the first."""
        seasons = visible_seasons(meta)
        try:
            selected = int(season)
        except (TypeError, ValueError):
            selected = None
        return selected if selected in seasons else (seasons[0] if seasons else None)

    meta = load_show(type, id, quiet=True, seasons=lambda meta: {season_shown(meta)})
    if meta is None:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    selected = season_shown(meta)
    from .infodialog import page_text  # infodialog imports this module

    state = get_watchstate()
    today = datetime.date.today().isoformat()
    hide_unreleased = ADDON.getSettingBool("hide_unaired")
    videos = meta.episodes(selected) if selected is not None else meta.videos
    rows = state.lookup([v.id for v in videos])
    items, shown = [], []
    for video in videos:
        released = video.is_released(today)
        if not released and hide_unreleased:
            continue
        item = episode_listitem(meta, video, released)
        row = rows.get(video.id)
        apply_watch(item, row)
        apply_info_actions(item, plugin, meta, video, bool(row and row.watched), header=False)
        item.setProperty("IsPlayable", "false")
        item.setProperty("stremiobridge.code", episode_code(video))
        item.setProperty(PAGE, id)
        add_context_menu(item, [])  # the skin's hand-over marker: our menu (Mark as watched)
        if video.rating:
            item.setProperty("stremiobridge.rating", f"{video.rating:.1f}")
        # The page's header shows these while the card has focus (Includes_StremioInfo.xml)
        for name, value in page_text(meta, video, state, rows=rows).items():
            item.setProperty(f"stremiobridge.page.{name}", value)
        items.append((plugin.url_for("extended_info", type=type, id=id, video=video.id), item, False))
        shown.append(video.id)
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.setContent(handle, "episodes")
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    # The list keeps its old position when it loads another season: select the
    # episode to watch next in the page's own season, else the season's first.
    if focus in shown:
        select_when_shown(INFO_EPISODES, shown.index(focus), items[shown.index(focus)][0])
    elif items:
        select_when_shown(INFO_EPISODES, 0, items[0][0])


INFO_WINDOW = "movieinformation"
INFO_SEASONS, INFO_EPISODES = 5060, 5061  # the skin's show browser lists (Includes_Stremio.xml)
SEASONS_READY = "sbinfo.seasons_ready"
# On the info page's list items: the page's title. Kodi keeps showing a list's last
# items until the new listing arrives, so the skin hides a list whose items are
# another title's (the previous page's).
PAGE = "stremiobridge.page"


def select_when_shown(control, position, path, timeout=5.0):
    """Select item `position` (its path is `path`) of the info page's list
    `control` once Kodi shows the listing just returned. The skin can't: the
    list loads after the page opens. Gives up if the page closes or another
    listing shows up instead."""
    if position < 0:
        return
    monitor = xbmc.Monitor()
    for _ in range(int(timeout / 0.1)):
        if not xbmc.getCondVisibility(f"Window.IsVisible({INFO_WINDOW})"):
            return
        if xbmc.getInfoLabel(f"Container({control}).ListItemAbsolute({position}).FileNameAndPath") == path:
            _move_to(control, position, monitor)
            return
        if monitor.waitForAbort(0.1):
            return
    log(f"Info page list {control} never showed {path}; not selecting item {position}")


def _move_to(control, position, monitor, timeout=2.0):
    """Move list `control` to item `position` and wait until Kodi has: a new
    listing first keeps the old one's position, and Kodi can apply a move from
    that, or late (callers go on to rely on the selection, e.g. the episodes
    follow the season tabs once they're in place)."""
    moved_from = None
    for _ in range(int(timeout / 0.05)):
        current = int(xbmc.getInfoLabel(f"Container({control}).CurrentItem") or 1) - 1
        if current == position:
            return True
        if current != moved_from:  # no move yet, or Kodi applied the last one from elsewhere
            xbmc.executebuiltin(f"Control.Move({control},{position - current})")
            moved_from = current
        if monitor.waitForAbort(0.05):
            return False
    log(f"Info page list {control} didn't move to item {position}")
    return False


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
        item = xbmcgui.ListItem(name, label2=" · ".join(entry["roles"]), offscreen=True)
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
