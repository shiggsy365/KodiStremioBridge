"""BingeTV: trailers on Home. When a title in one of Home's rows stays focused for a
while (Settings > Home page layout), its trailer plays in a window at the top right
(the skin's video window, shown while Window(Home).Property(btv.trailer) is set),
without going fullscreen. It stops as soon as the focus moves, Home is left or
covered, or the trailer ends; and only ever the trailer: playback started in the
meantime (a title's Play) is left alone.

The trailer is the list item's own (ListItem.Trailer), else its title's metadata's. YouTube's
are found in the background, starting a little before the delay is up (youtube.py: no YouTube
add-on), and play from the service's trailer server.
"""

import os
import threading
import time

import xbmc
import xbmcgui
import xbmcvfs

from .common import ADDON, BINGETV_SKIN_ID, jsonrpc, log
from .contextmenu import item_target

PROPERTY = "btv.trailer"
QUIET_VOLUME = "btv.quietvolume"  # a trailer's mute or unmute: no volume icon for it
STEADY = "btv.steady"   # a title focused this long (its path): the skin shows its description
STEADY_AFTER = 0.25    # + the next tick and the skin's fade: shown at about half a second
HUB_WINDOW, HUB_LIST = 1150, 400
ROWS = {base * 10 + n for base in range(910, 915) for n in range(1, 7)}  # Home's row lists
TICK = 0.25


def focused_title():
    """``(path, trailer)`` of the title focused in one of Home's rows or a hub page's list, else None."""
    if xbmc.getSkinDir() != BINGETV_SKIN_ID or xbmc.getCondVisibility(
            "System.HasActiveModalDialog | Window.IsActive(fullscreenvideo)"):
        return None
    if xbmc.getCondVisibility(f"Window.IsActive({HUB_WINDOW})"):
        control = HUB_LIST if xbmc.getCondVisibility(f"Control.HasFocus({HUB_LIST})") else 0
    elif xbmc.getCondVisibility("Window.IsActive(home)"):
        try:
            control = int(xbmc.getInfoLabel("System.CurrentControlId") or 0)
        except ValueError:
            return None
        control = control if control in ROWS else 0
    else:
        return None
    if not control:
        return None
    path = xbmc.getInfoLabel(f"Container({control}).ListItem.FileNameAndPath")
    if not path or item_target(path) is None:
        return None
    return path, xbmc.getInfoLabel(f"Container({control}).ListItem.Trailer")


def focused_path():
    """The path of the title focused on Home's rows or a hub page's list, else ""."""
    if xbmc.getCondVisibility(f"Window.IsActive({HUB_WINDOW}) + Control.HasFocus({HUB_LIST})"):
        return xbmc.getInfoLabel(f"Container({HUB_LIST}).ListItem.FileNameAndPath")
    if not xbmc.getCondVisibility("Window.IsActive(home)"):
        return ""
    try:
        control = int(xbmc.getInfoLabel("System.CurrentControlId") or 0)
    except ValueError:
        return ""
    return xbmc.getInfoLabel(f"Container({control}).ListItem.FileNameAndPath") if control in ROWS else ""


def busy():
    return xbmc.getCondVisibility("Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)")


class SteadyFocus:
    """Marks the title that has kept the focus for STEADY_AFTER seconds (Window(Home).Property(btv.steady)):
    the skin fades its description in then, rather than flashing every title's on the way past
    (a scroll's titles stay focused for less)."""

    def __init__(self, clock=time.monotonic, focused=focused_path):
        self.clock = clock
        self.focused = focused
        self.path, self.since, self.marked = "", 0.0, ""

    def tick(self):
        if busy():  # Kodi's busy dialog (a trailer loading) has the focus for a moment: no change
            return
        now, path = self.clock(), self.focused()
        if path != self.path:
            self.path, self.since = path, now
        steady = path if path and now - self.since >= STEADY_AFTER else ""
        if steady != self.marked:
            self.marked = steady
            xbmcgui.Window(10000).setProperty(STEADY, steady)


