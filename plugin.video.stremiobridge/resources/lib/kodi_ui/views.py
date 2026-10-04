"""Default view modes for this add-on's movie, show, season and episode lists.

The views on offer are read from the current skin's own XML files (every
control with a <viewtype>), so the list matches whatever skin is in use. The
choice is stored as "Name (id)" and applied with Container.SetViewMode when one
of our lists of that kind opens in the Videos window (never on home widgets).
"""

import glob
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .common import ADDON, ADDON_ID, L, log
from .router import route

# Container content -> (setting id, label string id)
VIEW_SETTINGS = {
    "movies": ("view_movies", 30281),
    "tvshows": ("view_tvshows", 30282),
    "seasons": ("view_seasons", 30283),
    "episodes": ("view_episodes", 30284),
}
DEFAULT_VIEW_IDS = {
    "seasons": {"skin.arctic.zephyr.stremio": 526},
}
_STORED_ID = re.compile(r"\((\d+)\)\s*$")
_LOCALIZE = re.compile(r"^\$LOCALIZE\[(\d+)\]$")
_ADDON_STRING = re.compile(r"^\$ADDON\[(\S+)\s+(\d+)\]$")


def _skin_xml_folders():
    skin = xbmcvfs.translatePath("special://skin/")
    folders = []
    try:
        root = ET.parse(os.path.join(skin, "addon.xml")).getroot()
        folders = [res.get("folder") for res in root.iter("res") if res.get("folder")]
    except (OSError, ET.ParseError):
        pass
    return [os.path.join(skin, folder) for folder in dict.fromkeys(folders or ["xml", "1080i", "720p"])]


def _label(raw, skin_id):
    raw = (raw or "").strip()
    match = _LOCALIZE.match(raw)
    if raw.isdigit() or match:
        number = int(match.group(1) if match else raw)
        text = xbmc.getLocalizedString(number)
        if not text and skin_id:
            text = xbmcaddon.Addon(skin_id).getLocalizedString(number)
        return text
    match = _ADDON_STRING.match(raw)
    if match:
        return xbmcaddon.Addon(match.group(1)).getLocalizedString(int(match.group(2)))
    return raw


def _video_window_views(folders):
    """View ids the skin allows in the Videos window (MyVideoNav.xml's <views>),
    in the skin's own order; None if it doesn't say."""
    for folder in folders:
        try:
            root = ET.parse(os.path.join(folder, "MyVideoNav.xml")).getroot()
        except (OSError, ET.ParseError):
            continue
        listed = root.find("views")
        if listed is not None and listed.text:
            return [int(v) for v in listed.text.replace(" ", "").split(",") if v.isdigit()]
    return None


def skin_views():
    """``[(view_id, name)]`` the current skin offers in the Videos window (in
    its own order), or every view it declares if it doesn't list them."""
    skin_id = xbmc.getSkinDir()
    views = {}
    folders = _skin_xml_folders()
    for folder in folders:
        for path in glob.glob(os.path.join(folder, "*.xml")):
            try:
                root = ET.parse(path).getroot()
            except (OSError, ET.ParseError):
                continue
            for control in root.iter("control"):
                viewtype = control.find("viewtype")
                view_id = control.get("id", "")
                if viewtype is None or not view_id.isdigit() or int(view_id) in views:
                    continue
                name = _label(viewtype.get("label"), skin_id) or (viewtype.text or "").strip().title()
                views[int(view_id)] = name or f"View {view_id}"
    allowed = _video_window_views(folders)
    if allowed is None:
        return sorted(views.items())
    return [(view_id, views[view_id]) for view_id in allowed if view_id in views]


_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def view_previews():
    """``{view_id: image path}`` from the skin's own preview images: files
    named after the view id anywhere under its ``extras`` folder (e.g.
    ``extras/views/52.jpg``, as the Arctic family ships). Top-level images win
    over theme variants in subfolders."""
    root = os.path.join(xbmcvfs.translatePath("special://skin/"), "extras")
    found = {}
    for folder, _, files in sorted(os.walk(root), key=lambda walk: walk[0].count(os.sep)):
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() in _IMAGE_EXTENSIONS and stem.isdigit():
                found.setdefault(int(stem), os.path.join(folder, name))
    return found


class ViewPicker(xbmcgui.WindowXMLDialog):
    """Set `heading`, `options` ([(label, label2, image)]) and `preselect`;
    afterwards `choice` is the chosen index or None."""

    heading = ""
    options = ()
    preselect = 0
    choice = None

    def onInit(self):
        self.setProperty("heading", self.heading)
        self.setProperty("no_preview", L(30293))
        items = []
        for label, label2, image in self.options:
            item = xbmcgui.ListItem(label, label2=label2)
            if image:
                item.setArt({"thumb": image})
            items.append(item)
        control = self.getControl(50)
        control.addItems(items)
        control.selectItem(self.preselect)
        self.setFocusId(50)

    def onClick(self, control_id):
        if control_id == 50:
            self.choice = self.getControl(50).getSelectedPosition()
            self.close()


