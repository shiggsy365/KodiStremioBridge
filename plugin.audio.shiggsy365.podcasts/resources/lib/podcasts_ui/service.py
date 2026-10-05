"""Background service: saves play positions of episodes this add-on started,
resumes where asked, and runs gPodder sync (on start, every few minutes,
and soon after a local change)."""

import threading
import time

import xbmc
import xbmcgui

from podcasts import PodcastError
from podcasts.store import PLAYED, RESUME
from podcasts.sync import sync

from .common import (
    ADDON, NOW_PLAYING, get_store, get_sync_client, log, refresh_if_showing, take_announcement,
)
from .menus import SYNC_REQUEST

SAVE_EVERY = 15        # seconds between position saves while playing
SEEK_DELAY = 1.0       # let playback settle before seeking to the resume point
SEEK_TOLERANCE = 15    # already there if within this many seconds
SYNC_DEBOUNCE = 5      # seconds after a local change before syncing it
TICK = 1.0


class Tracker(xbmc.Player):
    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        self.episode = None
        self._reset()

    def _reset(self):
        self.position = self.duration = 0.0
        self.pending_seek = 0.0
        self.started = self.last_save = 0.0

    def onAVStarted(self):
        self._start("onAVStarted")

    def onPlayBackStarted(self):
        self._start("onPlayBackStarted")

    def _start(self, how):
        if self.episode is not None and self.started and time.time() - self.started < 5:
            return  # both callbacks fire for one start
        announced = take_announcement()
        if announced is None:
            self.episode = None  # something else is playing
            return
        self.episode, self.pending_seek = announced
        self.started = self.last_save = time.time()
        self.position, self.duration = 0.0, self._total_time()
        log(f"Tracking {self.episode.title} ({how})")

    def onPlayBackStopped(self):
        self._finish(ended=False)

    def onPlayBackEnded(self):
        self._finish(ended=True)

    def onPlayBackError(self):
        self._finish(ended=False)

    def onPlayBackPaused(self):
        if self.episode:
            self._save()

    def tick(self):
        if not self.isPlayingAudio():
            return
        if not self.episode:
            # Missed the start callback: pick up our announcement if there is one.
            if xbmcgui.Window(10000).getProperty(NOW_PLAYING):
                self._start("poll")
            if not self.episode:
                return
        try:
            self.position = self.getTime()
            self.duration = self.getTotalTime() or self.duration
        except RuntimeError:
            return
        now = time.time()
        if self.pending_seek and now - self.started >= SEEK_DELAY:
            if abs(self.position - self.pending_seek) > SEEK_TOLERANCE:
                log(f"Resuming at {int(self.pending_seek)}s")
                self.seekTime(self.pending_seek)
                self.position = self.pending_seek
            self.pending_seek = 0.0
        if now - self.last_save >= SAVE_EVERY and not self.pending_seek:
            self._save()

    def _total_time(self):
        try:
            return self.getTotalTime()
        except RuntimeError:
            return 0.0

    def _save(self):
        self.last_save = time.time()
        return get_store().record(self.episode, self.position, self.duration)

    def _finish(self, ended):
        if not self.episode:
            return
        if ended and self.duration:
            self.position = self.duration
        status = self._save() if not self.pending_seek else None
        log(f"Stopped {self.episode.title} at {int(self.position)}/{int(self.duration)}s: {status or 'not saved'}")
        self.episode = None
        self._reset()
        if status in (PLAYED, RESUME):
            self.on_change()
            refresh_if_showing()


class Syncer:
    def __init__(self):
        self.next_run = time.time() + 10  # first sync shortly after Kodi starts
        self.requested = ""
        self.thread = None

    def tick(self, now):
        request = xbmcgui.Window(10000).getProperty(SYNC_REQUEST)
        if request and request != self.requested:
            self.requested = request
            self.next_run = min(self.next_run, now + SYNC_DEBOUNCE)
        if now < self.next_run:
            return
        if self.thread is not None and self.thread.is_alive():
            self.next_run = now + SYNC_DEBOUNCE  # one at a time; try again once it's done
            return
        self.next_run = now + max(ADDON.getSettingInt("sync_minutes"), 5) * 60
        client = get_sync_client()
        if client is not None:
            self.thread = threading.Thread(target=self._run, args=(client,), name="podcasts-sync", daemon=True)
            self.thread.start()

    @staticmethod
    def _run(client):
        try:
            summary = sync(get_store(), client, log=log)
        except PodcastError as exc:
            log(f"Sync failed: {exc}", xbmc.LOGWARNING)
            return
        except Exception as exc:  # noqa: BLE001 - keep the service alive
            log(f"Sync crashed: {exc!r}", xbmc.LOGERROR)
            return
        if summary["subscriptions_received"] or summary["positions_received"]:
            refresh_if_showing()

    def soon(self):
        self.next_run = min(self.next_run, time.time() + SYNC_DEBOUNCE)


def run():
    monitor = xbmc.Monitor()
    syncer = Syncer()
    tracker = Tracker(on_change=syncer.soon)
    log("Service started")
    while not monitor.abortRequested():
        tracker.tick()
        syncer.tick(time.time())
        if monitor.waitForAbort(TICK):
            break
    log("Service stopped")
