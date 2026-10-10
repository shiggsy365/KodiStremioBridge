"""Background service: tracks playback started by this add-on.

The play route announces what it's about to play (see
``common.announce_playback``); when Kodi starts that video, the tracker saves
progress to the watch database, scrobbles to MDBList, resumes where asked and
hands the next episode to Up Next. Playback from other add-ons is ignored.
"""

import base64
import datetime
import json
import os
import queue
import threading
import time

import xbmc
import xbmcgui

from mdblist import MDBListError, scrobble_payload
from stremio import StremioError
from stremio.aggregate import gather
from stremio.catalog import fetch_catalog, with_default_filters
from stremio.watchstate import RESUME, WATCHED, next_episode

from .common import (
    ADDON, ADDON_ID, L, get_client, get_mdblist, get_registry, get_watchstate, jsonrpc, log, notify,
    notify_widgets, plugin_url, refresh_when_idle, skin_active, stale_queue_path,
    take_announced_playback, take_announcement, take_stale,
)

SAVE_EVERY = 15            # seconds between progress saves while playing
SCROBBLE_EVERY = 600       # seconds between "still watching" scrobbles
SEEK_DELAY = 1.5           # let playback settle before seeking to the resume point
SEEK_TOLERANCE = 30        # Kodi already resumed if we're within this many seconds
UPNEXT = "service.upnext"


class Worker:
    """Runs network jobs one at a time off the player thread. It ends on its own
    when Kodi stops the service: Kodi waits for every thread of a stopped script,
    and a worker left waiting for jobs (its stop never called, e.g. after Kodi
    killed the service at a profile change) kept Kodi from starting the next
    profile's services."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._monitor = xbmc.Monitor()
        self._thread = threading.Thread(target=self._run, name="stremiobridge-worker", daemon=True)
        self._thread.start()

    def submit(self, fn, *args):
        self._jobs.put((fn, args))

    def _run(self):
        while True:
            try:
                job = self._jobs.get(timeout=1)
            except queue.Empty:
                if self._monitor.abortRequested():
                    return
                continue
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
        self._thread.join(timeout=2)


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
    # noresume: `resume` says it already (Kodi would ask if it had a bookmark for the URL)
    xbmc.executebuiltin("PlayMedia({},noresume)".format(plugin_url(
        "play", type=retry["type"], id=retry["id"], meta=retry.get("meta"), binge=retry.get("binge"),
        resume="1" if retry.get("resume") else "0", start=start, tries=retry["tries"] + 1)))


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
    second, with other skins only: with Arctic Zephyr Stremio our titles get our own menu."""

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


class HomeWidgets:
    """Arctic Zephyr Stremio's home widgets for the logged-in profile
    (homewidgets.py): published as the service starts, and taken over from
    Customise Home when it saves (its properties file changes; checked every
    few seconds, without asking Kodi's GUI anything)."""

    CHECK_EVERY = 5

    def __init__(self):
        from .homewidgets import properties_path

        self.path = properties_path()
        self.mtime = None
        self.checked = 0.0
        self.check(start=True)

    def _mtime(self):
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return None

    def tick(self):
        if time.time() - self.checked >= self.CHECK_EVERY:
            self.check()

    def check(self, start=False):
        from .homewidgets import publish, take_over
        from .skinhelper import rebuild_menus

        self.checked = time.time()
        mtime = self._mtime()
        if not start and mtime == self.mtime:
            return
        if not skin_active():
            self.mtime = mtime
            return
        if xbmcgui.Window(10000).getProperty("skinshortcuts-isrunning"):
            return  # it's building the menu from the file: next time
        moved = take_over()
        self.mtime = self._mtime()  # take_over may have rewritten it
        publish()
        if moved:
            # The menu holds the choice itself: rebuilt (when the home screen next
            # opens) to hold the references again. Once per change, not per login.
            rebuild_menus()


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
        elif method == UPNEXT_PLAY and sender == "upnextprovider.SIGNAL":
            play_from_upnext(data)


def play_from_upnext(data):
    """Up Next's UPNEXT_PLAY: `data` is the notification's data, a JSON list
    holding our play_info, base64-encoded JSON. Starts that episode, where it
    was left if it was started before."""
    try:
        info = json.loads(base64.b64decode(json.loads(data)[0]))
        type_, id_ = info["type"], info["id"]
    except (ValueError, TypeError, KeyError, IndexError):
        log(f"Up Next sent something we can't play: {data!r}", xbmc.LOGWARNING)
        return
    row = get_watchstate().get(id_)
    resume = "1" if row and row.position > 0 and not row.watched else "0"
    url = plugin_url("play", type=type_, id=id_, meta=info.get("meta") or None, binge=info.get("binge") or None,
                     resume=resume)
    log(f"Up Next: playing {id_}")
    xbmc.executebuiltin(f"PlayMedia({url},noresume)")  # resume says where (the service seeks)


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


UPNEXT_PLAY = f"Other.{ADDON_ID}_play_action"  # Up Next's notification when it's time to play


