"""Long-press Play/Pause while watching opens the source picker: a keymap in
special://profile/keymaps, written (or removed) by the service to follow the
setting. A normal press of Play/Pause still pauses."""

import os

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
FILE = "special://profile/keymaps/shiggsy365.dispatcharrbridge.xml"
ACTION = "RunScript(script.shiggsy365.dispatcharrbridge)"
WINDOWS = ("FullscreenLiveTV", "FullscreenVideo")


def keymap_xml():
    sections = "".join(
        f"  <{window}>\n    <keyboard>\n"
        f"      <play_pause mod=\"longpress\">{ACTION}</play_pause>\n"
        f"    </keyboard>\n  </{window}>\n" for window in WINDOWS)
    return f"<keymap>\n{sections}</keymap>\n"


def sync():
    """Install or remove the keymap to match the setting; reload if it changed."""
    path = xbmcvfs.translatePath(FILE)
    wanted = keymap_xml() if ADDON.getSettingBool("long_press") else None
    try:
        with open(path, encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = None
    if current == wanted:
        return
    if wanted is None:
        os.remove(path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(wanted)
    xbmc.executebuiltin("Action(reloadkeymaps)")
    xbmc.log(f"[{ADDON.getAddonInfo('id')}] Long-press keymap {'installed' if wanted else 'removed'}", xbmc.LOGINFO)


class Monitor(xbmc.Monitor):
    def onSettingsChanged(self):
        global ADDON
        ADDON = xbmcaddon.Addon()
        sync()


def run_service():
    monitor = Monitor()
    sync()
    monitor.waitForAbort()
