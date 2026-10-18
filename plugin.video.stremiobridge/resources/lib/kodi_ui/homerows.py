"""BingeTV's Home rows: choose, order, rename and remove the rows on Home and on
the Movies, TV Shows, Music and Live TV menu items (Arctic Zephyr Stremio uses Skin Shortcuts'
Customise Home for this instead).

The choices go in the same per-profile store as Customise Home's
(homewidgets.py: home_widgets.json), as Skin Shortcuts' widgetPath / widgetName
fields for slots 1-6; publish() then shows them. Once a menu item's rows have
been edited here, all six slots are saved (empty ones too), so the standard
rows no longer fill in.
"""

import json
import os
import re
from urllib.parse import quote

import xbmc
import xbmcgui

from .common import ADDON_ID, L, get_registry, jsonrpc, profile_dir
from .homewidgets import BINGETV_ITEMS, DEFAULTS, SLOTS, STORE, field, load_store, publish
from .router import route

MENU_LABELS = {"10000": 30453, "movies": 30040, "tvshows": 30341, "music": 30530, "livetv": 30531}
_COLOUR = re.compile(r"\[/?(?:COLOR|B|I)[^\]]*\]")
LISTS = (("continue", 30180), ("next_up", 30181), ("watchlist", 30300))
# Kodi's own lists a row can show: the music library, and Live TV (with its channel groups)
MUSIC_ROWS = (("musicdb://recentlyaddedalbums/", 30532), ("musicdb://recentlyplayedalbums/", 30533),
              ("musicdb://top100/albums/", 30534), ("musicdb://top100/songs/", 30535),
              ("musicdb://albums/", 30536), ("musicdb://artists/", 30537))
LIVETV_ROWS = (("pvr://channels/tv/*?view=lastplayed", 30538), ("pvr://channels/tv/*", 30539),
               ("pvr://recordings/tv/active?view=flat", 30540), ("pvr://timers/tv/timers", 30541))


def rows_of(item, store=None):
    """``[(path, name), ...]``: `item`'s rows, in order (the standard ones until it's edited)."""
    chosen = (store if store is not None else load_store()).get(item)
    if not chosen:
        return list(DEFAULTS.get(item, []))
    rows = []
    for slot in SLOTS:
        path = chosen.get(field("widgetPath", slot), "")
        if path:
            rows.append((path, chosen.get(field("widgetName", slot), "")))
    return rows


