"""A keymap of this add-on's: with "Back stops playback" on, Back in full-screen
video stops it (as the Stop button does: playback is saved and the list it came
from shows again) instead of leaving it playing behind the menus.

Written to special://profile/keymaps/ by the service, removed when the
setting is off; Kodi reloads its keymaps after either.
"""

import os

import xbmc
import xbmcvfs

from .common import ADDON, log

KEYMAP = "special://profile/keymaps/plugin.video.stremiobridge.xml"
# Back on a keyboard, a remote, Android's back key (Fire TV) and a gamepad
KEYMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by Stremio Bridge ("Back stops playback"); removed when that setting is off -->
<keymap>
  <FullscreenVideo>
    <keyboard>
      <backspace>Stop</backspace>
      <escape>Stop</escape>
      <browser_back>Stop</browser_back>
    </keyboard>
    <remote>
      <back>Stop</back>
    </remote>
    <gamepad>
      <b>Stop</b>
    </gamepad>
  </FullscreenVideo>
</keymap>
"""


def apply_keymap(enabled=None):
    """Write or remove the keymap to match the setting; True if anything changed."""
    if enabled is None:
        enabled = ADDON.getSettingBool("back_stops")
    path = xbmcvfs.translatePath(KEYMAP)
    try:
        with open(path, encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = None
    if enabled and current != KEYMAP_XML:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(KEYMAP_XML)
    elif not enabled and current is not None:
        os.remove(path)
    else:
        return False
    xbmc.executebuiltin("Action(reloadkeymaps)")
    log(f"Back stops playback: {'on' if enabled else 'off'}")
    return True
