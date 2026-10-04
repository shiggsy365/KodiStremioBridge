"""Our own context menu for titles, used with Arctic Zephyr Stremio.

Kodi builds its context menu itself (Play, Information, Queue item, Play next,
Add to favourites, then add-on entries), and a skin can't reorder or drop
those (nor can it tell which item the menu is for). So when the menu opens on
one of our titles, the service closes it and runs this route instead
(service.MenuSwap), which shows:

    Play, Information, Mark as watched (or unwatched: one entry, for the movie,
    episode, season or show the item is), Show Playable Streams, Add to
    watchlist, (the item's other entries), Play trailer, Add to favourites

The show itself is browsed from Information (its season and episode browser).

The item's own entries (``stremiobridge.menu``, see common.add_context_menu)
supply the labels and commands for most of these, so they stay in step with
the plain context menu.
"""

import json
import re
from urllib.parse import parse_qsl, urlsplit

import xbmc
import xbmcgui

from .common import ADDON_ID, MENU_PROPERTY, L, jsonrpc
from .listitems import PLAYABLE_TYPES
from .router import route

OPEN_PROPERTY = "stremiobridge.menu_open"  # set while ours shows (it uses the same window)
_COLOUR = re.compile(r"\[/?COLOR[^\]]*\]")

# Where the item's own entries go, by what they run; None drops them.
_RANKS = (
    ("action=extended_info", None),     # Information covers it
    ("action=library_", None),
    ("action=set_watched", 3),
    ("pick=1", 4),
    ("action%3Dmeta", 5), ("action=meta", 5),
    ("action=watchlist_", 6),
    ("action=play_trailer", 8),
)
OTHER_RANK = 7


def item_target(path):
    """``(action, params)`` for one of our title paths, else None."""
    if not path.startswith(f"plugin://{ADDON_ID}/"):
        return None
    params = dict(parse_qsl(urlsplit(path).query))
    action = params.pop("action", "")
    if action not in ("play", "meta", "season", "extended_info") or not params.get("type") or not params.get("id"):
        return None
    return action, params


def rank(command):
    for needle, place in _RANKS:
        if needle in command:
            return place
    return OTHER_RANK


def menu_entries(plugin, path, own_entries, favourite, watched=False):
    """``[(label, builtin)]`` in our order. `own_entries` are the item's
    ``[label, builtin]`` pairs; `favourite` is True/False (is it one);
    `watched` is the item's tick (for Mark as watched, when the item's own
    entries leave it to Kodi)."""
    action, params = item_target(path)
    type_, id_ = params["type"], params["id"]
    if action == "play":
        show_id = params.get("meta")
        info = plugin.url_for("extended_info", type=type_, id=show_id or id_,
                              video=id_ if show_id and show_id != id_ else None)
        play = path
    elif action == "extended_info":
        info, video = path, params.get("video")
        play = plugin.url_for("play", type=type_, id=video or id_, meta=id_ if video else None)
    elif action == "season":
        info = plugin.url_for("extended_info", type=type_, id=id_, season=params.get("season"))
        play = None
    else:  # a show
        info, play = plugin.url_for("extended_info", type=type_, id=id_), None

    entries = []
    if play and (type_ in PLAYABLE_TYPES or action != "meta"):
        entries.append((1, xbmc.getLocalizedString(208), f"PlayMedia({play})"))
    elif action == "season":  # its next episode to watch, as the info page's Play does
        entries.append((1, xbmc.getLocalizedString(208),
                        plugin.run_url("info_play", type=type_, id=id_, season=params.get("season"))))
    entries.append((2, xbmc.getLocalizedString(19033), f"RunPlugin({info})"))
    for label, command in own_entries:
        clean = _COLOUR.sub("", label)
        place = rank(command)
        if place is not None:
            entries.append((place, clean, command))
    if not any(place == 3 for place, _, _ in entries):
        if action == "meta":
            mark = {"type": type_, "id": id_}
        elif action == "season":
            mark = {"type": type_, "id": id_, "season": params.get("season")}
        elif action == "play":
            mark = {"type": type_, "id": id_, "meta": params.get("meta")}
        else:
            video = params.get("video")
            mark = {"type": type_, "id": video or id_, "meta": id_ if video else None}
        entries.append((3, L(30191 if watched else 30190),
                        plugin.run_url("set_watched", value=int(not watched), **mark)))
    entries.append((9, xbmc.getLocalizedString(14077 if favourite else 14076), "favourite"))
    entries.sort(key=lambda e: e[0])  # stable: the item's own order within a rank
    return [(label, command) for _, label, command in entries]


def is_favourite(path):
    favourites = jsonrpc("Favourites.GetFavourites", properties=["path", "windowparameter"]).get("favourites") or []
    return any(path in (f.get("path"), f.get("windowparameter")) for f in favourites)


def toggle_favourite(path, title, thumb, folder):
    """Kodi's Favourites.AddFavourite adds, or removes one already there."""
    if folder:
        jsonrpc("Favourites.AddFavourite", title=title, type="window", window="videos",
                windowparameter=path, thumbnail=thumb)
    else:
        jsonrpc("Favourites.AddFavourite", title=title, type="media", path=path, thumbnail=thumb)


# The skin's info page lists whose items get our menu: the show browser's
# episodes and seasons, and Find similar (Includes_Stremio.xml / Includes_DialogVideoInfo.xml).
INFO_LISTS = (5061, 5060, 9502)


def focused_item():
    """``(source, path)``: the infolabel prefix of the item a context menu is
    for, and its path. In the info page, ``ListItem`` is the page's own title
    even while one of its lists has focus, so those lists are asked directly."""
    if xbmc.getCondVisibility("Window.IsVisible(movieinformation)"):
        for control in INFO_LISTS:
            if xbmc.getCondVisibility(f"Control.HasFocus({control})"):
                source = f"Container({control}).ListItem"
                return source, xbmc.getInfoLabel(f"{source}.FileNameAndPath")
    return "ListItem", xbmc.getInfoLabel("ListItem.FileNameAndPath")


@route("context_menu")
def context_menu(plugin, path="", source="ListItem"):
    home = xbmcgui.Window(10000)
    home.setProperty(OPEN_PROPERTY, "1")
    try:
        _show_menu(plugin, path or xbmc.getInfoLabel(f"{source}.FileNameAndPath"), source)
    finally:
        home.clearProperty(OPEN_PROPERTY)


def _show_menu(plugin, path, source="ListItem"):
    if item_target(path) is None:
        return
    title = xbmc.getInfoLabel(f"{source}.Label")
    thumb = xbmc.getInfoLabel(f"{source}.Art(poster)") or xbmc.getInfoLabel(f"{source}.Art(thumb)")
    folder = xbmc.getCondVisibility(f"{source}.IsFolder")
    try:
        own = json.loads(xbmc.getInfoLabel(f"{source}.Property({MENU_PROPERTY})") or "[]")
    except ValueError:
        own = []
    watched = (xbmc.getInfoLabel(f"{source}.PlayCount") or "0") != "0"
    entries = menu_entries(plugin, path, own, is_favourite(path), watched)
    choice = xbmcgui.Dialog().contextmenu([label for label, _ in entries])
    if choice < 0:
        return
    command = entries[choice][1]
    if command == "favourite":
        toggle_favourite(path, title, thumb, folder)
    else:
        xbmc.executebuiltin(command)
