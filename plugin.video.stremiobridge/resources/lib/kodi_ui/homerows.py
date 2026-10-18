"""BingeTV's Home rows: choose, order, rename and remove the rows on Home and on
the Movies and TV Shows menu items (Arctic Zephyr Stremio uses Skin Shortcuts'
Customise Home for this instead).

The choices go in the same per-profile store as Customise Home's
(homewidgets.py: home_widgets.json), as Skin Shortcuts' widgetPath / widgetName
fields for slots 1-6; publish() then shows them. Once a menu item's rows have
been edited here, all six slots are saved (empty ones too), so the standard
rows no longer fill in.
"""

import json
import os

import xbmcgui

from .common import L, get_registry, profile_dir
from .homewidgets import DEFAULTS, ITEMS, SLOTS, STORE, field, load_store, publish
from .router import route

MENU_LABELS = {"10000": 30453, "movies": 30040, "tvshows": 30341}
LISTS = (("continue", 30180), ("next_up", 30181), ("watchlist", 30300))


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


def row_sources(plugin):
    """``[(label, [(path, name), ...]), ...]``: what a row can show, grouped: your
    lists, then each hub's catalogs."""
    from .browse import catalog_item
    from .hubs import hub_label

    groups = [(L(30459), [(plugin.url_for(action), L(label)) for action, label in LISTS])]
    registry = get_registry()
    for hub in registry.hubs_in_use():
        choices = []
        for addon, catalog in registry.hub_catalogs(hub):
            choices.append((catalog_item(plugin, addon, catalog)[0], addon.display_name(catalog, tidy=True)))
        groups.append((hub_label(hub, registry), choices))
    return groups


def _choose_source(plugin):
    """A (path, name) chosen from row_sources, or None."""
    groups = [(label, choices) for label, choices in row_sources(plugin) if choices]
    dialog = xbmcgui.Dialog()
    while True:
        group = dialog.select(L(30458), [label for label, _ in groups])
        if group < 0:
            return None
        choices = groups[group][1]
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
        source = _choose_source(plugin)
        if source is None:
            return False
        rows[index] = source
    else:
        del rows[index]
    return True


@route("home_rows")
def home_rows(plugin, item=None):
    """The editor: pick a menu item (unless given), then its rows."""
    dialog = xbmcgui.Dialog()
    if item not in ITEMS:
        pick = dialog.select(L(30452), [L(MENU_LABELS[i]) for i in ITEMS])
        if pick < 0:
            return
        item = ITEMS[pick]
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
            source = _choose_source(plugin)
            if source is not None:
                save_rows(item, rows + [source])
        elif dialog.yesno(heading, L(30457)):
            reset_rows(item)
