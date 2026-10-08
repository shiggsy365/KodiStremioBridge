"""Extended info: our own info page with clickable cast and action buttons.

Layout: resources/skins/Default/1080i/stremiobridge-info.xml. The dialog only
collects what the user picked; the route acts on it after the dialog closes
(navigating or starting playback while a modal dialog is open is unreliable).
"""

import datetime
import os
import time

import xbmc
import xbmcgui
import xbmcplugin

from stremio import StremioError
from stremio.models import CAST
from stremio.watchstate import next_episode

from mdblist import MDBListError, overall_rating

from .common import (
    ADDON, L, busy, clock_text, get_client, get_mdblist, get_watchstate, library_enabled, log, notify,
    refresh_when_idle, skin_active,
)
from .details import INFO_WINDOW, SEASONS_READY, load_show, play_trailer
from .library import LIBRARY_TYPES, in_library
from .watchlist import change_watchlist, on_watchlist
from .listitems import (
    GENRE_SEPARATOR, PLAYABLE_TYPES, apply_info_actions, apply_watch, episode_code, episode_listitem,
    mark_item_watched, meta_item, season_label,
)
from .router import route
from .watching import mark_watched

XML = "stremiobridge-info.xml"
CAST_LIST, ACTIONS = 50, 9000
PLAY, STREAMS, TRAILER, SAME_DIRECTOR, WATCHED, SIMILAR, LIBRARY, WATCHLIST = (
    "play", "streams", "trailer", "director", "watched", "similar", "library", "watchlist")
ACCENT = "FF12A0C7"
DETAIL_ROWS = 8  # one-line rows in the details box (see the skin XML)


def icon_path(name):
    return os.path.join(ADDON.getAddonInfo("path"), "resources", "media", "icons", name + ".png")


def _duration(seconds):
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


