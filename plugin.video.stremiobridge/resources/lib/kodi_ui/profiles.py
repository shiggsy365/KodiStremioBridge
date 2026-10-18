"""Profiles: choose another Kodi profile and load it straight away, instead of
logging off to Kodi's login screen and choosing there (Arctic Zephyr Stremio's
picture by the clock, BingeTV's menu bar).

Each Kodi profile has its own add-on data, so its own Stremio add-ons, MDBList,
watch history and Home rows; the add-ons themselves are shared. On BingeTV, whose
Kodi has commands for it (patches/0006 in the BingeTV repository), the picker
also adds profiles in one go: a name, one of the film avatars, and optionally a
copy of this profile's Stremio add-ons, Home rows and Live TV set-up; the new
profile then asks for its own MDBList key. It can also change a profile's
picture, and turn the "Who's watching?" screen at start-up on or off. On BingeTV
the picker also has "Exit BingeTV".

BingeTV's skin shows the profiles in its own window, like "Who's watching?"
(profile_items fills it); each profile's context menu has the rest: change its
picture, rename it, delete it, add a profile, "Who's watching?" on or off, locks.
"""

import json
import os
import re
import shutil
import xml.etree.ElementTree as ET

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from .common import ADDON, ADDON_ID, L, jsonrpc, log
from .router import route

LOCKED, CURRENT = 20166, 30447  # Kodi's "Locked"; ours "Watching now"
REVISION = "btv.profiles.rev"      # the skin's profile window reloads its list when this changes
AVATARS = os.path.join(ADDON.getAddonInfo("path"), "resources", "media", "avatars")
AVATAR_STORE = "special://masterprofile/avatars/"   # chosen avatars, where every profile can see them
MASTER = "special://masterprofile/"
# What a new profile can take from the current one (special://profile/addon_data/<id>/)
COPY = {
    ADDON_ID: ("addons.json", "home_widgets.json", "settings.xml"),
    "skin.bingetv": ("settings.xml",),
    "pvr.iptvsimple": None,                         # None: the whole folder
    "script.shiggsy365.dispatcharrbridge": ("settings.xml",),
}
# Stremio Bridge settings that belong to one person: not copied (the new profile asks)
PERSONAL = {"mdblist_api_key", "mdblist_enabled", "show_watchlist", "mdblist_scrobble"}


def profile_choices(profiles, current):
    """``[(name, label2, thumbnail)]`` for Profiles.GetProfiles' `profiles`."""
    choices = []
    for profile in profiles:
        name = profile.get("label") or ""
        if not name:
            continue
        if name == current:
            note = L(CURRENT)
        elif profile.get("lockmode"):
            note = xbmc.getLocalizedString(LOCKED)
        else:
            note = ""
        choices.append((name, note, profile.get("thumbnail") or "DefaultUser.png"))
    return choices


def can_manage():
    """BingeTV's Kodi, with its profile commands (and only the master profile manages them)."""
    from .bingetv import build_info

    return build_info() is not None and \
        xbmcvfs.translatePath("special://profile/") == xbmcvfs.translatePath(MASTER)


def _profiles():
    return jsonrpc("Profiles.GetProfiles", properties=["thumbnail", "lockmode"]).get("profiles") or []


def avatars():
    """``[(name, path), ...]``: the film avatars to choose from."""
    try:
        with open(os.path.join(AVATARS, "avatars.json"), encoding="utf-8") as f:
            listed = json.load(f)
    except (OSError, ValueError):
        return []
    return [(a["name"], os.path.join(AVATARS, a["file"])) for a in listed]


def _choose_avatar(heading):
    """An avatar, copied where every profile can see it (its path then), or None."""
    choices = avatars()
    items = []
    for name, path in choices:
        item = xbmcgui.ListItem(name, offscreen=True)
        item.setArt({"icon": path, "thumb": path})
        items.append(item)
    pick = xbmcgui.Dialog().select(heading, items, useDetails=True)
    if pick < 0:
        return None
    source = choices[pick][1]
    store = xbmcvfs.translatePath(AVATAR_STORE)
    os.makedirs(store, exist_ok=True)
    target = os.path.join(store, os.path.basename(source))
    shutil.copyfile(source, target)
    return AVATAR_STORE + os.path.basename(source)


