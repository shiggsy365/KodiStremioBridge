"""Background service: tracks playback started by this add-on.

The play route announces what it's about to play (see
``common.announce_playback``); when Kodi starts that video, the tracker saves
progress to the watch database, scrobbles to MDBList, resumes where asked and
hands the next episode to Up Next. Playback from other add-ons is ignored.
"""

import base64
import datetime
import json
import queue
import threading
import time

import xbmc

from mdblist import MDBListError, scrobble_payload
from stremio import StremioError
from stremio.aggregate import gather
from stremio.catalog import fetch_catalog, with_default_filters
from stremio.watchstate import RESUME, WATCHED, next_episode

from .common import (
    ADDON, ADDON_ID, L, get_client, get_mdblist, get_registry, get_watchstate, jsonrpc, log, notify,
    notify_widgets, plugin_url, refresh_when_idle,
    take_announced_playback, take_announcement,
)

SAVE_EVERY = 15            # seconds between progress saves while playing
SCROBBLE_EVERY = 600       # seconds between "still watching" scrobbles
SEEK_DELAY = 1.5           # let playback settle before seeking to the resume point
SEEK_TOLERANCE = 30        # Kodi already resumed if we're within this many seconds
UPNEXT = "service.upnext"


class Worker:
    """Runs network jobs one at a time off the player thread."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="stremiobridge-worker", daemon=True)
        self._thread.start()

    def submit(self, fn, *args):
        self._jobs.put((fn, args))

    def _run(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, args = job
            try:
                fn(*args)
            except (MDBListError, StremioError, OSError) as exc:
                log(f"Background job {getattr(fn, '__name__', fn)} failed: {exc}", xbmc.LOGWARNING)
            except Exception as exc:  # noqa: BLE001 - never kill the worker
                log(f"Background job {getattr(fn, '__name__', fn)} crashed: {exc!r}", xbmc.LOGERROR)

    def stop(self):
        self._jobs.put(None)
        self._thread.join(timeout=5)


class Tracker(xbmc.Player):
    def __init__(self, worker):
        super().__init__()
        self.worker = worker
        self.entry = None
        self._reset()

    def _reset(self):
        self.position = self.duration = 0.0
        self.pending_seek = 0.0
        self.started = self.last_save = self.last_scrobble = 0.0

    # ------------------------------------------------------------ Kodi callbacks

    def onAVStarted(self):
        announced = take_announced_playback()
        if announced is None:
            self.entry = None  # something else is playing
            return
        self.entry, self.pending_seek = announced
        now = time.time()
        self.started = self.last_save = self.last_scrobble = now
        self.position, self.duration = 0.0, self._total_time()
        get_watchstate().touch(self.entry)  # remembers the source group right away
        log(f"Tracking {self.entry.type} {self.entry.video_id}")
        self._scrobble("start")
        if self.entry.is_episode and ADDON.getSettingBool("upnext_enabled") and \
                xbmc.getCondVisibility(f"System.HasAddon({UPNEXT})"):
            self.worker.submit(signal_upnext, self.entry)

    def onPlayBackPaused(self):
        if self.entry:
            self._save()
            self._scrobble("pause")

    def onPlayBackResumed(self):
        if self.entry:
            self._scrobble("start")

    def onPlayBackStopped(self):
        self._finish(ended=False)

    def onPlayBackEnded(self):
        self._finish(ended=True)

    def onPlayBackError(self):
        # A play announced but not started yet is the one that failed: playing
        # again straight after a Stop, Kodi can report the error before the
        # previous video's stop reaches us. Otherwise it's the tracked video.
        announced = take_announcement()
        if self.entry:
            self._finish(ended=False)
            if announced is None:
                return
        # Failed before the video started: if the play route said how, try
        # again (autoplay only; a Stop before starting is the user cancelling).
        retry = (announced or {}).get("retry")
        if retry:
            self.worker.submit(retry_next_stream, retry)

    # ------------------------------------------------------------ polling

    def tick(self):
        if not self.entry or not self.isPlayingVideo():
            return
        try:
            self.position = self.getTime()
            self.duration = self.getTotalTime() or self.duration
        except RuntimeError:
            return
        now = time.time()
        if self.pending_seek and now - self.started >= SEEK_DELAY:
            if self.position < self.pending_seek - SEEK_TOLERANCE:
                log(f"Resuming at {int(self.pending_seek)}s")
                self.seekTime(self.pending_seek)
            self.pending_seek = 0.0
        if now - self.last_save >= SAVE_EVERY:
            self._save()
        if now - self.last_scrobble >= SCROBBLE_EVERY:
            self._scrobble("start")

    # ------------------------------------------------------------ helpers

    def _total_time(self):
        try:
            return self.getTotalTime()
        except RuntimeError:
            return 0.0

    def _save(self):
        self.last_save = time.time()
        return get_watchstate().record(self.entry, self.position, self.duration)

    def _scrobble(self, event):
        self.last_scrobble = time.time()
        if not ADDON.getSettingBool("mdblist_scrobble"):
            return
        client = get_mdblist()
        progress = self.position / self.duration * 100 if self.duration else 0.0
        payload = scrobble_payload(self.entry, progress, ADDON.getAddonInfo("version"))
        if client is not None and payload is not None:
            self.worker.submit(client.scrobble, event, payload)

    def _finish(self, ended):
        if not self.entry:
            return
        if ended and self.duration:
            self.position = self.duration
        status = self._save()
        self._scrobble("stop")
        log(f"Stopped {self.entry.video_id} at {int(self.position)}/{int(self.duration)}s: {status or 'not saved'}")
        self.entry = None
        self._reset()
        self.worker.submit(after_playback, status)


# ---------------------------------------------------------------- jobs

MAX_STREAM_RETRIES = 5


def retry_next_stream(retry):
    """After a stream failed to play: the same stream once more first (opening
    it can fail for a moment, e.g. replaying straight after a Stop while the
    server is still closing the last connection), then the next ones."""
    same = retry["tries"] == 0
    start = retry["next"] - 1 if same else retry["next"]
    if start >= retry["total"] or retry["tries"] >= MAX_STREAM_RETRIES:
        log(f"Not retrying {retry['id']}: tried {retry['tries'] + 1} times after playback errors")
        return
    if same:
        log(f"Trying {retry['id']} again")
        xbmc.sleep(1000)
    else:
        notify(L(30178))
    xbmc.executebuiltin("PlayMedia({})".format(plugin_url(
        "play", type=retry["type"], id=retry["id"], meta=retry.get("meta"), binge=retry.get("binge"),
        resume="1" if retry.get("resume") else None, start=start, tries=retry["tries"] + 1)))


def after_playback(status):
    """`status` is what the final save did: WATCHED, RESUME or None."""
    if status not in (WATCHED, RESUME):
        return  # nothing changed (e.g. stopped after a few seconds): leave Kodi alone
    notify_widgets()
    # Update ticks and resume bars in our listing, once Kodi has finished
    # returning from playback (see refresh_when_idle for why it must wait).
    refresh_when_idle()
    if status == WATCHED:
        sync_mdblist()
    sync_library()


def sync_mdblist(force=False):
    from .watching import run_mdblist_sync

    summary = run_mdblist_sync(force=force)
    if summary and (summary["added"] or summary["removed"] or summary["resumes"]):
        sync_library()


def sync_library():
    """Bring Kodi's library copies of exported titles in line with our watch state."""
    from .common import library_enabled
    from .library import sync_library_watched

    if library_enabled():
        sync_library_watched()


