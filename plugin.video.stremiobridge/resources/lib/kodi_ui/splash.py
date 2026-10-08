"""Kodi's own splash screen (shown before any skin loads) with Arctic Zephyr
Stremio: Kodi shows special://home/media/splash.jpg instead of its own logo
when that file exists. While the skin is in use and its "Kodi Stremio Bridge
splash when Kodi starts" setting is on, the service puts this add-on's splash
there; otherwise it removes it, but only if it's ours. A splash from an earlier
version (PREVIOUS) counts as ours, so a new one replaces it.
"""

import filecmp
import hashlib
import os
import shutil

import xbmc
import xbmcvfs

from .common import ADDON, log, skin_active
from .router import route

KODI_SPLASH = "special://home/media/splash.jpg"
SKIN_SETTING = "sb.kodi.splash"
# SHA-256 of the splashes earlier versions put there (add the old one's when replacing it)
PREVIOUS = {
    "4ba5c8cda13a5615ee82b7556b6f273b1f24de6a6d3d712a0e5b67bc3bcc81ae",  # 0.13.3
}


def our_splash():
    return os.path.join(ADDON.getAddonInfo("path"), "resources", "media", "splash.jpg")


def wanted():
    return skin_active() and xbmc.getCondVisibility(f"Skin.HasSetting({SKIN_SETTING})")


def apply_splash(want=None):
    """Write or remove Kodi's splash to match; True if anything changed."""
    want = wanted() if want is None else want
    target, source = xbmcvfs.translatePath(KODI_SPLASH), our_splash()
    exists = os.path.exists(target)
    current = exists and filecmp.cmp(target, source, shallow=False)
    ours = current or (exists and _sha256(target) in PREVIOUS)
    if want and not current:
        if exists and not ours:
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


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@route("kodi_splash")
def kodi_splash(plugin):
    """Run by the skin's setting when it's toggled."""
    apply_splash()
