"""Dispatcharr Bridge in Kodi: choose which source a live TV channel plays.

    RunScript(script.shiggsy365.dispatcharrbridge)            the channel being watched
    RunScript(script.shiggsy365.dispatcharrbridge,next)       straight to the next source
    context menu "Choose source" on a channel or guide entry   (context.py)

Long-press Play/Pause while watching opens it (a keymap the service installs).
"""

import json
import os
import re
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .api import Channel, Dispatcharr, DispatcharrError, NotRunning, base_from_playlist, find_channel

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
CHANNELS_FILE = "channels.json"
CHANNELS_SECONDS = 600       # channel list kept briefly: numbers and names rarely change
START_TIMEOUT = 25           # waiting for a channel started from the context menu


def L(string_id, **kwargs):
    text = ADDON.getLocalizedString(string_id)
    return text.format(**kwargs) if kwargs else text


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def notify(message, error=False):
    xbmcgui.Dialog().notification(L(32000), message,
                                  xbmcgui.NOTIFICATION_ERROR if error else ADDON.getAddonInfo("icon"), 5000)


def playlist_url():
    """IPTV Simple's playlist URL, from its (first) instance settings."""
    folder = xbmcvfs.translatePath("special://profile/addon_data/pvr.iptvsimple/")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".xml"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                match = re.search(r'<setting id="m3uUrl"[^>]*>([^<]+)<', f.read())
        except OSError:
            continue
        if match and match.group(1).strip().startswith("http"):
            return match.group(1).strip()
    return ""


def client():
    base = ADDON.getSettingString("base_url").strip() or base_from_playlist(playlist_url())
    key = ADDON.getSettingString("api_key").strip()
    if not base or not key:
        xbmcgui.Dialog().ok(L(32000), L(32010))
        ADDON.openSettings()
        return None
    return Dispatcharr(base, key)


def cached_channels(dispatcharr, refresh=False):
    path = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo("profile")), CHANNELS_FILE)
    if not refresh:
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if time.time() - saved["at"] < CHANNELS_SECONDS and saved["base"] == dispatcharr.base_url:
                return [Channel(**c) for c in saved["channels"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    channels = dispatcharr.channels()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "base": dispatcharr.base_url,
                       "channels": [c.__dict__ for c in channels]}, f)
    except OSError:
        pass
    return channels


def locate(dispatcharr, number_label, name):
    """The Dispatcharr channel for Kodi's channel number/name (refreshing the
    cached list once if it isn't there)."""
    for refresh in (False, True):
        channel = find_channel(cached_channels(dispatcharr, refresh), number_label, name)
        if channel:
            return channel
    return None


def pick(dispatcharr, channel):
    """Show the sources; returns "next", a source id, or None."""
    try:
        accounts = dispatcharr.accounts()
    except DispatcharrError as exc:
        log(f"M3U account names unavailable: {exc}")
        accounts = {}
    sources = dispatcharr.sources(channel, accounts)
    if not sources:
        notify(L(32011, channel=channel.name), error=True)
        return None
    current = dispatcharr.current_source(channel)
    items = []
    first = xbmcgui.ListItem(L(32012), label2=L(32013))
    first.setArt({"icon": "DefaultAddonsUpdates.png"})
    items.append(first)
    for position, source in enumerate(sources, 1):
        playing = source.id == current
        label = f"{position}. {source.name}" + (f"  [COLOR FF12A0C7]{L(32014)}[/COLOR]" if playing else "")
        item = xbmcgui.ListItem(label, label2=" · ".join(p for p in (source.account, source.details) if p))
        item.setArt({"icon": "DefaultTVShows.png"})
        items.append(item)
    preselect = next((i + 1 for i, s in enumerate(sources) if s.id == current), 0)
    choice = xbmcgui.Dialog().select(L(32015, channel=channel.name), items, preselect=preselect, useDetails=True)
    if choice < 0:
        return None
    return "next" if choice == 0 else sources[choice - 1].id


def apply(dispatcharr, channel, choice):
    if choice == "next":
        dispatcharr.next_source(channel)
        notify(L(32016, channel=channel.name))
    else:
        result = dispatcharr.switch(channel, choice)
        log(f"Switched {channel.name} to stream {choice}: {result.get('message', '')}")
        notify(L(32017, channel=channel.name))


def wait_until_running(dispatcharr, channel):
    monitor = xbmc.Monitor()
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline and not monitor.abortRequested():
        if dispatcharr.current_source(channel) is not None:
            return True
        monitor.waitForAbort(1)
    return False


def run(mode="playing", number_label="", name="", path=""):
    dispatcharr = client()
    if dispatcharr is None:
        return
    if mode != "context":
        number_label = xbmc.getInfoLabel("VideoPlayer.ChannelNumberLabel")
        name = xbmc.getInfoLabel("VideoPlayer.ChannelName")
        if not name and not number_label:
            notify(L(32018), error=True)
            return
    try:
        channel = locate(dispatcharr, number_label, name)
        if channel is None:
            notify(L(32019, channel=name or number_label), error=True)
            return
        choice = "next" if mode == "next" else pick(dispatcharr, channel)
        if choice is None:
            return
        if dispatcharr.current_source(channel) is None:
            # Not streaming yet (chosen from the channel list): start it, then switch.
            if not path:
                notify(L(32020, channel=channel.name), error=True)
                return
            xbmc.executebuiltin(f"PlayMedia({path})")
            if not wait_until_running(dispatcharr, channel):
                notify(L(32020, channel=channel.name), error=True)
                return
        apply(dispatcharr, channel, choice)
    except NotRunning:
        notify(L(32020, channel=name or number_label), error=True)
    except DispatcharrError as exc:
        log(str(exc), xbmc.LOGWARNING)
        notify(str(exc), error=True)
