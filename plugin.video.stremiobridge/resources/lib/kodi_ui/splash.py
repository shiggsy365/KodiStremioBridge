"""Kodi's own splash screen (shown before any skin loads) with Arctic Zephyr
Stremio: Kodi shows special://home/media/splash.jpg instead of its own logo
when that file exists. While the skin is in use and its "Kodi Stremio Bridge
splash when Kodi starts" setting is on, the service puts this add-on's splash
there; otherwise it removes it, but only if it's ours.
"""

import filecmp
import os
import shutil

import xbmc
import xbmcvfs

from .common import ADDON, log, skin_active
from .router import route

KODI_SPLASH = "special://home/media/splash.jpg"
SKIN_SETTING = "sb.kodi.splash"


def our_splash():
    return os.path.join(ADDON.getAddonInfo("path"), "resources", "media", "splash.jpg")


def wanted():
    return skin_active() and xbmc.getCondVisibility(f"Skin.HasSetting({SKIN_SETTING})")


def apply_splash(want=None):
    """Write or remove Kodi's splash to match; True if anything changed."""
    want = wanted() if want is None else want
    target, source = xbmcvfs.translatePath(KODI_SPLASH), our_splash()
    exists = os.path.exists(target)
    ours = exists and filecmp.cmp(target, source, shallow=False)
    if want and not ours:
        if exists:
            return False  # a splash of the user's own: leave it
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        log("Kodi's splash: Kodi Stremio Bridge")
        return True
    if not want and ours:
        os.remove(target)
        log("Kodi's splash: Kodi's own")
        return True
    return False


@route("kodi_splash")
def kodi_splash(plugin):
    """Run by the skin's setting when it's toggled."""
    apply_splash()
