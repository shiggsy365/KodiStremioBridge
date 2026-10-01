"""Shared Kodi helpers: add-on info, strings, logging, storage and factories."""

import json
import os
import random
import time
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from stremio.cache import Cache
from stremio.client import StremioClient
from stremio.history import SearchHistory
from stremio.registry import AddonRegistry
from stremio.watchstate import WatchState

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")


def L(string_id, **kwargs):
    """Localized string; ``{placeholders}`` are filled from kwargs."""
    text = ADDON.getLocalizedString(string_id)
    return text.format(**kwargs) if kwargs else text


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {msg}", level)


def debug(msg):
    # LOGDEBUG only shows with Kodi's global debug logging, so promote it when
    # the add-on's own debug setting is on.
    level = xbmc.LOGINFO if ADDON.getSettingBool("debug_logging") else xbmc.LOGDEBUG
    log(msg, level)


def profile_dir():
    return xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


def get_registry():
    return AddonRegistry(os.path.join(profile_dir(), "addons.json"), log=log)


def get_history():
    return SearchHistory(os.path.join(profile_dir(), "search_history.json"))


def clock_text(seconds):
    """1:02:03 / 4:05"""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def get_watchstate():
    return WatchState(
        os.path.join(profile_dir(), "watch.db"),
        watched_ratio=(ADDON.getSettingInt("watched_percent") or 90) / 100,
        min_resume=ADDON.getSettingInt("min_resume_seconds"),
    )


def get_mdblist():
    """An MDBList client if it's enabled and has a key, else None."""
    key = ADDON.getSettingString("mdblist_api_key").strip()
    if not ADDON.getSettingBool("mdblist_enabled") or not key:
        return None
    from mdblist import MDBListClient

    return MDBListClient(key, cache=get_cache())


def plugin_url(action, **params):
    """A plugin:// URL for code that has no Plugin (e.g. the service)."""
    params = {k: v for k, v in params.items() if v is not None}
    return f"plugin://{ADDON_ID}/?{urlencode({'action': action, **params})}"


# Handed from the play route to the playback service: what is about to play.
NOW_PLAYING = f"{ADDON_ID}.now_playing"


def announce_playback(entry, offset=0.0, retry=None):
    """`retry` describes how to try the next stream if this one fails to play."""
    xbmcgui.Window(10000).setProperty(NOW_PLAYING, json.dumps(
        {"entry": entry.to_json(), "offset": offset, "retry": retry, "time": time.time()}))


def take_announcement(max_age=120):
    """The raw announcement made within `max_age` seconds, else None. Clears it."""
    window = xbmcgui.Window(10000)
    raw = window.getProperty(NOW_PLAYING)
    window.clearProperty(NOW_PLAYING)
    try:
        data = json.loads(raw)
        return data if time.time() - data["time"] <= max_age else None
    except (ValueError, KeyError, TypeError):
        return None


def take_announced_playback(max_age=120):
    """``(PlaybackEntry, offset)`` announced within `max_age` seconds, else None."""
    from stremio.watchstate import PlaybackEntry

    data = take_announcement(max_age)
    try:
        return PlaybackEntry.from_json(data["entry"]), float(data.get("offset") or 0)
    except (TypeError, KeyError, ValueError):
        return None


_library = None


def get_library():
    """The "My Library" export (one instance per plugin call/service)."""
    global _library
    if _library is None:
        from stremio.library import Library

        _library = Library(os.path.join(profile_dir(), "Library"),
                           lambda type_, id_, meta=None: plugin_url("play", type=type_, id=id_, meta=meta))
    return _library


def jsonrpc(method, **params):
    """Call Kodi's JSON-RPC API; returns ``result`` ({} on error, which is logged)."""
    request = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        request["params"] = params
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)) or "{}")
    if "error" in response:
        log(f"JSON-RPC {method} failed: {response['error']}", xbmc.LOGWARNING)
        return {}
    return response.get("result") or {}