class InfoDialog(xbmcgui.WindowXMLDialog):
    """Set `meta`, `playable`, `watched`, `resume_at` and `can_similar` before doModal().
    Afterwards `choice` is None or one of ("play",), ("trailer",), ("similar",),
    ("search", name); `toggle_watched` is called for the watched button."""

    meta = None
    episode = None  # a Video of `meta`: the page is about that episode
    playable = True
    watched = False
    resume_at = 0.0
    can_similar = False
    overall = None  # (score 0-100, number of sources)
    toggle_watched = None
    in_library = None      # None: not offered for this type; else True/False
    toggle_library = None
    on_watchlist = None    # None: no MDBList; else True/False
    toggle_watchlist = None
    choice = None
    watched_changed = False

    def onInit(self):
        meta, episode = self.meta, self.episode
        year = f" [COLOR FF999999]({meta.year})[/COLOR]" if meta.year else ""
        if episode is not None:
            number = f"{episode.season}x{episode.episode:02d}. " if episode.season is not None else ""
            title = episode.title or L(30065, episode=episode.episode)
            self.setProperty("title", f"{meta.name}  [COLOR FF999999]{number}{title}[/COLOR]")
        else:
            self.setProperty("title", f"{meta.name}{year}")
        self.setProperty("poster", meta.poster)
        self.setProperty("fanart", (episode.thumbnail if episode else "") or meta.background)
        self.setProperty("plot", (episode.overview if episode else "") or meta.description)
        details = self._details()
        for index in range(DETAIL_ROWS):
            self.setProperty(f"detail{index + 1}", details[index] if index < len(details) else "")
        self._refresh_badges()

        cast = [p for p in meta.people if p.job == CAST]
        self._cast_names = [p.name for p in cast]
        items = []
        for person in cast:
            item = xbmcgui.ListItem(person.name, label2=person.role)
            item.setArt({"thumb": person.photo or "DefaultActor.png"})
            items.append(item)
        self.getControl(CAST_LIST).addItems(items)

        # Icon tiles for the actions that apply to this title.
        available = [PLAY]
        # With autoplay, Play picks a stream itself; Streams lets you choose.
        available += [STREAMS] if self.playable and ADDON.getSettingBool("autoplay") else []
        available += [TRAILER] if meta.trailer else []
        available += [SAME_DIRECTOR] if meta.director else []
        available += [WATCHED]
        available += [SIMILAR] if self.can_similar else []
        available += [LIBRARY] if self.in_library is not None else []
        available += [WATCHLIST] if self.on_watchlist is not None else []
        self._actions = available
        items = []
        for action in available:
            item = xbmcgui.ListItem()
            self._style(item, action)
            items.append(item)
        self.getControl(ACTIONS).addItems(items)
        self.setFocusId(ACTIONS)

    def _look(self, action):
        """(label, icon file) for an action tile, reflecting current state."""
        if action == PLAY:
            return (L(30221), "play") if self.playable else (L(30222), "seasons")
        if action == WATCHED:
            return (L(30191), "unwatched") if self.watched else (L(30190), "watched")
        if action == LIBRARY:
            return (L(30267), "library_remove") if self.in_library else (L(30266), "library_add")
        if action == WATCHLIST:
            return (L(30302), "watchlist_remove") if self.on_watchlist else (L(30301), "watchlist_add")
        return {STREAMS: (L(30366), "streams"), TRAILER: (L(30223), "trailer"), SAME_DIRECTOR: (L(30224), "director"),
                SIMILAR: (L(30226), "similar")}[action]

    def _style(self, item, action):
        label, icon = self._look(action)
        item.setLabel(label)
        item.setArt({"icon": icon_path(icon)})

    def _restyle(self, action):
        """Update one tile after a toggle (the dialog stays open)."""
        if action in self._actions:
            self._style(self.getControl(ACTIONS).getListItem(self._actions.index(action)), action)

    def _details(self):
        meta = self.meta

        def line(label, values):
            values = [v for v in values if v]
            return f"[COLOR {ACCENT}]{label}:[/COLOR] {' / '.join(values)}" if values else ""

        lines = [
            line(L(30059), meta.director),
            line(L(30058), meta.writer),
            line(L(30231), meta.genres),
            line(L(30232), [meta.type.replace(".", " ").replace("_", " ").title()]),
            line(L(30239), [f"{round(self.overall[0])}/100"] if self.overall else []),
            line(L(30235), [(self.episode.air_date if self.episode else "") or meta.premiered or meta.release_info]),
            line(L(30238), [meta.country]),
        ]
        return [l for l in lines if l][:DETAIL_ROWS]

    def _refresh_badges(self):
        meta = self.meta
        badges = []
        if meta.runtime_seconds:
            badges.append(_duration(meta.runtime_seconds))
        if meta.imdb_rating is not None:
            badges.append(f"IMDb {meta.imdb_rating:.1f}")
        if meta.certification:
            badges.append(meta.certification)
        if self.watched:
            badges.append(L(30236))
        elif self.resume_at:
            badges.append(L(30237, time=clock_text(self.resume_at)))
        self.setProperty("badges", "     ".join(f"[B]{b}[/B]" for b in badges))

    def onClick(self, control_id):
        if control_id == CAST_LIST:
            position = self.getControl(CAST_LIST).getSelectedPosition()
            if 0 <= position < len(self._cast_names):
                self._choose(("search", self._cast_names[position]))
        elif control_id == ACTIONS:
            position = self.getControl(ACTIONS).getSelectedPosition()
            if 0 <= position < len(self._actions):
                self._run(self._actions[position])

    def _run(self, action):
        if action == PLAY:
            self._choose(("play",))
        elif action == STREAMS:
            self._choose(("streams",))
        elif action == TRAILER:
            self._choose(("trailer",))
        elif action == SAME_DIRECTOR:
            self._choose(("search", self.meta.director[0]))
        elif action == SIMILAR:
            self._choose(("similar",))
        elif action == WATCHLIST and self.toggle_watchlist and self.toggle_watchlist(not self.on_watchlist):
            self.on_watchlist = not self.on_watchlist
            self._restyle(WATCHLIST)
        elif action == LIBRARY and self.toggle_library and self.toggle_library(not self.in_library):
            self.in_library = not self.in_library
            self._restyle(LIBRARY)
        elif action == WATCHED and self.toggle_watched and self.toggle_watched(not self.watched):
            self.watched = not self.watched
            self.watched_changed = not self.watched_changed
            self.resume_at = 0.0
            self._restyle(WATCHED)
            self._refresh_badges()

    def _choose(self, choice):
        self.choice = choice
        self.close()


