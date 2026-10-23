"""Settings shown in Stremio Bridge's settings but kept elsewhere, so there's one
place to set everything (BingeTV's settings page):

- BingeTV's skin options (Home page layout): skin settings (Skin.HasSetting);
- IPTV: IPTV Simple's playlist and guide addresses (its first instance's
  settings file) and Dispatcharr Bridge's settings.

sync() runs when the service starts and whenever our settings change. Each value
goes whichever way it changed since the last sync (linked.json remembers it): set
here, it's passed on; changed over there (e.g. in IPTV Simple's own settings),
it's brought here. The first time, the other side's value is taken.

open_kodi opens one of Kodi's own settings pages from ours, and our settings
again once you're back.
"""

import json
import os
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .common import ADDON, ADDON_ID, BINGETV_SKIN_ID, jsonrpc, log, profile_dir
from .router import route

STORE = "linked.json"
IPTV = "pvr.iptvsimple"
IPTV_INSTANCE = f"special://profile/addon_data/{IPTV}/instance-settings-1.xml"
DISPATCHARR = "script.shiggsy365.dispatcharrbridge"
OSD_TIMES = (3, 5, 7, 10)   # btv_osd_hide; 7 is the skin's own default (no setting)


# ------------------------------------------------------------------ the other sides

def _skin_bool(name, inverted=False):
    def get():
        on = xbmc.getCondVisibility(f"Skin.HasSetting({name})")
        return (not on) if inverted else on

    def put(value):
        on = (not value) if inverted else value
        xbmc.executebuiltin(f"Skin.SetBool({name})" if on else f"Skin.Reset({name})")
    return get, put


def _osd_get():
    for seconds in (3, 5, 10):
        if xbmc.getCondVisibility(f"Skin.HasSetting(btv.osd{seconds})"):
            return seconds
    return 7


def _osd_put(seconds):
    for each in (3, 5, 7, 10):
        builtin = "Skin.SetBool" if each == seconds and each != 7 else "Skin.Reset"
        xbmc.executebuiltin(f"{builtin}(btv.osd{each})")


def _iptv_path():
    return xbmcvfs.translatePath(IPTV_INSTANCE)


def _iptv_values():
    """{setting id: value} from IPTV Simple's first instance, {} if it has none."""
    try:
        root = ET.parse(_iptv_path()).getroot()
    except (OSError, ET.ParseError):
        return {}
    return {s.get("id"): (s.text or "") for s in root.iter("setting")}


def _iptv_get(key, path_type):
    def get():
        values = _iptv_values()
        return values.get(key, "") if values.get(path_type, "1") == "1" else ""
    return get


def _iptv_put(key, path_type):
    def put(value):
        write_iptv({path_type: "1", key: value})
    return put


def write_iptv(changes):
    """Set IPTV Simple's first instance's settings, then restart it so it loads them."""
    path = _iptv_path()
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        root = ET.Element("settings", version="2")
        tree = ET.ElementTree(root)
        for sid, value in (("kodi_addon_instance_name", "BingeTV"), ("kodi_addon_instance_enabled", "true")):
            ET.SubElement(root, "setting", id=sid).text = value
    settings = {s.get("id"): s for s in root.iter("setting")}
    for sid, value in changes.items():
        element = settings.get(sid)
        if element is None:
            element = ET.SubElement(root, "setting", id=sid)
        element.attrib.pop("default", None)
        element.text = value
    if dispatcharr_playlist(changes.get("m3uUrl")) and not (settings.get("defaultMimeType") is not None
                                                           and settings["defaultMimeType"].text):
        element = settings.get("defaultMimeType")
        if element is None:
            element = ET.SubElement(root, "setting", id="defaultMimeType")
        element.attrib.pop("default", None)
        element.text = TS_MIME  # (see ensure_stream_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=False)
    _restart_addon(IPTV, enable=bool(changes.get("m3uUrl")))


LIVE_TV_MARKER = "live_tv_enabled"