def update_library():
    from .common import library_enabled
    from .library import update_library as update

    if library_enabled():
        update()


# ------------------------------------------------------------------ Kodi's own "Mark as watched"

def watched_target(path):
    """``(type, video id, show id or None)`` for one of our playable items' paths
    (its play or Extended info link), else None."""
    from urllib.parse import parse_qsl, urlsplit

    if not path.startswith(f"plugin://{ADDON_ID}/"):
        return None
    query = dict(parse_qsl(urlsplit(path).query))
    action, type_, id_ = query.get("action"), query.get("type"), query.get("id")
    if not type_ or not id_:
        return None
    if action == "play":
        return type_, id_, query.get("meta")
    if action == "extended_info":
        return (type_, query["video"], id_) if query.get("video") else (type_, id_, None)
    return None


def follow_kodi_watched(path, watched):
    """Record a watched change Kodi made to one of our items."""
    from .watching import mark_watched

    target = watched_target(path)
    if target is None:
        return
    type_, video_id, meta_id = target
    row = get_watchstate().get(video_id)
    if bool(row and row.watched) != watched:
        log(f"Kodi marked {video_id} {'watched' if watched else 'unwatched'}: following")
        if mark_watched(type_, video_id, watched, meta_id if meta_id != video_id else None):
            refresh_when_idle()