def save_rows(item, rows):
    """Save `item`'s rows (at most six) and show them on Home."""
    store = load_store()
    chosen = {}
    for slot in SLOTS:
        path, name = rows[slot - 1] if slot <= len(rows) else ("", "")
        chosen[field("widgetPath", slot)] = path
        chosen[field("widgetName", slot)] = name
    store[item] = chosen
    os.makedirs(profile_dir(), exist_ok=True)
    with open(os.path.join(profile_dir(), STORE), "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1)
    publish()


def reset_rows(item):
    store = load_store()
    if store.pop(item, None) is not None:
        with open(os.path.join(profile_dir(), STORE), "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1)
    publish()


def channel_groups():
    """``[(path, name), ...]``: Live TV's channel groups (Kodi's own "All channels" aside), as
    pvr://channels/tv/<name>@<client id>/ (the client whose groups they are: the first that has
    groups, as JSON-RPC doesn't say which group is whose)."""
    clients = jsonrpc("PVR.GetClients").get("clients") or []
    client = next((c["clientid"] for c in clients if c.get("supportschannelgroups")), None)
    if client is None:
        return []
    every = xbmc.getLocalizedString(19287)  # "All channels"
    groups = jsonrpc("PVR.GetChannelGroups", channeltype="tv").get("channelgroups") or []
    return [(f"pvr://channels/tv/{quote(g['label'], safe='')}@{client}/", g["label"])
            for g in groups if g.get("label") and g["label"] != every]


def row_sources(plugin, item=None):
    """``[(label, [(path, name), ...]), ...]``: what a row can show, grouped: your
    lists, each hub's catalogs, then Kodi's music library, Live TV and add-ons (those first
    on Music and Live TV). Add-ons' choices are None: they're browsed."""
    from .browse import catalog_item
    from .hubs import hub_label

    groups = [(L(30459), [(plugin.url_for(action), L(label)) for action, label in LISTS])]
    registry = get_registry()
    for hub in registry.hubs_in_use():
        choices = []
        for addon, catalog in registry.hub_catalogs(hub):
            choices.append((catalog_item(plugin, addon, catalog)[0], addon.display_name(catalog, tidy=True)))
        groups.append((hub_label(hub, registry), choices))
    kodi = [(L(30530), [(path, L(label)) for path, label in MUSIC_ROWS])]
    if xbmc.getCondVisibility("System.HasPVRAddon"):
        kodi.append((L(30531), [(path, L(label)) for path, label in LIVETV_ROWS] + channel_groups()))
    if item == "livetv":
        kodi.reverse()
    kodi.append((L(30543), None))  # Add-ons: a folder of one, browsed (_choose_source)
    return kodi + groups if item in ("music", "livetv") else groups + kodi


def addon_sources(item=None):
    """``[(path, name), ...]``: the music and video add-ons a row can show a folder of
    (music ones first on Music)."""
    found = {}
    for content in ("audio", "video"):
        listed = jsonrpc("Addons.GetAddons", type="xbmc.addon." + content, enabled=True,
                         properties=["name"]).get("addons") or []
        found[content] = sorted(((f"plugin://{a['addonid']}/", a.get("name") or a["addonid"])
                                 for a in listed if a.get("addonid") != ADDON_ID), key=lambda a: a[1].lower())
    first, second = ("audio", "video") if item == "music" else ("video", "audio")
    return found[first] + [a for a in found[second] if a not in found[first]]


def _browse_addon(path, name):
    """A folder of an add-on, chosen by browsing it: ``(path, name)``, or None (back)."""
    dialog = xbmcgui.Dialog()
    trail = [(path, name)]
    while trail:
        here, title = trail[-1]
        listed = jsonrpc("Files.GetDirectory", directory=here, media="files").get("files") or []
        folders = [(f["file"], _COLOUR.sub("", f.get("label") or "")) for f in listed
                   if f.get("filetype") == "directory" and f.get("file")]
        pick = dialog.select(title, [f"+  {L(30542)}"] + [label for _, label in folders])
        if pick < 0:
            trail.pop()
        elif pick == 0:
            return here, title
        else:
            trail.append(folders[pick - 1])
    return None


def _choose_source(plugin, item=None):
    """A (path, name) chosen from row_sources or an add-on's folders, or None."""
    groups = [(label, choices) for label, choices in row_sources(plugin, item) if choices or choices is None]
    dialog = xbmcgui.Dialog()
    while True:
        group = dialog.select(L(30458), [label for label, _ in groups])
        if group < 0:
            return None
        choices = groups[group][1]
        if choices is None:
            addons = addon_sources(item)
            pick = dialog.select(groups[group][0], [name for _, name in addons])
            if pick >= 0:
                chosen = _browse_addon(*addons[pick])
                if chosen:
                    return chosen
            continue
        pick = dialog.select(groups[group][0], [name for _, name in choices])
        if pick >= 0:
            return choices[pick]


def _edit_row(plugin, item, rows, index):
    """The actions for one row; True if `rows` changed."""
    path, name = rows[index]
    actions = []
    if index > 0:
        actions.append(("up", L(30012)))
    if index < len(rows) - 1:
        actions.append(("down", L(30013)))
    actions += [("rename", L(30254)), ("change", L(30456)), ("remove", L(30015))]
    choice = xbmcgui.Dialog().select(name, [label for _, label in actions])
    if choice < 0:
        return False
    do = actions[choice][0]
    if do in ("up", "down"):
        other = index - 1 if do == "up" else index + 1
        rows[index], rows[other] = rows[other], rows[index]
    elif do == "rename":
        new = xbmcgui.Dialog().input(L(30254), defaultt=name, type=xbmcgui.INPUT_ALPHANUM).strip()
        if not new or new == name:
            return False
        rows[index] = (path, new)
    elif do == "change":
        source = _choose_source(plugin, item)
        if source is None:
            return False
        rows[index] = source
    else:
        del rows[index]
    return True


def menu_items():
    """The menu items with rows: Music and Live TV unless they're hidden (or there's no Live TV)."""
    items = list(BINGETV_ITEMS)
    if xbmc.getCondVisibility("Skin.HasSetting(btv.hidemusic)"):
        items.remove("music")
    if xbmc.getCondVisibility("Skin.HasSetting(btv.hidelivetv) | !System.HasPVRAddon"):
        items.remove("livetv")
    return items


@route("home_rows")
def home_rows(plugin, item=None):
    """The editor: pick a menu item (unless given), then its rows."""
    dialog = xbmcgui.Dialog()
    items = menu_items()
    if item not in items:
        pick = dialog.select(L(30452), [L(MENU_LABELS[i]) for i in items])
        if pick < 0:
            return
        item = items[pick]
    heading = f"{L(30452)} · {L(MENU_LABELS[item])}"
    while True:
        rows = rows_of(item)
        entries = [f"{n}.  {name}" for n, (_, name) in enumerate(rows, 1)]
        extra = []
        if len(rows) < len(SLOTS):
            extra.append(("add", f"+  {L(30454)}"))
        extra.append(("reset", L(30455)))
        choice = dialog.select(heading, entries + [label for _, label in extra])
        if choice < 0:
            return
        if choice < len(rows):
            if _edit_row(plugin, item, rows, choice):
                save_rows(item, rows)
            continue
        do = extra[choice - len(rows)][0]
        if do == "add":
            source = _choose_source(plugin, item)
            if source is not None:
                save_rows(item, rows + [source])
        elif dialog.yesno(heading, L(30457)):
            reset_rows(item)
