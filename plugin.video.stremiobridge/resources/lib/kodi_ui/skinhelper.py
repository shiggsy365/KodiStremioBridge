"""Small helpers for skins (Arctic Zephyr Stremio), standing in for the
Embuary Helper features its parent skin used:

    RunPlugin(plugin://plugin.video.stremiobridge/?action=skin_select&header=...)
        A selection list built from window properties Dialog.N.Label (and
        .Label2, .Icon), running Dialog.N.Builtin (several joined by "||")
        for the chosen entry. Properties are cleared afterwards.
    plugin://plugin.video.stremiobridge/?action=skin_letters&showall=true
        Jump-bar letters (# and A-Z) for the focused list; each opens skin_jump.
    plugin://plugin.video.stremiobridge/?action=skin_jump&letter=M
        Moves the focused list (control 50) to that letter.
"""

import xbmc
import xbmcgui
import xbmcplugin

from .common import SKIN_ID, jsonrpc
from .router import route

ALPHABET = ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]
# Kodi's "SMS" jumps: each key cycles through the letters on that phone key.
SMS_KEYS = {"ABC": 2, "DEF": 3, "GHI": 4, "JKL": 5, "MNO": 6, "PQRS": 7, "TUV": 8, "WXYZ": 9}
MAX_PROPERTIES = 100


def _prop(window, name):
    return xbmc.getInfoLabel(f"Window({window}).Property({name})" if window else f"Window.Property({name})")


@route("skin_select")
def skin_select(plugin, header="", window="", splitby="||", usedetails="", preselect="-1"):
    items, numbers = [], []
    for n in range(1, MAX_PROPERTIES):
        label = _prop(window, f"Dialog.{n}.Label")
        if not label:
            break
        if label in ("none", "-"):
            continue
        item = xbmcgui.ListItem(label, label2=_prop(window, f"Dialog.{n}.Label2"))
        item.setArt({"icon": _prop(window, f"Dialog.{n}.Icon")})
        items.append(item)
        numbers.append(n)
    if items:
        try:
            start = int(preselect)
        except ValueError:
            start = -1
        choice = xbmcgui.Dialog().select(header, items, preselect=start, useDetails=usedetails == "true")
        if choice >= 0:
            for builtin in _prop(window, f"Dialog.{numbers[choice]}.Builtin").split(splitby or "||"):
                if builtin.strip():
                    xbmc.executebuiltin(builtin.strip())
                    xbmc.sleep(30)
    target = f",{window}" if window else ""
    for n in range(1, MAX_PROPERTIES):
        for field in ("Builtin", "Label", "Label2", "Icon"):
            xbmc.executebuiltin(f"ClearProperty(Dialog.{n}.{field}{target})")


def jump_letters(sort_letters, showall=True):
    """``[(letter, available)]`` for a list whose items start with
    `sort_letters`; [] when there's nothing worth jumping between."""
    present = {letter.upper() for letter in sort_letters if letter}
    has_number = any(letter.isdigit() for letter in present)
    letters = [l for l in ALPHABET[1:] if l in present]
    if len(letters) + (1 if has_number else 0) < 2:
        return []
    found = []
    for letter in ALPHABET:
        available = has_number if letter == "#" else letter in present
        if available or showall:
            found.append((letter, available))
    return found


@route("skin_letters")
def skin_letters(plugin, showall="true", reload=None):
    handle = plugin.handle
    try:
        count = int(xbmc.getInfoLabel("Container.NumItems") or 0)
    except ValueError:
        count = 0
    letters = jump_letters([xbmc.getInfoLabel(f"ListItem({i}).SortLetter") for i in range(count)],
                           showall=showall != "false")
    items = []
    for letter, available in letters:
        item = xbmcgui.ListItem(letter, offscreen=True)
        if not available:
            item.setProperty("NotAvailable", "true")
        url = plugin.url_for("skin_jump", letter="0" if letter == "#" else letter) if available else ""
        items.append((url, item, False))
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def sms_action(letter, descending=False):
    if letter == "0":
        return "lastpage" if descending else "firstpage"
    key = next((k for keys, k in SMS_KEYS.items() if letter in keys), None)
    return f"jumpsms{key}" if key else None


