"""BingeTV (the Kodi build with Stremio Bridge built in): telling you when a newer
version is out.

BingeTV's build writes special://xbmc/media/bingetv.json: its version and where
its releases are (GitHub's API for the latest release). About once a week the
service asks; if there's a newer one, it says so once per version, with a way to
download the APK for this device (32- or 64-bit ARM). Elsewhere (plain Kodi)
there's no such file, and nothing happens.
"""

import json
import os
import platform
import time
import urllib.request

import xbmc
import xbmcgui
import xbmcvfs

from .common import L, log, profile_dir
from .router import route

INFO = "special://xbmc/media/bingetv.json"
STATE = "bingetv_update.json"
CHECK_EVERY = 7 * 24 * 3600
TIMEOUT = 20


def build_info():
    """``{"version", "releases"}`` from BingeTV's build, or None (not BingeTV)."""
    try:
        with open(xbmcvfs.translatePath(INFO), encoding="utf-8") as f:
            info = json.load(f)
        return info if info.get("version") and info.get("releases") else None
    except (OSError, ValueError, AttributeError):
        return None


def version_key(text):
    """"v0.10.2" -> (0, 10, 2), for comparing; non-numbers count as 0."""
    parts = []
    for part in str(text).lstrip("vV").split("-")[0].split("."):
        parts.append(int(part) if part.isdigit() else 0)
    return tuple(parts)


def device_abi():
    return "arm64-v8a" if platform.machine().lower() in ("aarch64", "arm64") else "armeabi-v7a"


def download_url(release, abi=None):
    """The release's APK for `abi` (this device's), else its page."""
    abi = abi or device_abi()
    for asset in release.get("assets") or []:
        name = asset.get("name", "")
        if name.endswith(".apk") and abi in name:
            return asset.get("browser_download_url")
    return release.get("html_url")


def _state_path():
    return os.path.join(profile_dir(), STATE)


def _load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(profile_dir(), exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f)


def latest_release(url):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "BingeTV update check"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def open_link(url):
    """The download in Android's browser (or whatever handles it); the link shown otherwise."""
    if xbmc.getCondVisibility("System.Platform.Android"):
        xbmc.executebuiltin(f'StartAndroidActivity("","android.intent.action.VIEW","","{url}")')
    else:
        xbmcgui.Dialog().ok("BingeTV", url)


def check_for_update(force=False, now=None):
    """Ask GitHub about once a week (always if `force`); offer a newer BingeTV
    once per version (every time if `force`). True if one was offered."""
    info = build_info()
    if info is None:
        return False
    now = now or time.time()
    state = _load_state()
    if not force and now - state.get("checked", 0) < CHECK_EVERY:
        return False
    try:
        release = latest_release(info["releases"])
    except Exception as exc:  # noqa: BLE001 - no network, no releases yet, rate limited...
        log(f"BingeTV update check failed: {exc}")
        if force:
            xbmcgui.Dialog().ok("BingeTV", L(30510, version=info["version"]))
        return False
    state["checked"] = now
    latest = str(release.get("tag_name", "")).lstrip("vV")
    offered = False
    if latest and version_key(latest) > version_key(info["version"]) and (force or state.get("offered") != latest):
        state["offered"] = latest
        _save_state(state)
        offered = True
        if xbmcgui.Dialog().yesno("BingeTV", L(30508, latest=latest, current=info["version"])):
            open_link(download_url(release))
    else:
        _save_state(state)
        if force:
            xbmcgui.Dialog().ok("BingeTV", L(30510, version=info["version"]))
    return offered


@route("bingetv_update")
def bingetv_update(plugin):
    """Settings > Expert > Check for BingeTV updates."""
    if build_info() is None:
        xbmcgui.Dialog().ok("BingeTV", L(30509))
        return
    check_for_update(force=True)


@route("bingetv_restore_offer")
def first_run_restore(plugin=None):
    """BingeTV's first start: bring a Kodi setup across from a backup (Backup in
    Stremio Bridge's settings, on the old device). True if the user went for it."""
    if build_info() is None:
        return False
    if not xbmcgui.Dialog().yesno("BingeTV", L(30511), nolabel=L(30512), yeslabel=L(30513)):
        return False
    from .backup import restore_backup

    restore_backup(plugin)  # closes Kodi once restored; back here if not
    return True
