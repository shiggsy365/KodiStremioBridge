"""Back up a whole Kodi installation (every profile) to one zip, and restore it
on another device. No Kodi imports, so this is unit-testable.

A backup is Kodi's home folder (special://home) without what Kodi and the
add-ons rebuild themselves: thumbnails and their texture databases, the TV
guide, downloaded add-on packages, temp, logs, Stremio Bridge's response
caches and Python bytecode. Binary add-ons (inputstream.adaptive, PVR clients,
...; their addon.xml names a platform library) only work on the platform they
were built for, so they're listed in the backup's manifest instead of copied:
the device restored onto reinstalls them from its repositories.

Restoring unpacks into a staging folder first (a backup that can't be read
leaves the device as it was), keeps the device's own binary add-ons, swaps the
folders in and resets Kodi's repository cache, which otherwise lists the
backed-up platform's add-ons.
"""

import json
import os
import re
import shutil
import sqlite3
import time
import zipfile

MANIFEST = "kodi-backup.json"
FORMAT = 1
TOP_DIRS = ("addons", "userdata", "media")  # under Kodi's home folder

_SKIP_PATHS = ("addons/packages", "addons/temp")
_SKIP_DIRS = {"Thumbnails", "__pycache__"}
_SKIP_FILES = re.compile(
    r"(^|/)(Textures\d+\.db|Epg\d+\.db)(-wal|-shm|-journal)?$"         # thumbnail index, TV guide
    r"|(^|/)addon_data/plugin\.video\.stremiobridge/cache\.db(-wal|-shm)?$"  # Stremio Bridge's responses
    r"|\.(pyc|pyo|log)$|-shm$"
    r"|(^|/)kodi-backup-[^/]*\.zip(\.part)?$"                       # earlier backups saved in here
)
_STORED = {".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".xbt", ".mp3", ".mp4", ".mkv", ".gz", ".7z"}
_BINARY = re.compile(r"\blibrary_[a-z_]+\s*=")


class BackupError(Exception):
    pass


def addon_info(addon_dir):
    """``(id, version, binary)`` from an add-on's addon.xml, or None."""
    try:
        with open(os.path.join(addon_dir, "addon.xml"), encoding="utf-8", errors="replace") as f:
            xml = f.read()
    except OSError:
        return None
    head = re.search(r"<addon\b[^>]*>", xml)
    if not head:
        return None
    id_ = re.search(r'\bid="([^"]+)"', head.group(0))
    version = re.search(r'\bversion="([^"]+)"', head.group(0))
    if not id_:
        return None
    return id_.group(1), version.group(1) if version else "", bool(_BINARY.search(xml))


def binary_addons(home):
    """``{folder name: (id, version)}`` of the binary add-ons installed in `home`."""
    found = {}
    root = os.path.join(home, "addons")
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        info = addon_info(os.path.join(root, name))
        if info and info[2]:
            found[name] = info[:2]
    return found


def skipped(relative):
    """Whether a file (its path under the home folder, "/"-separated) is left out."""
    if any(relative == p or relative.startswith(p + "/") for p in _SKIP_PATHS):
        return True
    if any(part in _SKIP_DIRS for part in relative.split("/")[:-1]):
        return True
    return bool(_SKIP_FILES.search(relative))


def plan(home):
    """``(files, binaries)``: the files a backup of `home` holds (relative paths)
    and the binary add-ons it lists instead, ``[(id, version)]``."""
    binaries = binary_addons(home)
    files = []
    for top in TOP_DIRS:
        root = os.path.join(home, top)
        for folder, dirs, names in os.walk(root):
            rel_folder = os.path.relpath(folder, home).replace(os.sep, "/")
            if rel_folder.startswith("addons/") and rel_folder.split("/")[1] in binaries:
                dirs[:] = []
                continue
            dirs.sort()
            for name in sorted(names):
                relative = f"{rel_folder}/{name}"
                if not skipped(relative):
                    files.append(relative)
    return files, sorted(binaries.values())


def write_backup(home, target, files, manifest, progress=None):
    """Write `files` (from plan) and `manifest` to the zip `target`.
    `progress(done, total, name)` returning False cancels (target removed).
    Returns the number of files written."""
    tmp = target + ".part"
    total = len(files)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(MANIFEST, json.dumps(dict(manifest, format=FORMAT, files=total), indent=1))
            for done, relative in enumerate(files, 1):
                if progress and done % 25 == 1 and progress(done, total, relative) is False:
                    raise BackupError("cancelled")
                path = os.path.join(home, *relative.split("/"))
                stored = os.path.splitext(relative)[1].lower() in _STORED
                try:
                    z.write(path, relative, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
                except FileNotFoundError:
                    continue  # gone since the plan (a cache rotated, say)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return total


def read_manifest(path):
    try:
        with zipfile.ZipFile(path) as z:
            data = json.loads(z.read(MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError(f"Not a backup of this kind: {exc}") from exc
    if not isinstance(data, dict) or data.get("format", 0) > FORMAT:
        raise BackupError("Made by a newer version: update Stremio Bridge first")
    return data


def _member_path(staging, name):
    """Where zip member `name` unpacks to, refusing anything outside `staging`."""
    target = os.path.normpath(os.path.join(staging, *name.split("/")))
    if os.path.commonpath([os.path.abspath(staging), os.path.abspath(target)]) != os.path.abspath(staging):
        raise BackupError(f"Unsafe path in backup: {name}")
    return target


def restore(home, path, progress=None, staging_name="sb-restore"):
    """Replace `home`'s add-ons, profiles and settings with the backup at `path`.
    Returns ``(manifest, missing)``: the binary add-ons the backup listed that
    this device doesn't have (to install from its repositories)."""
    manifest = read_manifest(path)
    work = os.path.join(home, "temp", staging_name)
    shutil.rmtree(work, ignore_errors=True)
    staging = os.path.join(work, "new")
    os.makedirs(staging)
    try:
        with zipfile.ZipFile(path) as z:
            members = [m for m in z.infolist() if m.filename != MANIFEST and not m.is_dir()]
            total = len(members)
            for done, member in enumerate(members, 1):
                if progress and done % 25 == 1 and progress(done, total, member.filename) is False:
                    raise BackupError("cancelled")
                if member.filename.split("/")[0] not in TOP_DIRS:
                    continue
                target = _member_path(staging, member.filename)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)  # the device stays as it was
        raise

    # This device's binary add-ons stay (the backup has none of its own)
    own = binary_addons(home)
    new_addons = os.path.join(staging, "addons")
    os.makedirs(new_addons, exist_ok=True)
    for name in own:
        if not os.path.exists(os.path.join(new_addons, name)):
            shutil.move(os.path.join(home, "addons", name), os.path.join(new_addons, name))

    # Swap: the old folders aside first, so nothing is lost if a move fails
    old = os.path.join(work, "old")
    os.makedirs(old)
    for top in TOP_DIRS:
        current = os.path.join(home, top)
        if os.path.exists(current):
            os.replace(current, os.path.join(old, top))
    for top in TOP_DIRS:
        restored = os.path.join(staging, top)
        if os.path.exists(restored):
            os.replace(restored, os.path.join(home, top))
    # Kodi's packages and add-on temp folders were left out: back in their place
    for keep in ("packages", "temp"):
        previous = os.path.join(old, "addons", keep)
        if os.path.isdir(previous) and not os.path.exists(os.path.join(home, "addons", keep)):
            os.replace(previous, os.path.join(home, "addons", keep))
    shutil.rmtree(work, ignore_errors=True)

    reset_repositories(os.path.join(home, "userdata", "Database"))
    have = {info[0] for info in own.values()}
    missing = [addon_id for addon_id, _ in manifest.get("binary_addons", []) if addon_id not in have]
    return manifest, missing


def reset_repositories(database_dir):
    """Clear Kodi's record of when it last read each repository, so it reads
    them again (for this device's platform) when it starts."""
    try:
        names = [n for n in os.listdir(database_dir) if re.fullmatch(r"Addons\d+\.db", n)]
    except OSError:
        return False
    if not names:
        return False
    latest = max(names, key=lambda n: int(re.sub(r"\D", "", n)))
    try:
        with sqlite3.connect(os.path.join(database_dir, latest)) as db:
            db.execute("UPDATE repo SET checksum = '', lastcheck = '', nextcheck = ''")
    except sqlite3.Error:
        return False
    return True


def backup_name(device, when=None):
    """``kodi-backup-<device>-<YYYY-MM-DD-HHMM>.zip``"""
    stamp = time.strftime("%Y-%m-%d-%H%M", time.localtime(when))
    device = re.sub(r"[^A-Za-z0-9]+", "-", device or "kodi").strip("-") or "kodi"
    return f"kodi-backup-{device}-{stamp}.zip"
