"""BingeTV: trailers on Home. When a title in one of Home's rows stays focused for a
while (Settings > Home page layout), its trailer plays in a window at the top right
(the skin's video window, shown while Window(Home).Property(btv.trailer) is set),
without going fullscreen. It stops as soon as the focus moves, Home is left or
covered, or the trailer ends; and only ever the trailer: playback started in the
meantime (a title's Play) is left alone.

The trailer is the list item's own (ListItem.Trailer), else its title's metadata's.
"""

import time

import xbmc
import xbmcgui

from .common import ADDON, BINGETV_SKIN_ID, log
from .contextmenu import item_target

PROPERTY = "btv.trailer"
ROWS = {base * 10 + n for base in range(910, 915) for n in range(1, 7)}  # Home's row lists
TICK = 0.25


def focused_title():
    """``(path, trailer)`` of the title focused in one of Home's rows, else None."""
    if xbmc.getSkinDir() != BINGETV_SKIN_ID or not xbmc.getCondVisibility(
            "Window.IsActive(home) + !System.HasActiveModalDialog + !Window.IsActive(fullscreenvideo)"):
        return None
    try:
        control = int(xbmc.getInfoLabel("System.CurrentControlId") or 0)
    except ValueError:
        return None
    if control not in ROWS:
        return None
    path = xbmc.getInfoLabel(f"Container({control}).ListItem.FileNameAndPath")
    if not path or item_target(path) is None:
        return None
    return path, xbmc.getInfoLabel(f"Container({control}).ListItem.Trailer")


def trailer_for(path, trailer=""):
    """A playable trailer URL for one of our title paths, or ""."""
    if trailer:
        return trailer
    from .details import load_meta
    from .listitems import YOUTUBE_PLUGIN

    _action, params = item_target(path)
    owner = params.get("meta") or params["id"].split(":")[0]
    meta = load_meta(params["type"], owner, quiet=True)
    return YOUTUBE_PLUGIN.format(meta.trailer) if meta and meta.trailer else ""


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
    def __init__(self, player=None, clock=time.monotonic):
        self.player = player or PlaybackEvents()
        self.clock = clock
        self.focused = None       # the focused title's path
        self.since = 0.0          # ... focused since
        self.tried = None         # the title whose trailer was tried (once per focus)
        self.playing = None       # our trailer: (asked at, starts and stops counted then)

    def enabled(self):
        return ADDON.getSettingBool("btv_home_trailers")

    def tick(self):
        now = self.clock()
        if xbmc.getCondVisibility("Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)"):
            # Kodi's busy dialog (e.g. while YouTube finds the trailer's stream) isn't leaving Home
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
        if path is None or self.tried == path or self.player.isPlaying():
            return
        if now - self.since < ADDON.getSettingInt("btv_trailer_delay"):
            return
        self.tried = path
        url = trailer_for(path, title[1])
        if url and self.focused == path:
            log(f"Home trailer for {path}")
            xbmcgui.Window(10000).setProperty(PROPERTY, "1")
            self.playing = (now, self.player.starts, self.player.stops)
            xbmc.executebuiltin(f'PlayMedia("{url}",0,1)')  # 1: in a window, not fullscreen

    def _state(self):
        """``(started, over)`` for our trailer, from the playback events since it was asked for."""
        _asked, starts, stops = self.playing
        started = self.player.starts > starts
        over = self.player.stops > stops or self.player.starts > starts + 1
        return started, over

    def _follow(self, now):
        started, over = self._state()
        if over or (not started and now - self.playing[0] > 20):  # ended or replaced; or never started
            self._clear()

    def stop(self):
        """Stop our trailer (only ours, also while it's still starting) and hide its window."""
        if not self.playing:
            return
        _started, over = self._state()
        if not over and self.player.isPlaying():
            self.player.stop()
        self._clear()

    def _clear(self):
        self.playing = None
        xbmcgui.Window(10000).clearProperty(PROPERTY)


def run():
    """The service's trailer thread (with a Monitor of its own): until Kodi stops the service."""
    monitor = xbmc.Monitor()
    trailer = HomeTrailer()
    xbmcgui.Window(10000).clearProperty(PROPERTY)
    while not monitor.waitForAbort(TICK):
        try:
            trailer.tick()
        except Exception as exc:  # noqa: BLE001 - a trailer mustn't take the service down
            log(f"Home trailer: {exc}", xbmc.LOGWARNING)
            trailer.stop()
    trailer.stop()
