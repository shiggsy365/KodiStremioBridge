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


# Our own skin: Kodi's info page replaces Extended info, and it has no use for
# the library export (its menus and widgets come from the add-on directly).
SKIN_ID = "skin.arctic.zephyr.stremio"


def skin_active():
    return xbmc.getSkinDir() == SKIN_ID


def select_opens_info():
    """Playback > When a title is selected: 0 shows its information page, 1 plays it
    (and a show or season opens its folder)."""
    return ADDON.getSettingInt("select_action") == 0


def library_enabled():
    return not skin_active()


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


# One connection per process (per settings): listings ask for these per item,
# and opening sqlite each time is slow on a Fire TV Stick.
_shared = {}


def get_watchstate():
    settings = ((ADDON.getSettingInt("watched_percent") or 90) / 100, ADDON.getSettingInt("min_resume_seconds"))
    path = os.path.join(profile_dir(), "watch.db")
    key = ("watchstate", path, settings)
    if key not in _shared:
        _shared[key] = WatchState(path, watched_ratio=settings[0], min_resume=settings[1])
    return _shared[key]


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
    path = os.path.join(profile_dir(), "cache.db")
    if ("cache", path) not in _shared:
        _shared[("cache", path)] = Cache(path, log=log)
    return _shared[("cache", path)]


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


STALE_QUEUE = "stale.jsonl"  # in the profile: requests answered from an expired cache, to refresh


def stale_queue_path():
    return os.path.join(profile_dir(), STALE_QUEUE)


def queue_stale(client):
    """Note the requests `client` answered from an expired cache, for the
    service to refresh (service.refresh_stale). One line per request, appended:
    widget calls run at the same time in separate processes, and small appends
    don't interleave."""
    if not client.served_stale:
        return
    lines = "".join(json.dumps(entry) + "\n" for entry in client.served_stale)
    try:
        with open(stale_queue_path(), "a", encoding="utf-8") as f:
            f.write(lines)
    except OSError as exc:
        log(f"Couldn't queue a refresh: {exc}")
    client.served_stale = []


def take_stale():
    """The queued requests (de-duplicated, oldest first), emptying the queue."""
    path = stale_queue_path()
    if not os.path.exists(path):
        return []
    taken = path + ".taking"
    try:
        os.replace(path, taken)  # new lines go to a fresh file meanwhile
        with open(taken, encoding="utf-8") as f:
            lines = f.read().splitlines()
        os.remove(taken)
    except OSError as exc:
        log(f"Couldn't read the refresh queue: {exc}")
        return []
    entries = {}
    for line in lines:
        try:
            transport_url, resource, type_, id_, extra = json.loads(line)
        except (ValueError, TypeError):
            continue
        entries.setdefault(line, (transport_url, resource, type_, id_, [tuple(e) for e in extra]))
    return list(entries.values())


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


MENU_PROPERTY = "stremiobridge.menu"  # read back by contextmenu.py
# With Arctic Zephyr Stremio, a context menu opening over one of our titles is handed
# to contextmenu.py by the skin (DialogContextMenu.xml), except while these are set:
MENU_OPEN = "stremiobridge.menu_open"      # one of this add-on's own menus is showing
MENU_NATIVE = "stremiobridge.menu_native"  # Kodi's own menu, asked for by contextmenu.py
# The first entry of our titles' context menus with that skin. Kodi's menu can't say which item
# it's for, but lists the item's own entries first: the skin hands it over when it starts with this.
MENU_MARKER = f"RunPlugin(plugin://{ADDON_ID}/?action=context_menu)"


def choose_from_menu(options):
    """``xbmcgui.Dialog().contextmenu(options)`` for this add-on's own menus
    (marked, so the skin doesn't hand them over as a title's menu)."""
    home = xbmcgui.Window(10000)
    home.setProperty(MENU_OPEN, "1")
    try:
        return xbmcgui.Dialog().contextmenu(options)
    finally:
        home.clearProperty(MENU_OPEN)


def add_context_menu(item, entries):
    """Add our entries (labels coloured) to an item's context menu. Kodi's
    addContextMenuItems writes its entries from the first slot on, so a second
    call would overwrite the first; the item keeps everything added so far
    and each call writes the whole menu again. With Arctic Zephyr Stremio the
    menu starts with MENU_MARKER (also with no `entries`: a title the skin
    should give our menu)."""
    skin = skin_active()
    if not entries and not skin:
        return
    try:
        menu = json.loads(item.getProperty(MENU_PROPERTY) or "[]")
    except ValueError:
        menu = []
    if not skin:
        entries = [(f"[COLOR {CONTEXT_MENU_COLOUR}]{label}[/COLOR]", command) for label, command in entries]
    elif not menu:  # our skin shows our own menu (contextmenu.py) for titles that start with this
        menu = [[ADDON_NAME, MENU_MARKER]]
    menu += [list(entry) for entry in entries]
    item.setProperty(MENU_PROPERTY, json.dumps(menu))
    item.addContextMenuItems([tuple(entry) for entry in menu])


WIDGETS_RELOAD = f"{ADDON_ID}.widgets.reload"
# Arctic Zephyr Stremio only: a second token, for the rows that show watch state as
# their content (WATCH_ROWS). Bumped alone when only those change, so the catalog rows
# aren't all fetched again (a process each) after every stop.
WATCH_RELOAD = f"{ADDON_ID}.widgets.reload.watch"
WATCH_ROWS = ("continue", "next_up", "watchlist")


def notify_widgets(watch_only=False):
    """Bump the home-window reload token. Skin widgets whose path ends in
    ``&reload=$INFO[Window(Home).Property(<addon id>.widgets.reload)]`` see a new
    path and reload (Kodi has no direct "reload widgets" command for add-ons).
    Arctic Zephyr Stremio's home widgets get the token from homewidgets.publish.

    `watch_only`: only Continue Watching, Next Up and the watchlist changed (a resume
    point, a dismissed show, the watchlist), not what catalog rows show; with our
    skin those rows alone reload. Other skins have one token, so everything does."""
    stamp = str(int(time.time() * 1000))
    home = xbmcgui.Window(10000)
    home.setProperty(WATCH_RELOAD if watch_only and skin_active() else WIDGETS_RELOAD, stamp)
    from .homewidgets import publish  # homewidgets imports this module

    publish()


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