def trailer_for(path, trailer=""):
    """A title's trailer: a URL (its list item's, which may name a YouTube video), a YouTube video
    id, or ""."""
    if trailer:
        return trailer
    from .details import load_meta

    _action, params = item_target(path)
    owner = params.get("meta") or params["id"].split(":")[0]
    meta = load_meta(params["type"], owner, quiet=True)
    return meta.trailer if meta and meta.trailer else ""


AHEAD = 3            # seconds before the delay is up that a YouTube trailer is looked for
WINDOW_HEIGHT = 720  # the window's half the screen: no more is needed, and it starts sooner
RESOLVE_WAIT = 15    # seconds to give YouTube


class Lookup:
    """A YouTube trailer's manifest found in a thread of its own (network only)."""

    def __init__(self, vid, publish=None):
        if publish is None:
            from .youtube import publish_trailer as publish
        self.url = None  # "" when not found
        self.thread = threading.Thread(target=self._run, args=(publish, vid), name="stremiobridge-yt", daemon=True)
        self.thread.start()

    def _run(self, publish, vid):
        try:
            self.url = publish(vid, WINDOW_HEIGHT)
        except Exception as exc:  # noqa: BLE001
            log(f"Home trailer: {exc}", xbmc.LOGWARNING)
            self.url = ""


class PlaybackEvents(xbmc.Player):
    """Counts video starts and stops: the trailer is the first start after it was asked for,
    and over at the next stop (or another start: something else plays instead)."""

    def __init__(self):
        super().__init__()
        self.starts = 0
        self.stops = 0

    def onAVStarted(self):
        self.starts += 1

    def onPlayBackStopped(self):
        self.stops += 1

    def onPlayBackEnded(self):
        self.stops += 1

    def onPlayBackError(self):
        self.stops += 1


