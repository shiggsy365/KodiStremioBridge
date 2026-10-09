"""What playback is doing while it finds a title's streams and tries them.

With Arctic Zephyr Stremio: its status bar (stremiobridge-status.xml in the
skin), the title's poster and name, the step, a progress line and "Back to
cancel"; with another skin, Kodi's progress dialog. Both run ``work(status)``
and report through ``status.update(percent, text)`` and ``status.cancelled``.

The bar is a modal dialog: a plain overlay would sit under Kodi's busy dialog
(shown while a selected item is resolved), which takes the Back key. So the
work runs in a thread while the bar holds the screen, and Back stops it.
"""

import threading

import xbmc
import xbmcgui

from .common import ADDON, ADDON_NAME, log, skin_active

XML = "stremiobridge-status.xml"
PROGRESS = 100  # the bar's progress line (in the skin's XML)
ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_STOP = 10, 92, 13


class _Bar(xbmcgui.WindowXMLDialog):
    def onInit(self):
        self.shown = True
        for name, value in self.properties.items():
            self.setProperty(name, value)
        self._show_progress()
        if self.finished:  # the work ended before the bar came up
            self.close()

    def onAction(self, action):
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_STOP):
            self.cancelled = True
            self.close()

    def _show_progress(self):
        try:
            self.getControl(PROGRESS).setPercent(self.percent)
        except (RuntimeError, TypeError):
            pass


class SkinStatus:
    """The skin's status bar (see the module's description)."""

    def __init__(self, title="", subtitle="", poster=""):
        self._bar = _Bar(XML, ADDON.getAddonInfo("path"), "Default", "1080i")
        self._bar.properties = {"title": title, "subtitle": subtitle, "poster": poster, "status": ""}
        self._bar.cancelled = self._bar.finished = self._bar.shown = False
        self._bar.percent = 0

    @property
    def cancelled(self):
        return self._bar.cancelled

    def update(self, percent, text):
        bar = self._bar
        bar.percent = max(0, min(100, int(percent)))
        bar.properties["status"] = text
        if bar.shown:
            bar.setProperty("status", text)
            bar._show_progress()

    def run(self, work):
        """``work(self)``'s result (its exception re-raised); the bar shows meanwhile."""
        bar, outcome = self._bar, {}

        def target():
            try:
                outcome["value"] = work(self)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
                outcome["error"] = exc
            finally:
                bar.finished = True
                bar.close()

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        bar.doModal()  # until the work ends (it closes the bar) or Back
        if bar.cancelled:
            worker.join(5)  # the work sees `cancelled` within a moment
        else:
            worker.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def close(self):
        self._bar.close()
        del self._bar


class DialogStatus:
    """Kodi's progress dialog, with SkinStatus's interface."""

    def __init__(self, title="", subtitle="", poster=""):
        self._dialog = xbmcgui.DialogProgress()
        self._dialog.create(ADDON_NAME, "")

    @property
    def cancelled(self):
        return self._dialog.iscanceled()

    def update(self, percent, text):
        self._dialog.update(max(0, min(100, int(percent))), text)

    def run(self, work):
        return work(self)

    def close(self):
        self._dialog.close()


def playback_status(title="", subtitle="", poster=""):
    """A SkinStatus with Arctic Zephyr Stremio (if its bar is there), else a DialogStatus."""
    if skin_active():
        try:
            return SkinStatus(title, subtitle, poster)
        except RuntimeError as exc:  # an older skin without the bar
            log(f"No status bar in the skin ({exc}); using Kodi's progress dialog", xbmc.LOGDEBUG)
    return DialogStatus(title, subtitle, poster)