@route("skin_jump")
def skin_jump(plugin, letter):
    letter = letter.upper()
    if plugin.handle >= 0:
        xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())
    action = sms_action(letter, xbmc.getInfoLabel("Container.SortOrder") == "Descending")
    if action is None:
        return
    xbmc.executebuiltin("SetFocus(50)")
    for _ in range(40):  # each jump moves to the next letter on that key
        jsonrpc("Input.ExecuteAction", action=action)
        xbmc.sleep(50)
        if letter == "0" or xbmc.getInfoLabel("ListItem.SortLetter").upper() == letter:
            break


# ------------------------------------------------------------------ Arctic Zephyr Stremio's hubs

# Skin Shortcuts groups of the skin's Movies Hub (window 1112) and Series Hub (1113),
# filled with this add-on's catalogs of that hub.
SKIN_HUBS = {"x1112": ("movies", "DefaultMovies.png"), "x1113": ("tvshows", "DefaultTVShows.png")}
SHORTCUTS_DIR = "special://profile/addon_data/script.skinshortcuts/"
# Skin Shortcuts keeps its hash (of what the menus were built from) in the master
# profile's folder, whichever profile is logged in; removing it has the menus rebuilt.
SHORTCUTS_HASH = f"special://masterprofile/addon_data/script.skinshortcuts/{SKIN_ID}.hash"
MENUS_BUILT = "stremio.menus_built"  # home property: Home.xml has had Skin Shortcuts build the menus


def rebuild_menus():
    """Have Skin Shortcuts rebuild the skin's menus (it does on the next skin reload)."""
    import os

    import xbmcgui
    import xbmcvfs

    try:
        os.remove(xbmcvfs.translatePath(SHORTCUTS_HASH))
    except OSError:
        pass
    # The skin only runs Skin Shortcuts' build once per session (Home.xml): ask for it again
    xbmcgui.Window(10000).clearProperty(MENUS_BUILT)


def hub_shortcuts_xml(entries):
    """Skin Shortcuts' DATA file for ``[(label, label2, icon, action)]``."""
    from xml.sax.saxutils import escape

    items = "".join(
        "\t<shortcut>\n"
        "\t\t<defaultID />\n"
        f"\t\t<label>{escape(label)}</label>\n"
        f"\t\t<label2>{escape(label2)}</label2>\n"
        f"\t\t<icon>{escape(icon)}</icon>\n"
        "\t\t<thumb />\n"
        f"\t\t<action>{escape(action)}</action>\n"
        "\t</shortcut>\n" for label, label2, icon, action in entries)
    return f"<shortcuts>\n{items}</shortcuts>\n"


def skin_hub_files(registry, url_for):
    """``{file name: DATA xml}`` for the skin's Movies and Series hubs."""
    from .common import ADDON

    tidy = ADDON.getSettingBool("tidy_names")
    files = {}
    for group, (hub, icon) in SKIN_HUBS.items():
        entries = []
        for addon, catalog in registry.hub_catalogs(hub):
            url = url_for(addon, catalog)
            entries.append((addon.display_name(catalog, tidy=tidy), addon.name, icon,
                            f'ActivateWindow(Videos,"{url}",return)'))
        files[f"{SKIN_ID}-{group}.DATA.xml"] = hub_shortcuts_xml(entries)
    return files


def update_skin_hubs(force=False):
    """Write the hubs' Skin Shortcuts files when Arctic Zephyr Stremio is the
    skin and they've changed; the menus are rebuilt on the next skin reload.
    Returns True if anything was written."""
    import os

    import xbmcvfs

    from .browse import slot_params
    from .common import get_registry, log
    from .router import Plugin

    if xbmc.getSkinDir() != SKIN_ID:
        return False
    plugin = Plugin(["plugin://plugin.video.stremiobridge/", "-1", ""])

    def url_for(addon, catalog):
        return plugin.url_for("catalog", addon=addon.key, type=catalog.type, id=catalog.id,
                              **slot_params(addon, catalog))

    folder = xbmcvfs.translatePath(SHORTCUTS_DIR)
    changed = False
    for name, xml in skin_hub_files(get_registry(), url_for).items():
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == xml and not force:
                    continue
        except OSError:
            pass
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        changed = True
    if changed:
        rebuild_menus()
        log("Skin hubs updated")
    return changed


@route("skin_hubs")
def skin_hubs(plugin):
    """Settings > Update skin hubs: rewrite the hubs now and reload the skin."""
    if update_skin_hubs(force=True):
        xbmc.executebuiltin("ReloadSkin()")