def enable_live_tv():
    """A profile whose Live TV is set up (copied from another profile, or restored) but whose
    IPTV Simple is off (Kodi keeps that per profile): turned on, once (after that, off stays off)."""
    marker = os.path.join(profile_dir(), LIVE_TV_MARKER)
    if os.path.exists(marker):
        return
    values = _iptv_values()
    if not (values.get("m3uUrl") or values.get("m3uPath")):
        return
    details = jsonrpc("Addons.GetAddonDetails", addonid=IPTV, properties=["enabled"]) or {}
    enabled = (details.get("addon") or {}).get("enabled")
    if enabled is None:
        return  # not installed
    if not enabled:
        jsonrpc("Addons.SetAddonEnabled", addonid=IPTV, enabled=True)
        log("Live TV is set up in this profile: IPTV Simple turned on")
    os.makedirs(profile_dir(), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        f.write("1")


TS_MIME = "video/mp2t"


def dispatcharr_playlist(url):
    """A Dispatcharr playlist (…/output/m3u): its streams (…/proxy/ts/stream/<id>) are MPEG-TS."""
    return "/output/m3u" in (url or "")


def ensure_stream_type():
    """With a Dispatcharr playlist, IPTV Simple's default MIME type is MPEG-TS. Its stream
    addresses have no extension, so otherwise IPTV Simple and then Kodi probe each one to tell
    its type, on Kodi's main thread: with Dispatcharr slow to answer, that froze BingeTV for up
    to 2 minutes (3 tries of 20 s, twice) whenever a channel started."""
    values = _iptv_values()
    if dispatcharr_playlist(values.get("m3uUrl")) and not values.get("defaultMimeType"):
        log("IPTV Simple: Dispatcharr's streams are MPEG-TS (no probing them as channels start)")
        write_iptv({"defaultMimeType": TS_MIME})


def _restart_addon(addon_id, enable=False):
    details = jsonrpc("Addons.GetAddonDetails", addonid=addon_id, properties=["enabled"]) or {}
    enabled = (details.get("addon") or {}).get("enabled")
    if enabled is None:
        return  # not installed
    if enabled:
        jsonrpc("Addons.SetAddonEnabled", addonid=addon_id, enabled=False)
    if enabled or enable:
        jsonrpc("Addons.SetAddonEnabled", addonid=addon_id, enabled=True)


def _addon_setting(addon_id, key, kind="string"):
    def addon():
        try:
            return xbmcaddon.Addon(addon_id)
        except RuntimeError:  # not installed
            return None

    def get():
        other = addon()
        if other is None:
            return None
        return other.getSettingBool(key) if kind == "bool" else other.getSettingString(key)

    def put(value):
        other = addon()
        if other is not None:
            (other.setSettingBool if kind == "bool" else other.setSettingString)(key, value)
    return get, put


def links():
    """``[(our setting, kind, get, put), ...]`` for what's linked here and now."""
    out = []
    if xbmc.getSkinDir() == BINGETV_SKIN_ID:
        for ours, name, inverted in (("btv_show_music", "btv.hidemusic", True),
                                     ("btv_show_livetv", "btv.hidelivetv", True),
                                     ("btv_show_power", "btv.showpower", False),
                                     ("btv_ribbons", "btv.hideribbons", True),
                                     ("btv_touch", "touchmode", False)):
            out.append((ours, "bool") + _skin_bool(name, inverted))
        out.append(("btv_osd_hide", "int", _osd_get, _osd_put))
    out.append(("iptv_m3u_url", "string", _iptv_get("m3uUrl", "m3uPathType"), _iptv_put("m3uUrl", "m3uPathType")))
    out.append(("iptv_epg_url", "string", _iptv_get("epgUrl", "epgPathType"), _iptv_put("epgUrl", "epgPathType")))
    for ours, key, kind in (("dispatcharr_url", "base_url", "string"), ("dispatcharr_api_key", "api_key", "string"),
                            ("dispatcharr_long_press", "long_press", "bool")):
        out.append((ours, kind) + _addon_setting(DISPATCHARR, key, kind))
    return out


# ------------------------------------------------------------------ syncing

def _fresh():
    """Our settings as they are now (a long-running service's own Addon object can lag behind
    changes made in the settings page)."""
    try:
        return xbmcaddon.Addon(ADDON_ID)
    except RuntimeError:
        return ADDON


def _ours(setting, kind, addon=None):
    addon = addon or ADDON
    if kind == "bool":
        return addon.getSettingBool(setting)
    if kind == "int":
        return addon.getSettingInt(setting)
    return addon.getSettingString(setting)


def _set_ours(setting, kind, value, addon=None):
    addon = addon or ADDON
    if kind == "bool":
        addon.setSettingBool(setting, bool(value))
    elif kind == "int":
        addon.setSettingInt(setting, int(value))
    else:
        addon.setSettingString(setting, str(value))


def _store_path():
    return os.path.join(profile_dir(), STORE)


def _load():
    try:
        with open(_store_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def snapshot():
    """Our side of each linked value, cheaply: when it differs from the last one, something
    was changed here and sync() is due. (The service checks this every few seconds as well
    as on Kodi's settings-changed event, which a change made in the settings page doesn't
    always bring while the dialog is up.)"""
    values, addon = [], _fresh()
    for setting, kind, _, _ in links():
        try:
            values.append(_ours(setting, kind, addon))
        except Exception:  # noqa: BLE001
            values.append(None)
    return tuple(values)


def sync():
    """Bring each linked value into step (see the module's description)."""
    last = _load()
    changed = False
    addon = _fresh()
    for setting, kind, get, put in links():
        try:
            theirs, ours = get(), _ours(setting, kind, addon)
            if theirs is None:  # the other side isn't there (e.g. not installed)
                continue
            if setting not in last:      # first time: theirs
                if ours != theirs:
                    _set_ours(setting, kind, theirs, addon)
                last[setting] = theirs
            elif ours != last[setting]:  # changed here
                put(ours)
                last[setting] = ours
                log(f"Settings: {setting} passed on")
            elif theirs != last[setting]:  # changed over there
                _set_ours(setting, kind, theirs, addon)
                last[setting] = theirs
            else:
                continue
            changed = True
        except Exception as exc:  # noqa: BLE001 - one link failing mustn't stop the others
            log(f"Settings: {setting} not synced: {exc}")
    if changed:
        os.makedirs(profile_dir(), exist_ok=True)
        with open(_store_path(), "w", encoding="utf-8") as f:
            json.dump(last, f, indent=1)


# ------------------------------------------------------------------ Kodi's own pages

EXPERT = 10039       # "Expert": the settings level button's label (control 20) at that level
LEVEL_BUTTON = 20
GIVE_UP = 30 * 60    # stop waiting to bring our settings back after this long


@route("open_kodi")
def open_kodi(plugin, window, section="", expert=None):
    """One of Kodi's settings pages (at `section`, and the expert level if asked);
    our settings again once you're back where you were."""
    came_from = xbmcgui.getCurrentWindowId()
    home = xbmcgui.Window(10000)
    home.setProperty("btv.fromsettings", "1")  # BingeTV: Home focuses the menu bar coming back
    xbmc.executebuiltin(f"ActivateWindow({window},{section})" if section else f"ActivateWindow({window})")
    monitor = xbmc.Monitor()
    for _ in range(50):
        if xbmcgui.getCurrentWindowId() != came_from:
            break
        monitor.waitForAbort(0.1)
    if expert == "1":
        changed = False
        for _ in range(4):
            # the button's label is the level's name, perhaps with more after it ("Expert ( )")
            if xbmc.getInfoLabel(f"Control.GetLabel({LEVEL_BUTTON})").startswith(xbmc.getLocalizedString(EXPERT)):
                break
            xbmc.executebuiltin("Action(settingslevelchange)")
            changed = True
            monitor.waitForAbort(0.4)
        if changed and section:  # changing the level goes back to the first section
            xbmc.executebuiltin(f"ActivateWindow({window},{section})")
    started = time.time()
    while not monitor.waitForAbort(0.5):
        if xbmcgui.getCurrentWindowId() == came_from and not xbmc.getCondVisibility("System.HasActiveModalDialog"):
            xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
            return
        if xbmc.Player().isPlayingVideo() or time.time() - started > GIVE_UP:
            home.clearProperty("btv.fromsettings")
            return
