"""The Kodi side of Tidy Cache: settings, JSON-RPC, the schedule and messages."""

import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

import tidy

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
STATE_FILE = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo("profile")), "state.json")
CHECK_EVERY = 3600       # look at the schedule hourly
START_DELAY = 120        # let Kodi finish starting up first


def L(string_id, **kwargs):
    text = ADDON.getLocalizedString(string_id)
    return text.format(**kwargs) if kwargs else text


def log(message):
    xbmc.log(f"[{ADDON_ID}] {message}", xbmc.LOGINFO)


def rpc(method, params=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        raise RuntimeError(f"{method}: {response['error']}")
    return response.get("result") or {}


def last_run():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return float(json.load(f).get("lastRun") or 0)
    except (OSError, ValueError):
        return 0.0


def save_last_run(when):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"lastRun": when}, f)


def busy():
    """Playing something: tidy later (removing artwork costs some CPU)."""
    return xbmc.Player().isPlaying()


def clean(monitor=None, manual=False):
    """One tidy-up. Returns True if it finished (not interrupted)."""
    monitor = monitor or xbmc.Monitor()
    started = tidy.now()
    max_age = ADDON.getSettingInt("max_age_days")
    thumbnails = xbmcvfs.translatePath("special://thumbnails/")

    def keep_going():
        return not monitor.abortRequested() and (manual or not busy())

    textures, texture_bytes, complete = tidy.remove_textures(rpc, thumbnails, started, max_age, keep_going)
    packages = package_bytes = 0
    if complete and ADDON.getSettingBool("clear_packages"):
        packages, package_bytes = tidy.remove_packages(
            xbmcvfs.translatePath("special://home/addons/packages/"), started)
    summary = L(32010, images=textures, packages=packages, size=tidy.size_text(texture_bytes + package_bytes))
    log(summary if complete else f"Interrupted after removing {textures} images; will finish later")
    if complete:
        save_last_run(started)
        if manual or ADDON.getSettingBool("notify"):
            xbmcgui.Dialog().notification(ADDON.getAddonInfo("name"), summary, ADDON.getAddonInfo("icon"), 6000)
    return complete


def run_service():
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(START_DELAY):
        return
    while not monitor.abortRequested():
        interval = max(1, ADDON.getSettingInt("interval_days"))
        if tidy.is_due(last_run(), tidy.now(), interval) and not busy():
            try:
                clean(monitor)
            except Exception as exc:  # never take the service down; try again next check
                log(f"Tidy-up failed: {exc}")
        if monitor.waitForAbort(CHECK_EVERY):
            return


def run_now():
    """Settings > Tidy now (or running the add-on): tidy straight away."""
    xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
    try:
        clean(manual=True)
    finally:
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")