def profile_folder(name, taken):
    """``profiles/<slug>/`` for a new profile, not one of `taken` (folders that exist)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"
    folder, n = slug, 2
    while folder in taken:
        folder, n = f"{slug}-{n}", n + 1
    return f"profiles/{folder}/"


def _without(path, drop):
    """The settings file at `path` without the settings in `drop` (as text)."""
    root = ET.parse(path).getroot()
    for setting in list(root):
        if setting.get("id") in drop:
            root.remove(setting)
    return ET.tostring(root, encoding="unicode")


def copy_setup(target_dir):
    """This profile's Stremio add-ons, Home rows, skin options and Live TV set-up into the
    profile folder `target_dir` (not its MDBList key, nor its watch history)."""
    here = xbmcvfs.translatePath("special://profile/addon_data/")
    there = os.path.join(xbmcvfs.translatePath(MASTER), target_dir, "addon_data")
    for addon_id, files in COPY.items():
        source = os.path.join(here, addon_id)
        if not os.path.isdir(source):
            continue
        dest = os.path.join(there, addon_id)
        if files is None:
            shutil.copytree(source, dest, dirs_exist_ok=True)
            continue
        os.makedirs(dest, exist_ok=True)
        for name in files:
            path = os.path.join(source, name)
            if not os.path.isfile(path):
                continue
            if addon_id == ADDON_ID and name == "settings.xml":
                text = _without(path, PERSONAL)
            elif addon_id == "skin.bingetv":
                text = _without(path, {"stremio.setup_done"})  # so the new profile's setup runs
            else:
                shutil.copyfile(path, os.path.join(dest, name))
                continue
            with open(os.path.join(dest, name), "w", encoding="utf-8") as f:
                f.write(text)


@route("add_profile")
def add_profile(plugin=None):
    """BingeTV: a new profile, in one go (see the module's description)."""
    dialog = xbmcgui.Dialog()
    name = dialog.input(L(30516), type=xbmcgui.INPUT_ALPHANUM).strip()
    if not name:
        return
    profiles = _profiles()
    if any(p.get("label") == name for p in profiles):
        dialog.ok(L(30446), L(30517, name=name))
        return
    thumb = _choose_avatar(L(30518, name=name)) or ""
    taken = set(os.listdir(os.path.join(xbmcvfs.translatePath(MASTER), "profiles"))) \
        if os.path.isdir(os.path.join(xbmcvfs.translatePath(MASTER), "profiles")) else set()
    folder = profile_folder(name, taken)
    copy = dialog.yesno(L(30446), L(30519, name=name), nolabel=L(30520), yeslabel=L(30521))
    xbmc.executebuiltin(f'BingeTV.AddProfile("{name}","{thumb}","{folder}")', True)
    if not any(p.get("label") == name for p in _profiles()):
        dialog.ok(L(30446), L(30522))
        return
    if copy:
        try:
            copy_setup(folder)
        except OSError as exc:
            log(f"Copying the set-up into {folder} failed: {exc}", xbmc.LOGERROR)
    log(f"Added profile {name} ({folder})")
    _changed()
    if dialog.yesno(L(30446), L(30523, name=name)):
        xbmc.executebuiltin(f'LoadProfile("{name}",prompt)')


def _changed():
    """The skin's profile window shows the change."""
    home = xbmcgui.Window(10000)
    home.setProperty(REVISION, str(int(home.getProperty(REVISION) or 0) + 1))


@route("profile_picture")
def profile_picture(plugin=None, name=None):
    """BingeTV: another film avatar for a profile (the one you're using unless named)."""
    name = name or xbmc.getInfoLabel("System.ProfileName")
    thumb = _choose_avatar(L(30518, name=name))
    if thumb:
        xbmc.executebuiltin(f'BingeTV.SetProfileThumb("{name}","{thumb}")', True)
        _changed()


@route("rename_profile")
def rename_profile(plugin, name):
    new = xbmcgui.Dialog().input(L(30545, name=name), defaultt=name, type=xbmcgui.INPUT_ALPHANUM).strip()
    if not new or new == name:
        return
    if any(p.get("label") == new for p in _profiles()):
        xbmcgui.Dialog().ok(L(30446), L(30517, name=new))
        return
    xbmc.executebuiltin(f'BingeTV.RenameProfile("{name}","{new}")', True)
    _changed()


@route("delete_profile")
def delete_profile(plugin, name):
    """Kodi asks first (and whether the profile's files go too)."""
    xbmc.executebuiltin(f'BingeTV.DeleteProfile("{name}")', True)
    _changed()


@route("load_profile")
def load_profile(plugin, name):
    if name == xbmc.getInfoLabel("System.ProfileName"):
        xbmc.executebuiltin("Action(Back)")  # the one in use: just close the picker
        return
    log(f"Switching to profile {name}")
    xbmcgui.Window(10000).setProperty("btv.fromsettings", "1")  # BingeTV: Home focuses its menu button
    xbmc.executebuiltin(f'LoadProfile("{name}",prompt)')  # Kodi asks for a locked one's code


def profile_menu(plugin, name, current, master_name, manage):
    """A profile's context menu in the skin's profile window: ``[(label, builtin)]``."""
    if not manage:
        return [(L(30525), plugin.run_url("profile_picture", name=name))] if name == current else []
    login = login_screen_on()
    menu = [(L(30525), plugin.run_url("profile_picture", name=name)),
            (L(30546), plugin.run_url("rename_profile", name=name))]
    if name not in (current, master_name):
        menu.append((L(30547), plugin.run_url("delete_profile", name=name)))
    menu += [(L(30524), plugin.run_url("add_profile")),
             (f"{L(30526)}: {L(30527) if login else L(30528)}", plugin.run_url("toggle_login_screen")),
             (L(30529), "ActivateWindow(Profiles)")]
    return menu


@route("toggle_login_screen")
def toggle_login_screen(plugin=None):
    xbmc.executebuiltin(f"BingeTV.UseLoginScreen({'false' if login_screen_on() else 'true'})", True)
    xbmcgui.Dialog().notification(L(30446), L(30527) if login_screen_on() else L(30528))


@route("profile_items")
def profile_items(plugin, rev=None):
    """The skin's profile window: each profile (its menu: profile_menu), then "Add a profile"."""
    profiles = _profiles()
    current = xbmc.getInfoLabel("System.ProfileName")
    manage = can_manage()
    master_name = profiles[0].get("label") if profiles else ""
    items = []
    for name, note, thumb in profile_choices(profiles, current):
        item = xbmcgui.ListItem(name, note, offscreen=True)
        item.setArt({"icon": thumb, "thumb": thumb})
        item.addContextMenuItems(profile_menu(plugin, name, current, master_name, manage))
        items.append((plugin.url_for("load_profile", name=name), item, False))
    if manage:
        item = xbmcgui.ListItem(L(30524), offscreen=True)
        item.setArt({"icon": "DefaultAddSource.png", "thumb": "DefaultAddSource.png"})
        items.append((plugin.url_for("add_profile"), item, False))
    xbmcplugin.addDirectoryItems(plugin.handle, items, len(items))
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


def login_screen_on():
    """Whether Kodi shows its profile picker ("Who's watching?") as it starts."""
    try:
        root = ET.parse(xbmcvfs.translatePath(MASTER + "profiles.xml")).getroot()
        return (root.findtext("useloginscreen") or "").strip().lower() == "true"
    except (OSError, ET.ParseError):
        return False


@route("switch_profile")
def switch_profile(plugin):
    profiles = _profiles()
    current = xbmc.getInfoLabel("System.ProfileName")
    choices = profile_choices(profiles, current)
    from .bingetv import build_info

    bingetv = build_info() is not None
    manage = can_manage()
    if len(choices) < 2 and not manage and not bingetv:  # nothing to choose: Kodi's own login screen
        xbmc.executebuiltin("System.LogOff")
        return
    items, actions = [], []
    for name, note, thumb in choices:
        item = xbmcgui.ListItem(name, note, offscreen=True)
        item.setArt({"icon": thumb, "thumb": thumb})
        items.append(item)
        actions.append(("load", name))
    if manage:
        login = login_screen_on()
        for action, label, label2, icon in (
                ("add", L(30524), "", "DefaultAddSource.png"),
                ("picture", L(30525), current, os.path.join(AVATARS, "director.png")),
                ("login", L(30526), L(30527) if login else L(30528), "DefaultUser.png"),
                ("lock", L(30529), "", "DefaultUser.png")):
            item = xbmcgui.ListItem(label, label2, offscreen=True)
            item.setArt({"icon": icon, "thumb": icon})
            items.append(item)
            actions.append((action, None))
    if bingetv:  # BingeTV: leaving it is here too (its menu bar has no power button by default)
        item = xbmcgui.ListItem(L(30544), offscreen=True)
        item.setArt({"icon": "bingetv/bar/power.png", "thumb": "bingetv/bar/power.png"})
        items.append(item)
        actions.append(("exit", None))
    names = [name for name, _, _ in choices]
    pick = xbmcgui.Dialog().select(L(30446), items, preselect=names.index(current) if current in names else -1,
                                   useDetails=True)
    if pick < 0:
        return
    action, name = actions[pick]
    if action == "load":
        if name == current:
            return
        log(f"Switching to profile {name}")
        xbmcgui.Window(10000).setProperty("btv.fromsettings", "1")  # BingeTV: Home focuses its menu button
        # Kodi asks for a locked profile's code itself (prompt)
        xbmc.executebuiltin(f'LoadProfile("{name}",prompt)')
    elif action == "add":
        add_profile(plugin)
    elif action == "picture":
        profile_picture(plugin)
    elif action == "login":
        xbmc.executebuiltin(f"BingeTV.UseLoginScreen({'false' if login_screen_on() else 'true'})", True)
        xbmcgui.Dialog().notification(L(30446), L(30527) if login_screen_on() else L(30528))
    elif action == "exit":
        xbmc.executebuiltin("Quit")
    elif action == "lock":
        # Kodi's own profile settings: a lock code per profile (and the master lock)
        xbmc.executebuiltin("ActivateWindow(Profiles)")