def get_cache():
    return Cache(os.path.join(profile_dir(), "cache.db"), log=log)


# Stream lists are kept briefly, so a retry after a failed stream (and going
# back to pick another) doesn't search every addon again. Debrid links stay
# valid far longer than this.
STREAM_CACHE_SECONDS = 300


def get_client():
    cache = get_cache()
    if random.random() < 0.05:  # occasional housekeeping; no need to do it on every call
        cache.purge()
    ttls = {
        "catalog": ADDON.getSettingInt("cache_catalog_minutes") * 60,
        "meta": ADDON.getSettingInt("cache_meta_hours") * 3600,
        "stream": STREAM_CACHE_SECONDS,
    }
    return StremioClient(
        timeout=ADDON.getSettingInt("request_timeout") or 15, cache=cache, ttls=ttls, log=log
    )


def notify(message, icon=xbmcgui.NOTIFICATION_INFO, time=3000):
    xbmcgui.Dialog().notification(ADDON_NAME, message, icon, time)


def run_with_progress(message, step_string_id, work):
    """Run ``work(on_progress)`` behind a cancellable progress dialog.

    `step_string_id` is a string with ``{done}`` and ``{total}`` placeholders.
    """
    dialog = xbmcgui.DialogProgress()
    dialog.create(ADDON_NAME, message)

    def on_progress(done, total, label):
        dialog.update(int(done * 100 / total), L(step_string_id, done=done, total=total))
        return not dialog.iscanceled()

    try:
        return work(on_progress)
    finally:
        dialog.close()


CONTEXT_MENU_COLOUR = "FFFF8080"  # light red: marks this add-on's entries among Kodi's own


def add_context_menu(item, entries):
    """``item.addContextMenuItems`` with our labels coloured."""
    item.addContextMenuItems(
        [(f"[COLOR {CONTEXT_MENU_COLOUR}]{label}[/COLOR]", command) for label, command in entries])


WIDGETS_RELOAD = f"{ADDON_ID}.widgets.reload"


def notify_widgets():
    """Bump the home-window reload token. Skin widgets whose path ends in
    ``&reload=$INFO[Window(Home).Property(<addon id>.widgets.reload)]`` see a new
    path and reload (Kodi has no direct "reload widgets" command for add-ons)."""
    xbmcgui.Window(10000).setProperty(WIDGETS_RELOAD, str(int(time.time() * 1000)))


def refresh_container():
    xbmc.executebuiltin("Container.Refresh")


IDLE_CONDITION = ("!Player.HasVideo + !Container.IsUpdating + !Window.IsActive(busydialog)"
                  " + !Window.IsActive(busydialognocancel)")


def refresh_when_idle(timeout=10.0, settle=1.0):
    """Refresh our own listing once Kodi has finished what it's doing.

    Refreshing while Kodi is still (re)loading a folder, e.g. straight after
    playback stops, makes Kodi abandon a load that's mid-copy into its directory
    cache and can crash it (seen: SIGSEGV in CDirectoryCache::SetDirectory). So
    wait until nothing is playing or loading, and only refresh if one of our
    folders is still on screen. Gives up after `timeout` seconds.
    """
    monitor = xbmc.Monitor()
    waited, idle_for = 0.0, 0.0
    while waited < timeout:
        if monitor.waitForAbort(0.25):
            return False
        waited += 0.25
        idle_for = idle_for + 0.25 if xbmc.getCondVisibility(IDLE_CONDITION) else 0.0
        if idle_for >= settle:
            if xbmc.getInfoLabel("Container.FolderPath").startswith(f"plugin://{ADDON_ID}/"):
                refresh_container()
                return True
            return False
    log("Skipped refreshing the listing: Kodi stayed busy")
    return False


class busy:
    """Context manager showing Kodi's busy spinner."""

    def __enter__(self):
        xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
        return self

    def __exit__(self, *exc):
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")
        return False
