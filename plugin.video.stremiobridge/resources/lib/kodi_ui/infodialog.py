"""Extended info: our own info page with clickable cast and action buttons.

Layout: resources/skins/Default/1080i/stremiobridge-info.xml. The dialog only
collects what the user picked; the route acts on it after the dialog closes
(navigating or starting playback while a modal dialog is open is unreliable).
"""

import datetime
import os

import xbmc
import xbmcgui

from stremio.models import CAST

from mdblist import MDBListError, overall_rating

from .common import ADDON, L, busy, clock_text, get_mdblist, get_watchstate, log, refresh_when_idle
from .details import load_meta, play_trailer
from .library import LIBRARY_TYPES, in_library
from .watchlist import change_watchlist, on_watchlist
from .listitems import PLAYABLE_TYPES
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
        meta = self.meta
        year = f" [COLOR FF999999]({meta.year})[/COLOR]" if meta.year else ""
        self.setProperty("title", f"{meta.name}{year}")
        self.setProperty("poster", meta.poster)
        self.setProperty("fanart", meta.background)
        self.setProperty("plot", meta.description)
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
            line(L(30235), [meta.premiered or meta.release_info]),
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


@route("extended_info")
def extended_info(plugin, type, id):
    """`id` is the movie or show (episodes open their show's page). Returns
    True if the user went somewhere from the dialog (played, searched, ...)."""
    meta = load_meta(type, id)
    if meta is None:
        return False
    state = get_watchstate()
    playable = type in PLAYABLE_TYPES or not meta.videos
    video_id = meta.default_video_id or meta.id

    dialog = InfoDialog(XML, ADDON.getAddonInfo("path"), "Default", "1080i")
    dialog.meta = meta
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
    xbmc.executebuiltin(f"PlayMedia({url})")
    return True


def open_folder(url):
    xbmc.executebuiltin(f"ActivateWindow(Videos,{url},return)")
