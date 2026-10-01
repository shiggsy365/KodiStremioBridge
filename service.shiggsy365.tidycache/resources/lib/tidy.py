"""Tidy Cache: remove cached artwork Kodi hasn't used lately, and the add-on
zips Kodi keeps after installing. No Kodi imports, so it can be tested.

Artwork is removed through Kodi's own JSON-RPC (Textures.RemoveTexture), which
deletes both the image under Thumbnails/ and its row in Textures13.db while
Kodi runs; Kodi re-downloads anything it needs again.
"""

import os
import time
from datetime import datetime, timedelta

DAY = 86400
PACKAGE_MIN_AGE = 3600  # leave zips of an install that's still in progress alone


def is_due(last_run, now, interval_days):
    return now - (last_run or 0) >= interval_days * DAY


def textures_query(now, max_age_days):
    """Textures.GetTextures params: images last used more than `max_age_days`
    ago, or every cached image when it's 0."""
    params = {"properties": ["cachedurl"]}
    if max_age_days > 0:
        cutoff = datetime.fromtimestamp(now) - timedelta(days=max_age_days)
        params["filter"] = {"field": "lastused", "operator": "lessthan",
                            "value": cutoff.strftime("%Y-%m-%d %H:%M:%S")}
    return params


def cached_file(thumbnails_dir, cachedurl):
    return os.path.join(thumbnails_dir, *cachedurl.split("/")) if cachedurl else ""


def remove_textures(rpc, thumbnails_dir, now, max_age_days, keep_going=lambda: True):
    """Remove old cached artwork. `rpc(method, params)` returns the result.
    Stops early (returning ``complete=False``) when `keep_going()` says so,
    e.g. because playback started. Returns ``(removed, bytes_freed, complete)``."""
    textures = rpc("Textures.GetTextures", textures_query(now, max_age_days)).get("textures") or []
    removed = freed = 0
    for texture in textures:
        if not keep_going():
            return removed, freed, False
        path = cached_file(thumbnails_dir, texture.get("cachedurl"))
        try:
            size = os.path.getsize(path) if path else 0
        except OSError:
            size = 0
        rpc("Textures.RemoveTexture", {"textureid": texture["textureid"]})
        removed += 1
        freed += size
    return removed, freed, True


def remove_packages(packages_dir, now, min_age=PACKAGE_MIN_AGE):
    """Delete the add-on zips Kodi keeps in addons/packages. Returns
    ``(removed, bytes_freed)``."""
    removed = freed = 0
    try:
        names = os.listdir(packages_dir)
    except OSError:
        return 0, 0
    for name in names:
        path = os.path.join(packages_dir, name)
        try:
            info = os.stat(path)
            if not os.path.isfile(path) or now - info.st_mtime < min_age:
                continue
            os.remove(path)
        except OSError:
            continue
        removed += 1
        freed += info.st_size
    return removed, freed


def size_text(size):
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


def now():
    return time.time()
