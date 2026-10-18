"""Add-on settings files in Kodi 21's format, brought up to Kodi 22's.

Kodi 22 reads a settings file written by Kodi 21 (format version 2, e.g. copied
across by a backup restore) by converting it, on every load, until one of the
add-on's settings is saved. That costs time on every call under load. Saving one
setting with its own value has Kodi rewrite the file in its own format (4).
"""

import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcvfs

from .common import log

ADDON_DATA = "special://profile/addon_data/"
CURRENT = 3   # files below this version are re-saved


def kodi_major():
    try:
        return int(xbmc.getInfoLabel("System.BuildVersion").split(".")[0])
    except ValueError:
        return 0


def old_files(root=None):
    """``[(add-on id, first setting id), ...]`` for settings files in an older format."""
    root = root or xbmcvfs.translatePath(ADDON_DATA)
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for addon_id in names:
        path = os.path.join(root, addon_id, "settings.xml")
        try:
            tree = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        # Skins keep their own kind of file (no version): theirs isn't an add-on settings file
        if addon_id.startswith("skin.") or tree.get("version") is None:
            continue
        try:
            version = int(tree.get("version"))
        except ValueError:
            continue
        first = tree.find("setting")
        if version < CURRENT and first is not None and first.get("id"):
            found.append((addon_id, first.get("id")))
    return found


def resave_old_settings():
    """On Kodi 22 and later: re-save the installed add-ons' old-format settings files."""
    if kodi_major() < 22:
        return []
    done = []
    for addon_id, setting in old_files():
        if not xbmc.getCondVisibility(f"System.HasAddon({addon_id})"):
            continue
        try:
            addon = xbmcaddon.Addon(addon_id)
            addon.setSetting(setting, addon.getSetting(setting))
            done.append(addon_id)
        except Exception as exc:  # noqa: BLE001 - one add-on's file mustn't stop the rest
            log(f"Settings file of {addon_id} not re-saved: {exc}")
    if done:
        log(f"Settings files brought up to Kodi's format: {', '.join(done)}")
    return done