def kodi_playcount(path):
    """Kodi's own play count for a plugin path (its video database), 0 if none."""
    import glob
    import os
    import sqlite3

    import xbmcvfs

    folder = xbmcvfs.translatePath("special://database/")
    databases = sorted(glob.glob(os.path.join(folder, "MyVideos*.db")),
                       key=lambda name: int("".join(c for c in os.path.basename(name) if c.isdigit()) or 0))
    if not databases:
        return 0
    base = path.partition("?")[0]  # Kodi keeps plugin paths whole as the file name
    try:
        with sqlite3.connect(f"file:{databases[-1]}?mode=ro", uri=True, timeout=2) as db:
            row = db.execute("SELECT files.playCount FROM files JOIN path ON files.idPath = path.idPath "
                             "WHERE path.strPath = ? AND files.strFilename = ?", (base, path)).fetchone()
    except sqlite3.Error as exc:
        log(f"Kodi's video database unreadable: {exc}")
        return 0
    return int(row[0] or 0) if row else 0


def align_kodi_playcount(path, watched):
    """Make Kodi's play count for our item agree with the watched tick we gave
    it, so either of Kodi's marks then shows up as a change."""
    if (kodi_playcount(path) > 0) != watched:
        jsonrpc("Files.SetFileDetails", file=path, media="video", playcount=1 if watched else 0)


class KodiWatchedFollower:
    """Kodi's context menu has its own Mark as watched / unwatched, which only
    changes Kodi's copy (its video database). Kodi's count for the focused item
    is kept in step with ours, then compared before and after a context menu; a
    change is recorded here too, so MDBList, Continue Watching and Next Up follow. Checked once a
    second."""

    SETTLE = 1.5  # Kodi writes the change shortly after the menu closes

    def __init__(self, worker):
        self.worker = worker
        self.focused = None     # our item's path, as of the last check without a menu
        self.pending = None     # (path, Kodi's play count when the menu opened)
        self.menu_open = False
        self.check_at = None

    def tick(self):
        if xbmc.getCondVisibility("Window.IsActive(contextmenu)"):
            if not self.menu_open:
                self.menu_open = True
                self.pending = (self.focused, kodi_playcount(self.focused)) if self.focused else None
            return
        if self.menu_open:
            self.menu_open = False
            self.check_at = time.time() + self.SETTLE if self.pending else None
            return
        if self.check_at is not None:
            if time.time() < self.check_at:
                return
            self.check_at = None
            path, before = self.pending
            after = kodi_playcount(path)
            if (after > 0) != (before > 0):
                self.worker.submit(follow_kodi_watched, path, after > 0)
        path = xbmc.getInfoLabel("ListItem.FileNameAndPath")
        path = path if watched_target(path) else None
        if path and path != self.focused:
            align_kodi_playcount(path, (xbmc.getInfoLabel("ListItem.PlayCount") or "0") != "0")
        self.focused = path