class HomeTrailer:
    def __init__(self, player=None, clock=time.monotonic, lookup=Lookup):
        self.player = player or PlaybackEvents()
        self.clock = clock
        self.lookup = lookup
        self.focused = None       # the focused title's path
        self.since = 0.0          # ... focused since
        self.tried = None         # the title whose trailer was tried (once per focus)
        self.playing = None       # our trailer: (asked at, starts and stops counted then)
        self.resolving = None     # a YouTube trailer being found: (title, Lookup, since)
        self.ready = None         # ... or another trailer, waiting for the delay: (title, url)
        self.muted = False        # we muted Kodi for it (and unmute it after)

    def enabled(self):
        return ADDON.getSettingBool("btv_home_trailers")

    def tick(self):
        now = self.clock()
        if busy():
            # Kodi's busy dialog isn't leaving Home
            if self.playing:
                self._follow(now)
            return
        title = focused_title() if self.enabled() else None
        path = title[0] if title else None
        if path != self.focused:
            self.focused, self.since, self.tried = path, now, None
            self.stop()
        if self.playing:
            self._follow(now)
            return
        if path is None or self.player.isPlaying():
            return
        delay = ADDON.getSettingInt("btv_trailer_delay")
        if self.tried != path and now - self.since >= max(0, delay - AHEAD):
            self.tried = path
            self._find(path, trailer_for(path, title[1]), now)
        if now - self.since < delay:
            return
        if self.resolving:
            self._resolve(now)
        elif self.ready and self.ready[0] == path:
            url, self.ready = self.ready[1], None
            self._start(path, url, now)

    def _find(self, path, trailer, now):
        from .youtube import video_id

        vid = video_id(trailer)
        if vid:
            self.resolving = (path, self.lookup(vid), now)
        elif trailer and "://" in trailer:
            self.ready = (path, trailer)

    def _resolve(self, now):
        path, lookup, since = self.resolving
        if lookup.url is None:
            if now - since > RESOLVE_WAIT:
                self.resolving = None
            return
        self.resolving = None
        if lookup.url and self.focused == path:
            self._start(path, lookup.url, now, dash=True)

    def _start(self, path, url, now, dash=False):
        if not (focused_title() and focused_title()[0] == path):
            return
        log(f"Home trailer for {path}")
        xbmcgui.Window(10000).setProperty(PROPERTY, "1")
        xbmcgui.Window(10000).clearProperty("stremiobridge.playing.badges")  # not the trailer's
        self.playing = (now, self.player.starts, self.player.stops)
        if ADDON.getSettingBool("btv_trailer_muted") and not self._is_muted():
            self._set_mute(True)
            self.muted = True
        if dash:
            from .youtube import playable

            item = playable(url)
        else:
            item = xbmcgui.ListItem(path=url, offscreen=True)
        item.setProperty("BingeTVTrailer", "true")  # no busy dialog (BingeTV's Kodi, patches/0011)
        self.player.play(url, item, True)  # True: in a window, not fullscreen

    def _state(self):
        """``(started, over)`` for our trailer, from the playback events since it was asked for."""
        _asked, starts, stops = self.playing
        started = self.player.starts > starts
        # over: stopped or ended, replaced by another start, or simply not playing any more
        # (should Kodi's stop event go astray)
        over = self.player.stops > stops or self.player.starts > starts + 1 \
            or (started and not self.player.isPlaying())
        return started, over

    def _follow(self, now):
        started, over = self._state()
        if over or (not started and now - self.playing[0] > 20):  # ended or replaced; or never started
            self._clear()

    def stop(self):
        """Stop our trailer (only ours, also while it's still starting) and hide its window."""
        self.resolving = self.ready = None
        if not self.playing:
            return
        _started, over = self._state()
        if not over and self.player.isPlaying():
            # not xbmc.Player().stop(): that waits for Kodi to close the trailer holding Python's
            # lock, and closing it Kodi asks the trailer server (Python, so it couldn't answer)
            # about it: Kodi's UI frozen for its timeouts, 20 s at a time
            xbmc.executebuiltin("PlayerControl(Stop)")
        self._clear()

    def _clear(self):
        self.playing = None
        xbmcgui.Window(10000).clearProperty(PROPERTY)
        if self.muted:  # the sound back (whatever plays next)
            self.muted = False
            self._set_mute(False)

    @staticmethod
    def _is_muted():
        return bool((jsonrpc("Application.GetProperties", properties=["muted"]) or {}).get("muted"))

    @staticmethod
    def _set_mute(on):
        # Kodi keeps its mute across restarts: should BingeTV close, or a profile load, before
        # the trailer's unmute, the next start undoes it (unmute_left_over)
        marker = xbmcvfs.translatePath(MUTED_MARKER)
        try:
            if on:
                with open(marker, "w", encoding="utf-8") as f:
                    f.write("1")
            elif os.path.exists(marker):
                os.remove(marker)
        except OSError:
            pass
        # the skin hides Kodi's volume icon while this is set (cleared a moment after the change)
        xbmcgui.Window(10000).setProperty(QUIET_VOLUME, "1")
        jsonrpc("Application.SetMute", mute=on)
        xbmc.executebuiltin(f"AlarmClock(btvquietvolume,ClearProperty({QUIET_VOLUME},home),00:03,silent)")


MUTED_MARKER = "special://masterprofile/addon_data/plugin.video.stremiobridge/trailer_muted"


def unmute_left_over():
    """A trailer's mute that was never undone (BingeTV closed mid-trailer): undone now."""
    if os.path.exists(xbmcvfs.translatePath(MUTED_MARKER)):
        log("Undoing a trailer's mute left over from last time")
        HomeTrailer._set_mute(False)


def run():
    """The service's trailer thread (with a Monitor of its own): until Kodi stops the service."""
    monitor = xbmc.Monitor()
    unmute_left_over()
    trailer, steady = HomeTrailer(), SteadyFocus()
    xbmcgui.Window(10000).clearProperty(PROPERTY)
    while not monitor.waitForAbort(TICK):
        if xbmc.getSkinDir() != BINGETV_SKIN_ID:
            continue
        try:
            steady.tick()
            trailer.tick()
        except Exception as exc:  # noqa: BLE001 - a trailer mustn't take the service down
            log(f"Home trailer: {exc}", xbmc.LOGWARNING)
            trailer.stop()
    trailer.stop()