def kodi_info(plugin, meta, episode, video_id, state, season=None):
    """With our skin: Kodi's own Information page for the title, one `season`
    of it, or an `episode`. `video_id` is what Kodi's own Play button would play
    (None for a show); the skin uses its own buttons instead, which run the
    links in the item's ``stremiobridge.*`` properties (see apply_info_actions)."""
    if episode:
        item = episode_listitem(meta, episode)
    else:
        item = meta_item(meta)
        if season is not None:
            tag = item.getVideoInfoTag()
            tag.setMediaType("season")
            tag.setTvShowTitle(meta.name)
            tag.setSeason(season)
            tag.setTitle(season_label(season))
            item.setLabel(season_label(season))
    if video_id is not None:
        row = state.get(video_id)
        apply_watch(item, row)
        params = {"meta": meta.id} if video_id != meta.id else {}
        item.setPath(plugin.url_for("play", type=meta.type, id=video_id, **params))
        item.setProperty("IsPlayable", "true")
        watched = bool(row and row.watched)
    else:
        item.setPath(plugin.url_for("meta", type=meta.type, id=meta.id))
        item.setIsFolder(True)
        today = datetime.date.today().isoformat()
        aired = {(v.season, v.episode) for v in meta.videos
                 if v.season and v.is_released(today) and (season is None or v.season == season)}
        watched = bool(aired) and aired <= state.watched_episodes(meta.id)
        mark_item_watched(item, watched)
    apply_info_actions(item, plugin, meta, episode, watched, season)
    for name, value in page_text(meta, episode, state, season).items():
        item.setProperty(f"stremiobridge.{name}", value)
    services = _services(meta)
    if services:
        item.setProperty("stremiobridge.service", services[0][0])
        item.setProperty("stremiobridge.service_name", services[0][1])
    _close_info()  # another title's page (e.g. from Similar): a fresh page, not the old one's view
    xbmcgui.Dialog().info(item)


def page_text(meta, episode, state, season=None, rows=None):
    """The info page's text (Arctic Zephyr Stremio's layout, Includes_StremioInfo.xml):
    the Play button's label, the facts row (year, seasons or runtime), genres,
    age rating, rating, and for an episode its code and title. `rows` is
    watch state already looked up (``{video_id: Row}``), for a list of episodes."""
    from .details import visible_seasons

    if episode is not None:
        target = episode
    elif meta.videos:
        target = episode_to_play(meta, state, season)
    else:
        target = None
    video_id = target.id if target else meta.default_video_id or meta.id
    row = rows.get(video_id) if rows is not None else state.get(video_id)
    resume = bool(row and row.position > 0 and not row.watched)
    code = episode_code(target)
    if code:
        play = L(30409 if resume else 30408, season=target.season, episode=target.episode)
    else:
        play = L(30407) if resume else L(30221)

    facts = []
    year = int(episode.air_date[:4]) if episode is not None and episode.air_date else meta.year
    if year:
        facts.append(str(year))
    seasons = [s for s in visible_seasons(meta) if s != 0] if meta.videos and episode is None else []
    if seasons:
        facts.append(L(30411) if len(seasons) == 1 else L(30410, count=len(seasons)))
    if meta.runtime_seconds:
        facts.append(_duration(meta.runtime_seconds))
    rating = episode.rating if episode is not None else meta.imdb_rating
    text = {
        "play_label": play,
        "facts": "   ".join(facts),
        "genres": "  •  ".join(meta.genres),
        "certification": meta.certification,
        "rating": f"{rating:.1f}" if rating else "",
        "title": meta.name,
        "subtitle": "",
    }
    if episode is not None:
        text["subtitle"] = "  ".join(part for part in (episode_code(episode), episode.title) if part)
    return text


def _services(meta):
    """Streaming services (of Streaming Catalogs) showing this title now."""
    from stremio.streaming import services_for

    try:
        return services_for(get_client(), meta.type, meta.id)
    except StremioError as exc:
        log(f"Streaming Catalogs unavailable: {exc}")
        return []


def _close_info():
    if xbmc.getCondVisibility(f"Window.IsVisible({INFO_WINDOW})"):
        xbmc.executebuiltin(f"Dialog.Close({INFO_WINDOW},true)")
        monitor = xbmc.Monitor()
        for _ in range(20):
            if not xbmc.getCondVisibility(f"Window.IsVisible({INFO_WINDOW})") or monitor.waitForAbort(0.1):
                break