class MenuSwap(threading.Thread):
    """With Arctic Zephyr Stremio: Kodi's context menu on one of our titles is
    swapped for ours (contextmenu.py), whose entries come in our order. Kodi's
    menu can't say which item it's for, so the focused item is tracked here.
    The same loop runs the info page's episode follower (one thread: Kodi's
    Python can crash when several service threads start importing at once,
    so everything is imported here, before the thread starts)."""

    POLL = 0.1
    SETTLE_AFTER_OURS = 0.6  # seconds: our menu's closing animation

    def __init__(self, monitor):
        super().__init__(name="stremiobridge-menus", daemon=True)
        import xbmcgui

        from .common import skin_active
        from .contextmenu import CLOSED_PROPERTY, OPEN_PROPERTY, focused_item, item_target

        self.monitor = monitor
        self.home = xbmcgui.Window(10000)
        self.skin_active, self.open_property, self.closed_property = skin_active, OPEN_PROPERTY, CLOSED_PROPERTY
        self.focused_item, self.item_target = focused_item, item_target
        self.follower = InfoPageFollower(self.home)

    def run(self):
        home = self.home
        focused, source, active, checked = None, "ListItem", False, 0.0
        while not self.monitor.waitForAbort(self.POLL):
            if time.time() - checked > 5:
                active, checked = self.skin_active(), time.time()
            if not active:
                continue
            if xbmc.getCondVisibility("Window.IsActive(contextmenu)"):
                # A menu Kodi opened, for the item focused just before (with a menu open,
                # Control.HasFocus is about the menu, so `focused` is kept from then).
                # Not ours: it sets the open property while it shows, and for a moment
                # after it closes it's still on screen.
                closed = float(home.getProperty(self.closed_property) or 0)
                if focused and not home.getProperty(self.open_property) and time.time() - closed > self.SETTLE_AFTER_OURS:
                    home.setProperty(self.open_property, "1")  # ours is on its way (the route clears it)
                    xbmc.executebuiltin("Dialog.Close(contextmenu,true)")
                    xbmc.executebuiltin(f"RunPlugin({plugin_url('context_menu', path=focused, source=source)})")
                continue
            if xbmc.getCondVisibility("Window.IsVisible(contextmenu)"):
                continue  # a menu closing: focus isn't back on the item yet, keep the one we have
            source, path = self.focused_item()
            focused = path if self.item_target(path) else None
            self.follower.tick()


class InfoPageFollower:
    """Arctic Zephyr Stremio's info page: once an episode card has had focus
    for a moment, the page's header (text, Play, Streams, Watched) is about
    that episode. The skin reads the header from home-window properties
    (sbinfo.*); they're copied here from the card's own properties."""

    SETTLE = 1.0   # seconds on one card before the header follows it
    EPISODES = "Container(5061).ListItem"
    TEXT = ("play_label", "facts", "rating", "subtitle")
    ACTIONS = ("play_action", "streams_action", "watched_action")

    def __init__(self, home):
        self.home = home
        self.seen, self.since, self.shown = "", 0.0, ""

    def tick(self):
        if not xbmc.getCondVisibility("Window.IsVisible(movieinformation)"):
            self.seen = self.shown = ""
            return
        if not xbmc.getCondVisibility("Control.HasFocus(5061)"):
            self.seen = ""
            return
        video = xbmc.getInfoLabel(f"{self.EPISODES}.Property(stremiobridge.video)")
        if video != self.seen:
            self.seen, self.since = video, time.time()
        elif video and video != self.shown and time.time() - self.since >= self.SETTLE:
            self.show(video)
            self.shown = video

    def show(self, video):
        home = self.home

        def get(label):
            return xbmc.getInfoLabel(f"{self.EPISODES}.{label}")

        for name in self.TEXT:
            home.setProperty(f"sbinfo.{name}", get(f"Property(stremiobridge.page.{name})"))
        for name in self.ACTIONS:
            home.setProperty(f"sbinfo.{name}", get(f"Property(stremiobridge.{name})"))
        home.setProperty("sbinfo.plot", get("Plot"))
        home.setProperty("sbinfo.video", video)
        home.setProperty("sbinfo.watched", "1" if (get("PlayCount") or "0") != "0" else "")


class LibraryMonitor(xbmc.Monitor):
    """Reacts to Kodi's library: its own watched changes on our exported items,
    and finished scans (newly added items get our watched state)."""

    def __init__(self, worker):
        super().__init__()
        self.worker = worker

    def onSettingsChanged(self):
        from .keymap import apply_keymap

        apply_keymap()

    def onNotification(self, sender, method, data):
        if method == "VideoLibrary.OnUpdate":
            from .library import on_library_update

            try:
                payload = json.loads(data or "{}")
            except ValueError:
                return
            self.worker.submit(on_library_update, payload)
        elif method == "VideoLibrary.OnScanFinished":
            self.worker.submit(sync_library)


