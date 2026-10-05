"""Shared Kodi helpers: add-on info, strings, logging and factories."""

import json
import os
import time
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from podcasts.apple import Directory
from podcasts.library import Library
from podcasts.store import Store

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")


def L(string_id, **kwargs):
    """Localized string; ``{placeholders}`` are filled from kwargs."""
    text = ADDON.getLocalizedString(string_id)
    return text.format(**kwargs) if kwargs else text


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {msg}", level)


def profile_dir():
    path = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    os.makedirs(path, exist_ok=True)
    return path


def get_store():
    return Store(os.path.join(profile_dir(), "podcasts.db"))


def get_directory(store=None):
    country = ADDON.getSettingString("country").strip().lower() or "gb"
    return Directory(country=country, cache=store or get_store())


def get_library():
    store = get_store()
    return Library(store, get_directory(store), feed_ttl=ADDON.getSettingInt("feed_cache_minutes") * 60,
                   latest_days=ADDON.getSettingInt("latest_days") or 30, log=log)


def get_sync_client():
    """A gPodder client if sync is on and set up, else None."""
    if not ADDON.getSettingBool("sync_enabled"):
        return None
    server = ADDON.getSettingString("sync_server").strip()
    username = ADDON.getSettingString("sync_username").strip()
    if not server or not username:
        return None
    from podcasts.gpodder import GPODDER, NEXTCLOUD, Client

    flavour = NEXTCLOUD if ADDON.getSettingInt("sync_type") == 1 else GPODDER
    return Client(server, username, ADDON.getSettingString("sync_password"),
                  device=ADDON.getSettingString("sync_device").strip() or "kodi", flavour=flavour)


def plugin_url(action, **params):
    params = {k: v for k, v in params.items() if v is not None}
    return f"plugin://{ADDON_ID}/?{urlencode({'action': action, **params})}"


def clock_text(seconds):
    """1:02:03 / 4:05"""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def notify(message, icon=xbmcgui.NOTIFICATION_INFO, time_ms=3000):
    xbmcgui.Dialog().notification(ADDON_NAME, message, icon, time_ms)


# Handed from the play route to the playback service: what is about to play.
NOW_PLAYING = f"{ADDON_ID}.now_playing"
# Set by the service after it saves or syncs, so open listings can refresh.
CHANGED = f"{ADDON_ID}.changed"


def announce_playback(episode, offset=0):
    xbmcgui.Window(10000).setProperty(NOW_PLAYING, json.dumps(
        {"episode": episode.to_dict(), "offset": offset, "time": time.time()}))


def take_announcement(max_age=120):
    """``(Episode, offset)`` announced within `max_age` seconds, else None. Clears it."""
    from podcasts.models import Episode

    window = xbmcgui.Window(10000)
    raw = window.getProperty(NOW_PLAYING)
    window.clearProperty(NOW_PLAYING)
    try:
        data = json.loads(raw)
        if time.time() - data["time"] > max_age:
            return None
        return Episode.from_dict(data["episode"]), float(data.get("offset") or 0)
    except (ValueError, KeyError, TypeError):
        return None


def refresh_if_showing():
    """Refresh the listing if one of ours is on screen (after playback or a sync)."""
    if xbmc.getInfoLabel("Container.FolderPath").startswith(f"plugin://{ADDON_ID}/"):
        xbmc.executebuiltin("Container.Refresh")


class busy:
    """Context manager showing Kodi's busy spinner."""

    def __enter__(self):
        xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
        return self

    def __exit__(self, *exc):
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")
        return False