@route("info_play")
def info_play(plugin, type, id, video=None, season=None, pick=None):
    """The info page's Play and Available streams buttons. `video` is an
    episode; for a show (or one `season` of it) the next episode to watch is
    played, else its first unwatched aired one."""
    _close_info()
    if video is None and type not in PLAYABLE_TYPES:
        meta = load_show(type, id)  # numbers and dates are enough to choose the episode
        if meta is None:
            return
        if meta.videos:
            episode = episode_to_play(meta, get_watchstate(), None if season is None else int(season))
            if episode is None:
                notify(L(30406))
                return
            video = episode.id
        else:
            video = meta.default_video_id or meta.id
    video = video or id
    play_with_resume_choice(plugin, type, video, id if video != id else None, pick=pick == "1")


_to_play = {}


def episode_to_play(meta, state, season=None):
    """The episode a show's (or season's) Play button starts, or None.
    Remembered for this process: a show's season list asks once per season."""
    today = datetime.date.today().isoformat()
    watched = frozenset(state.watched_episodes(meta.id))
    key = (meta.id, len(meta.videos), season, today, watched)
    if key not in _to_play:
        if len(_to_play) > 200:  # the service lives on: don't grow for ever
            _to_play.clear()
        _to_play[key] = _episode_to_play(meta, watched, today, season)
    return _to_play[key]


def _episode_to_play(meta, watched, today, season):
    from .details import visible_seasons

    if season is None:
        upcoming = next_episode(meta, watched, today)
        if upcoming is not None:
            return upcoming
        videos = [v for s in visible_seasons(meta) if s != 0 for v in meta.episodes(s)] or meta.videos
    else:
        videos = meta.episodes(season)
    aired = [v for v in videos if v.is_released(today)]
    return next((v for v in aired if (v.season, v.episode) not in watched), aired[0] if aired else None)


def refresh_info_page(plugin):
    """If the skin's info page is open, show it again with the current watch
    state (the page's title is in the sbinfo.* properties DialogVideoInfo.xml sets)."""
    if not skin_active() or not xbmc.getCondVisibility(f"Window.IsVisible({INFO_WINDOW})"):
        return
    home = xbmcgui.Window(10000)
    type_, id_ = home.getProperty("sbinfo.type"), home.getProperty("sbinfo.id")
    if type_ and id_:
        refresh_when_idle()
        extended_info(plugin, type_, id_, home.getProperty("sbinfo.video") or None,
                      home.getProperty("sbinfo.season") or None)


@route("info_toggle")
def info_toggle(plugin, what, type, id, value, video=None, season=None):
    """The info page's Mark watched and Watchlist buttons: change it, then show
    the page again with the new state."""
    on = bool(int(value))
    if what == "watched":
        done = mark_watched(type, video or id, on, id if video else None, season)
    else:
        done = change_watchlist(type, id, on)
    if done:
        refresh_when_idle()
        extended_info(plugin, type, id, video, season)


# The info page's browser lists reload when this home property changes
# (Includes_Stremio.xml adds it to their paths); the page itself stays.
BROWSER_REVISION = "sbinfo.browser_rev"


@route("info_mark")
def info_mark(plugin, type, id, value, video=None, season=None, tab=None):
    """Mark watched from a season tab's or episode card's menu, then reload
    the season tabs and episodes in place, on season `tab` (the one showing)."""
    on = bool(int(value))
    if not mark_watched(type, video or id, on, id if video else None, season):
        return
    home = xbmcgui.Window(10000)
    if tab:
        home.clearProperty(SEASONS_READY)  # the episodes reload on `tab`, not wherever the tabs are mid-reload
        home.setProperty("sbinfo.season_focus", tab)
    if video:
        home.setProperty("sbinfo.focus_video", video)
        if home.getProperty("sbinfo.video") == video:  # the header is about this episode
            home.setProperty("sbinfo.watched", "1" if on else "")
    home.setProperty(BROWSER_REVISION, str(int(time.time() * 1000)))


