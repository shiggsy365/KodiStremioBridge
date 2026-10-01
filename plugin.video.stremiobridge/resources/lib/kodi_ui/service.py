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
    ADDON, ADDON_ID, L, get_client, get_mdblist, get_registry, get_watchstate, log, notify, notify_widgets,
    plugin_url, refresh_when_idle,
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
        if self.entry:
            self._finish(ended=False)
            return
        # Failed before the video started: if the play route said how, try the
        # next stream (autoplay only; a Stop before starting is the user cancelling).
        announced = take_announcement()
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
    if retry["next"] >= retry["total"] or retry["tries"] >= MAX_STREAM_RETRIES:
        log(f"Not retrying {retry['id']}: tried {retry['tries'] + 1} streams after playback errors")
        return
    notify(L(30178))
    xbmc.executebuiltin("PlayMedia({})".format(plugin_url(
        "play", type=retry["type"], id=retry["id"], meta=retry.get("meta"), binge=retry.get("binge"),
        resume="1" if retry.get("resume") else None, start=retry["next"], tries=retry["tries"] + 1)))


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
    from .library import sync_library_watched

    sync_library_watched()


def update_library():
    from .library import update_library as update

    update()


class LibraryMonitor(xbmc.Monitor):
    """Reacts to Kodi's library: its own watched changes on our exported items,
    and finished scans (newly added items get our watched state)."""

    def __init__(self, worker):
        super().__init__()
        self.worker = worker

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


def run():
    worker = Worker()
    monitor = LibraryMonitor(worker)
    tracker = Tracker(worker)
    next_sync = time.time() + 60             # first syncs shortly after Kodi starts
    next_library = time.time() + 120
    next_prewarm = time.time() + 180
    log("Service started")
    while not monitor.waitForAbort(1):
        tracker.tick()
        if time.time() >= next_sync:
            next_sync = time.time() + max(1, ADDON.getSettingInt("mdblist_sync_hours")) * 3600
            worker.submit(sync_mdblist)
        if time.time() >= next_library:
            next_library = time.time() + LIBRARY_UPDATE_EVERY
            worker.submit(update_library)
        if time.time() >= next_prewarm:
            next_prewarm = time.time() + PREWARM_EVERY
            worker.submit(prewarm)
    worker.stop()
    log("Service stopped")