def _upnext_episode(meta, video):
    return {
        "episodeid": video.id,
        "tvshowid": meta.id,
        "title": video.title,
        "art": {
            "thumb": video.thumbnail or meta.background,
            "tvshow.clearart": "",
            "tvshow.clearlogo": meta.logo,
            "tvshow.fanart": meta.background,
            "tvshow.landscape": meta.background,
            "tvshow.poster": meta.poster,
        },
        "season": video.season,
        "episode": video.episode,
        "showtitle": meta.name,
        "plot": video.overview,
        "playcount": 0,
        "rating": meta.imdb_rating or 0,
        "firstaired": video.air_date,
        "runtime": meta.runtime_seconds or 0,
    }


def signal_upnext(entry):
    """Tell Up Next what follows this episode; it calls our play URL (with the
    same source group) when the user continues."""
    from .details import load_meta

    meta = load_meta(entry.type, entry.meta_id, quiet=True)
    if meta is None:
        return
    current = next((v for v in meta.videos if v.id == entry.video_id), None)
    following = next_episode(meta, {(entry.season, entry.episode)}, datetime.date.today().isoformat())
    if current is None or following is None:
        return
    data = {
        "current_episode": _upnext_episode(meta, current),
        "next_episode": _upnext_episode(meta, following),
        "play_url": plugin_url("play", type=entry.type, id=following.id, meta=meta.id,
                               binge=entry.binge_group or None),
    }
    encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "JSONRPC.NotifyAll",
        "params": {"sender": f"{ADDON_ID}.SIGNAL", "message": "upnext_data", "data": [encoded]},
    }))
    log(f"Up Next: {entry.video_id} -> {following.id}")


# ---------------------------------------------------------------- main loop

LIBRARY_UPDATE_EVERY = 24 * 3600
PREWARM_EVERY = 30 * 60


def prewarm():
    """Refresh the lists you're likely to open (front-page and home catalogs,
    Continue Watching, Next Up, the watchlist) in the background, so they open instantly."""
    if not ADDON.getSettingBool("prewarm") or xbmc.Player().isPlaying():
        return
    from .watching import next_up, resume_metas  # these import the UI modules; only needed here
    from .watchlist import watchlist_items

    client = get_client()
    catalogs = get_registry().home_catalogs()

    def task(addon, catalog):
        return lambda: fetch_catalog(client, addon, catalog, with_default_filters(catalog, {}), refresh=True)

    _, errors, _ = gather([(f"{a.name} / {c.name}", task(a, c)) for a, c in catalogs])
    state = get_watchstate()
    resume_metas(state.continue_watching())
    next_up(state)
    if get_mdblist() is not None:
        watchlist_items(refresh=True)
    log(f"Pre-warmed {len(catalogs) - len(errors)} catalogs, Continue Watching, Next Up and the watchlist")


def refresh_skin_hubs():
    """Arctic Zephyr Stremio: keep its Movies/Series hubs in step with the catalogs."""
    from .skinhelper import update_skin_hubs

    if update_skin_hubs() and not xbmc.Player().isPlaying():
        xbmc.executebuiltin("ReloadSkin()")


def run():
    worker = Worker()
    monitor = LibraryMonitor(worker)
    tracker = Tracker(worker)
    follower = KodiWatchedFollower(worker)
    MenuSwap(monitor).start()
    from .keymap import apply_keymap

    apply_keymap()
    next_sync = time.time() + 60             # first syncs shortly after Kodi starts
    next_library = time.time() + 120
    next_prewarm = time.time() + 180
    next_skin_hubs = time.time() + 20
    log("Service started")
    while not monitor.waitForAbort(1):
        tracker.tick()
        follower.tick()
        if time.time() >= next_sync:
            next_sync = time.time() + max(1, ADDON.getSettingInt("mdblist_sync_hours")) * 3600
            worker.submit(sync_mdblist)
        if time.time() >= next_library:
            next_library = time.time() + LIBRARY_UPDATE_EVERY
            worker.submit(update_library)
        if time.time() >= next_prewarm:
            next_prewarm = time.time() + PREWARM_EVERY
            worker.submit(prewarm)
        if next_skin_hubs and time.time() >= next_skin_hubs:
            next_skin_hubs = None  # once per start: the hubs follow the catalogs you have
            worker.submit(refresh_skin_hubs)
    worker.stop()
    log("Service stopped")