@route("extended_info")
def extended_info(plugin, type, id, video=None, season=None):
    """`id` is the movie or show; `video` an episode of the show, which the
    page is then about (Play, Streams and Mark watched act on it), or `season`
    one season of it (with our skin). Returns True if the user went somewhere
    from the dialog (played, searched, ...)."""
    if skin_active() and plugin.handle >= 0:
        # Selected in a list or widget: Kodi waits on us (busy, which hides its
        # info page) until we answer, so answer first, then open the page from
        # this same call (another call would start Python again: most of a
        # second on a Fire TV Stick).
        xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())
        plugin.handle = -1
    meta = load_show(type, id, videos={video} if video else ())
    if meta is None:
        return False
    state = get_watchstate()
    episode = next((v for v in meta.videos if v.id == video), None) if video else None
    playable = episode is not None or type in PLAYABLE_TYPES or not meta.videos
    video_id = episode.id if episode else meta.default_video_id or meta.id
    if skin_active():
        whole_season = int(season) if season is not None and episode is None else None
        kodi_info(plugin, meta, episode, video_id if playable else None, state, whole_season)
        return False

    dialog = InfoDialog(XML, ADDON.getAddonInfo("path"), "Default", "1080i")
    dialog.meta = meta
    dialog.episode = episode
    dialog.playable = playable
    mdblist = get_mdblist()
    dialog.can_similar = mdblist is not None
    if mdblist is not None:
        try:
            with busy():
                dialog.overall = overall_rating(mdblist.item(type, meta.id))
        except MDBListError as exc:
            log(f"MDBList ratings unavailable: {exc}")
    if playable:
        row = state.get(video_id)
        dialog.watched = bool(row and row.watched)
        dialog.resume_at = row.position if row else 0.0
        dialog.toggle_watched = lambda watched: mark_watched(type, video_id, watched,
                                                            meta.id if video_id != meta.id else None)
    else:
        today = datetime.date.today().isoformat()
        aired = {(v.season, v.episode) for v in meta.videos if v.season and v.is_released(today)}
        dialog.watched = bool(aired) and aired <= state.watched_episodes(meta.id)
        dialog.toggle_watched = lambda watched: mark_watched(type, meta.id, watched)
    if type in LIBRARY_TYPES:
        if library_enabled():
            dialog.in_library = in_library(type, meta.id)
            dialog.toggle_library = lambda add: _toggle_library(plugin, type, meta.id, add)
        dialog.on_watchlist = on_watchlist(type, meta.id)
        dialog.toggle_watchlist = lambda add: change_watchlist(type, meta.id, add)
    dialog.doModal()
    choice, changed = dialog.choice, dialog.watched_changed
    del dialog

    if changed:
        refresh_when_idle()  # the listing behind the dialog shows the new watched state
    if choice is None:
        return False
    action = choice[0]
    if action == "search":
        from .searchwindow import search_window  # searchwindow imports this module

        search_window(plugin, query=choice[1], person="1")
    elif action == "similar":
        open_folder(plugin.url_for("similar", type=type, id=meta.id))
    elif action == "trailer":
        play_trailer(plugin, type, meta.id, yt=meta.trailer)
    elif action == "play" and not playable:
        open_folder(plugin.url_for("meta", type=type, id=meta.id))
    elif action in ("play", "streams"):
        return play_with_resume_choice(plugin, type, video_id, meta.id if video_id != meta.id else None,
                                       pick=action == "streams")
    return True


def _toggle_library(plugin, type_, id_, add):
    """Add/remove from the library while the dialog stays open; True if it worked."""
    from .library import library_add, library_remove

    (library_add if add else library_remove)(plugin, type_, id_)
    return in_library(type_, id_) == add


def play_with_resume_choice(plugin, type_, video_id, meta_id=None, pick=False):
    """Start playback, asking "Resume from…" first if there's a resume point.
    With `pick`, the stream list is shown even with autoplay on. False if the
    user cancelled that question."""
    resume = None
    row = get_watchstate().get(video_id)
    if row and row.position > 0:
        choice = xbmcgui.Dialog().contextmenu([L(30227, time=clock_text(row.position)), L(30228)])
        if choice < 0:
            return False
        resume = "1" if choice == 0 else "0"
    url = plugin.url_for("play", type=type_, id=video_id, meta=meta_id, resume=resume, pick=1 if pick else None)
    # noresume: asked already. Without it Kodi asks again whenever it has its own
    # bookmark for this URL (it keeps one per plugin URL played); the playback
    # service seeks to our resume point instead.
    xbmc.executebuiltin(f"PlayMedia({url},noresume)")
    return True


def open_folder(url):
    xbmc.executebuiltin(f"ActivateWindow(Videos,{url},return)")