def signal_upnext(entry):
    """Tell Up Next what follows this episode. When the user continues (or the
    countdown ends) it sends us `play_info` back (UPNEXT_PLAY) and we start the
    episode, with the same source group. Not a play_url: Up Next queues that in
    Kodi's playlist and skips to it, but our playback isn't a playlist's, so Kodi
    found no next item ("Can't find a next item to play")."""
    from .details import load_show

    today = datetime.date.today().isoformat()

    def seasons(meta):  # this episode's and the next one's, in full
        following = next_episode(meta, {(entry.season, entry.episode)}, today)
        return {entry.season} | ({following.season} if following else set())

    meta = load_show(entry.type, entry.meta_id, quiet=True, seasons=seasons)
    if meta is None:
        return
    current = next((v for v in meta.videos if v.id == entry.video_id), None)
    following = next_episode(meta, {(entry.season, entry.episode)}, today)
    if current is None or following is None:
        return
    data = {
        "current_episode": _upnext_episode(meta, current),
        "next_episode": _upnext_episode(meta, following),
        "play_info": {"type": entry.type, "id": following.id, "meta": meta.id, "binge": entry.binge_group or ""},
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
PREFETCH_RESUMED = 20  # Continue Watching titles whose info pages are read ahead (the most recent)


def prewarm():
    """Refresh the lists you're likely to open (front-page and home catalogs,
    Continue Watching, Next Up, the watchlist) in the background, so they open instantly."""
    if not ADDON.getSettingBool("prewarm") or xbmc.Player().isPlaying():
        return
    from .details import prefetch_pages
    from .watching import next_up, resume_metas  # these import the UI modules; only needed here
    from .watchlist import watchlist_items, watchlist_metas

    client = get_client()
    catalogs = get_registry().home_catalogs()

    def task(addon, catalog):
        return lambda: fetch_catalog(client, addon, catalog, with_default_filters(catalog, {}), refresh=True)

    # Streaming Catalogs too: the info page checks them before it opens ("More on Netflix")
    from stremio.streaming import streaming_addon

    streaming = streaming_addon()
    catalogs = catalogs + [(streaming, c) for c in streaming.manifest.catalogs]
    _, errors, _ = gather([(f"{a.name} / {c.name}", task(a, c)) for a, c in catalogs])
    state = get_watchstate()
    rows = state.continue_watching()
    resume_metas(rows)
    upcoming = next_up(state)
    if get_mdblist() is not None:
        listed = watchlist_items(refresh=True)
        if listed:
            watchlist_metas(listed)  # their details (kept a few hours), so the list opens at once
    # Their info pages too (the lists keep only slim copies): the title, and a show's season to watch
    titles = {}
    for row in rows[:PREFETCH_RESUMED]:
        titles.setdefault((row.type, row.meta_id or row.video_id), row.video_id if row.meta_id else None)
    for meta, video in upcoming:
        titles.setdefault((meta.type, meta.id), video.id)
    pages = prefetch_pages(titles, lambda: xbmc.Monitor().abortRequested() or xbmc.Player().isPlaying())
    log(f"Pre-warmed {len(catalogs) - len(errors)} catalogs, Continue Watching, Next Up, the watchlist "
        f"and {pages} information pages")


STALE_SETTLE = 2  # seconds without new entries before the queue is taken: one reload for a whole home screen
_stale_submitted = threading.Event()


def refresh_stale():
    """Fetch again what widgets and hub rows showed from an expired cache
    (common.queue_stale), then reload them once if anything came back new."""
    _stale_submitted.clear()
    entries = take_stale()
    if not entries:
        return
    client = get_client()

    def task(entry):
        transport_url, resource, type_, id_, extra = entry
        return lambda: client.get_resource(transport_url, resource, type_, id_, extra, refresh=True)

    _, errors, _ = gather([(f"{e[1]} {e[2]}/{e[3]}", task(e)) for e in entries])
    log(f"Refreshed {len(entries) - len(errors)} of {len(entries)} lists shown from an expired cache")
    if len(errors) < len(entries) and not xbmc.Player().isPlaying():
        notify_widgets()


def stale_queued(now=None):
    """True once lists are queued for refresh and the widgets have stopped adding to it."""
    if _stale_submitted.is_set():
        return False
    try:
        changed = os.path.getmtime(stale_queue_path())
    except OSError:
        return False
    return (now or time.time()) - changed >= STALE_SETTLE


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
    from .keymap import apply_keymap
    from .splash import apply_splash

    apply_keymap()
    apply_splash()
    home_widgets = HomeWidgets()  # this profile's, before the home screen shows
    log("Service started")
    try:
        _loop(monitor, worker, tracker, follower, home_widgets)
    finally:  # also when Kodi kills the service: no thread may be left running
        worker.stop()
        log("Service stopped")


def _loop(monitor, worker, tracker, follower, home_widgets):
    from .backup import install_after_restore
    from .splash import apply_splash

    next_sync = time.time() + 60             # first syncs shortly after Kodi starts
    next_library = time.time() + 120
    next_prewarm = time.time() + 180
    next_skin_hubs = time.time() + 20
    while not monitor.waitForAbort(1):
        tracker.tick()
        home_widgets.tick()
        if not skin_active():  # with our skin, titles' menus are ours (contextmenu.py), not Kodi's
            follower.tick()
        if time.time() >= next_sync:
            next_sync = time.time() + max(1, ADDON.getSettingInt("mdblist_sync_hours")) * 3600
            worker.submit(sync_mdblist)
        if time.time() >= next_library:
            next_library = time.time() + LIBRARY_UPDATE_EVERY
            worker.submit(update_library)
        if stale_queued():  # widgets showed expired lists: refresh just those, now
            _stale_submitted.set()
            worker.submit(refresh_stale)
        if time.time() >= next_prewarm:
            next_prewarm = time.time() + PREWARM_EVERY
            worker.submit(prewarm)
        if next_skin_hubs and time.time() >= next_skin_hubs:
            next_skin_hubs = None  # once per start: the hubs follow the catalogs you have
            worker.submit(refresh_skin_hubs)
            worker.submit(apply_splash)  # again: on a first start the skin sets its default late
            worker.submit(install_after_restore)  # the first start after a restore (backup.py)
