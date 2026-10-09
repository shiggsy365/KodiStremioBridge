"""Back up this Kodi (every profile, its add-ons and settings) to one file, and
restore such a backup on another device (kodibackup.py does the work).

A restore ends by closing Kodi straight away: Kodi saves its settings when it
quits normally, which would write the old ones over the restored ones. When it
starts again, the service installs the binary add-ons the backup listed
(install_after_restore): they're built per platform, so they come from this
device's repositories.
"""

import html
import json
import os
import re
import time

import xbmc
import xbmcgui
import xbmcvfs

from kodibackup import BackupError, backup_name, plan, read_manifest, restore, write_backup

from .common import ADDON, ADDON_NAME, L, jsonrpc, log, notify
from .router import route

PENDING = "special://masterprofile/addon_data/plugin.video.stremiobridge/restore_pending.json"
INSTALL_TRIES = 2  # starts that try to install what a restore listed, before giving up
PLATFORMS = ("Android", "Linux", "Windows", "UWP", "OSX", "IOS", "TVOS", "FreeBSD", "WebOS")


def _home():
    return xbmcvfs.translatePath("special://home/")


def _platform():
    return next((p.lower() for p in PLATFORMS if xbmc.getCondVisibility(f"System.Platform.{p}")), "unknown")


def _profiles(home):
    try:
        with open(os.path.join(home, "userdata", "profiles.xml"), encoding="utf-8") as f:
            return [html.unescape(n) for n in re.findall(r"<name>([^<]*)</name>", f.read())]
    except OSError:
        return []


def _size(path):
    try:
        return f"{os.path.getsize(path) / 1e6:.0f} MB"
    except OSError:
        return ""


@route("backup")
def backup(plugin, folder=None):
    """`folder`: where to save, without asking (e.g. from a schedule)."""
    folder = folder or xbmcgui.Dialog().browse(3, L(30432), "files", "", False, True)
    if not folder:
        return
    home = _home()
    device = xbmc.getInfoLabel("System.FriendlyName") or "Kodi"
    name = backup_name(device)
    local = xbmcvfs.translatePath(folder)
    direct = os.path.isdir(local)  # a folder on this device; else a share: written here, then copied
    target = os.path.join(local if direct else xbmcvfs.translatePath("special://temp/"), name)
    dialog = xbmcgui.DialogProgress()
    dialog.create(ADDON_NAME, L(30433))

    def progress(done, total, _name):
        dialog.update(int(done * 100 / max(total, 1)), L(30434, done=done, total=total))
        return not dialog.iscanceled()

    try:
        files, binaries = plan(home)
        manifest = {
            "created": time.strftime("%Y-%m-%d %H:%M"), "device": device, "platform": _platform(),
            "kodi": xbmc.getInfoLabel("System.BuildVersion"), "profiles": _profiles(home),
            "binary_addons": binaries, "made_by": f"{ADDON_NAME} {ADDON.getAddonInfo('version')}",
        }
        write_backup(home, target, files, manifest, progress)
        size = _size(target)
        if not direct:
            dialog.update(100, L(30445))
            copied = xbmcvfs.copy(target, folder.rstrip("/") + "/" + name)
            os.remove(target)
            if not copied:
                raise BackupError(f"couldn't write to {folder}")
    except BackupError as exc:
        if str(exc) != "cancelled":
            log(f"Backup failed: {exc}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok(ADDON_NAME, L(30436, reason=exc))
        return
    except OSError as exc:
        log(f"Backup failed: {exc}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok(ADDON_NAME, L(30436, reason=exc))
        return
    finally:
        dialog.close()
    log(f"Backup of {len(files)} files saved to {folder}{name} ({size})")
    xbmcgui.Dialog().ok(ADDON_NAME, L(30435, name=name, size=size))


@route("restore")
def restore_backup(plugin):
    chosen = xbmcgui.Dialog().browse(1, L(30437), "files", ".zip")
    if not chosen:
        return
    home = _home()
    local, copy = xbmcvfs.translatePath(chosen), None
    if not os.path.isfile(local):  # on a share: copied here first
        copy = os.path.join(xbmcvfs.translatePath("special://temp/"), "sb-restore.zip")
        busy = xbmcgui.DialogProgress()
        busy.create(ADDON_NAME, L(30445))
        ok = xbmcvfs.copy(chosen, copy)
        busy.close()
        if not ok:
            xbmcgui.Dialog().ok(ADDON_NAME, L(30444, reason=f"couldn't read {chosen}"))
            return
        local = copy
    try:
        manifest = read_manifest(local)
        profiles = ", ".join(manifest.get("profiles") or []) or "-"
        if not xbmcgui.Dialog().yesno(ADDON_NAME, L(30438, device=manifest.get("device", "?"),
                                                    date=manifest.get("created", "?"), profiles=profiles)):
            return
        dialog = xbmcgui.DialogProgress()
        dialog.create(ADDON_NAME, L(30439))

        def progress(done, total, _name):
            dialog.update(int(done * 100 / max(total, 1)), L(30434, done=done, total=total))
            return not dialog.iscanceled()

        try:
            manifest, missing = restore(home, local, progress)
        finally:
            dialog.close()
    except (BackupError, OSError) as exc:
        if str(exc) != "cancelled":
            log(f"Restore failed: {exc}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok(ADDON_NAME, L(30444, reason=exc))
        return
    finally:
        if copy:
            try:
                os.remove(copy)
            except OSError:
                pass
    if missing:
        pending = xbmcvfs.translatePath(PENDING)
        os.makedirs(os.path.dirname(pending), exist_ok=True)
        with open(pending, "w", encoding="utf-8") as f:
            json.dump({"install": missing, "tries": 0}, f)
    log(f"Restored the backup of {manifest.get('device')} from {manifest.get('created')}; "
        f"to install: {missing or 'nothing'}. Closing Kodi.")
    xbmcgui.Dialog().ok(ADDON_NAME, L(30440, count=len(missing)) if missing else L(30441))
    # Straight away: quitting normally saves Kodi's settings over the restored ones
    os._exit(0)


def install_after_restore(monitor=None):
    """At the first start after a restore: install the binary add-ons the
    backup listed, from this device's repositories (read afresh first)."""
    path = xbmcvfs.translatePath(PENDING)
    try:
        with open(path, encoding="utf-8") as f:
            pending = json.load(f)
    except (OSError, ValueError):
        return
    wanted = [a for a in pending.get("install", []) if not xbmc.getCondVisibility(f"System.HasAddon({a})")]
    if wanted:
        notify(L(30442))
        xbmc.executebuiltin("UpdateAddonRepos", True)
        monitor = monitor or xbmc.Monitor()
        if monitor.waitForAbort(30):  # the repositories' lists arrive
            return
        for addon_id in wanted:
            xbmc.executebuiltin(f"InstallAddon({addon_id})", True)
    for addon_id in pending.get("install", []):
        if xbmc.getCondVisibility(f"System.HasAddon({addon_id})"):
            jsonrpc("Addons.SetAddonEnabled", addonid=addon_id, enabled=True)
    still = [a for a in pending.get("install", []) if not xbmc.getCondVisibility(f"System.HasAddon({a})")]
    tries = pending.get("tries", 0) + 1
    if still and tries < INSTALL_TRIES:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"install": still, "tries": tries}, f)
        return
    os.remove(path)
    if still:
        log(f"After the restore, not installed: {still}", xbmc.LOGWARNING)
        xbmcgui.Dialog().ok(ADDON_NAME, L(30443, names=", ".join(still)))