def stored_view_id(content):
    setting = VIEW_SETTINGS.get(content)
    if setting is None:
        return None
    stored = ADDON.getSettingString(setting[0])
    match = _STORED_ID.search(stored)
    if match:
        return int(match.group(1))
    return DEFAULT_VIEW_IDS.get(content, {}).get(xbmc.getSkinDir())


@route("choose_view")
def choose_view(plugin, content):
    setting, label_id = VIEW_SETTINGS[content]
    views = skin_views()
    if not views:
        xbmcgui.Dialog().ok(L(30280), L(30292))
        return
    previews = view_previews()
    stored = [""] + [f"{name} ({view_id})" for view_id, name in views]
    picker = ViewPicker("stremiobridge-viewpicker.xml", ADDON.getAddonInfo("path"), "Default", "1080i")
    picker.heading = L(label_id)
    picker.options = [(L(30291), "", "")] + [
        (name, f"{L(30294)} {view_id}", previews.get(view_id, "")) for view_id, name in views]
    current = stored_view_id(content)
    picker.preselect = next((i + 1 for i, (view_id, _) in enumerate(views) if view_id == current), 0)
    picker.doModal()
    choice = picker.choice
    del picker
    if choice is None:
        return
    ADDON.setSettingString(setting, stored[choice])
    ADDON.openSettings()  # back to the settings page this was opened from


_content = {"value": None, "path": None, "focus": None}


def reset_content(path=None):
    """Start of a plugin call; `path` is the folder Kodi asked for."""
    _content.update(value=None, path=path, focus=None)


def set_focus(position):
    """Have end_listing move the selection to item `position` (0-based)."""
    _content["focus"] = position


def set_content(handle, content):
    """xbmcplugin.setContent, remembering it for end_listing."""
    import xbmcplugin

    _content["value"] = content
    xbmcplugin.setContent(handle, content)


def end_listing(handle, **kwargs):
    """endOfDirectory, then the user's default view for this kind of list."""
    import xbmcplugin

    xbmcplugin.endOfDirectory(handle, **kwargs)
    content, _content["value"] = _content["value"], None
    focus, _content["focus"] = _content["focus"], None
    if content:
        apply_view(content, path=_content["path"])
    if focus:
        focus_item(focus, path=_content["path"])


def _wait_for_listing(path, content=None, wait=3.0):
    """Wait until the Videos window shows our listing `path` (not a widget, not
    the page it was opened from). False if it didn't within `wait` seconds."""
    condition = ("Window.IsActive(videos) + !Container.IsUpdating"
                 f" + String.StartsWith(Container.FolderPath,plugin://{ADDON_ID}/)")
    if content:
        condition += f" + Container.Content({content})"
    monitor = xbmc.Monitor()
    waited = 0.0
    while not (xbmc.getCondVisibility(condition)
               and (path is None or _same_folder(xbmc.getInfoLabel("Container.FolderPath"), path))):
        if waited >= wait or monitor.waitForAbort(0.1):
            return False
        waited += 0.1
    return True


def focus_item(position, path=None, wait=3.0):
    """Select item `position` of our just-listed folder (e.g. the next episode
    to watch). Allows for Kodi's ".." item when that's shown."""
    from .common import jsonrpc

    if not _wait_for_listing(path, wait=wait):
        return False
    control_id = xbmcgui.Window(10025).getFocusId()
    if not control_id:
        return False
    parent_item = jsonrpc("Settings.GetSettingValue", setting="filelists.showparentdiritems").get("value", True)
    xbmc.executebuiltin(f"SetFocus({control_id},{position + (1 if parent_item else 0)},absolute)")
    return True


def _same_folder(a, b):
    return unquote(a or "").rstrip("/") == unquote(b or "").rstrip("/")


def apply_view(content, wait=3.0, path=None):
    """Switch our just-listed folder to the chosen view for `content`. Only in
    the Videos window, once Kodi shows *this* listing (`path`: e.g. page 2 of a
    catalog, not the page it was opened from); widgets are left alone."""
    view_id = stored_view_id(content)
    if view_id is None or not _wait_for_listing(path, content, wait):
        return False
    xbmc.executebuiltin(f"Container.SetViewMode({view_id})")
    log(f"View {view_id} for {content}")
    return True
