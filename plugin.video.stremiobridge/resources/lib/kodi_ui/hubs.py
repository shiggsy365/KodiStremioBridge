"""Hubs (Movies, TV Shows, Anime, More, plus any the user creates) and
organising the catalogs in them.

A catalog goes in the hub for its type unless the user moves it; within a hub
catalogs appear in the user's order, under the user's name for them, and can
be hidden or pinned to the front page (Settings > Addons > Organise catalogs).
Hubs themselves can be created, renamed and (the user's own) removed there.
"""

import xbmcgui
import xbmcplugin

from stremio import StremioError
from .browse import FILTER_PREFIX, catalog_item, slot_params, tile_art, type_label
from .common import L, add_context_menu, get_registry, refresh_container
from .router import route

HUB_LABELS = {"movies": 30040, "tvshows": 30341, "anime": 30342, "more": 30343}
HUB_SEARCH_TYPES = {"movies": "movie", "tvshows": "series"}  # hubs that get "Search …"
# A "genre" extra with only a couple of options is something else (e.g. TMDB
# Trending's Day/Week), so such catalogs don't contribute to Genres.
MIN_GENRE_OPTIONS = 5


def hub_label(hub, registry=None):
    name = (registry or get_registry()).hub_name(hub)
    return name or (L(HUB_LABELS[hub]) if hub in HUB_LABELS else hub)


def hub_art(hub, registry=None):
    """The hub's tile; renamed and user-made hubs get one without a caption."""
    if hub in HUB_LABELS:
        named = (registry or get_registry()).hub_name(hub)
        return tile_art(f"hub_{hub}_plain.png" if named else f"hub_{hub}.png")
    return tile_art("hub_custom.png")


def hub_item(plugin, hub, registry=None):
    item = xbmcgui.ListItem(hub_label(hub, registry))
    item.setArt(hub_art(hub, registry))
    return plugin.url_for("hub", hub=hub), item, True


# ------------------------------------------------------------------ browsing

def hub_genres(hub_catalogs):
    """``{genre: (addon, catalog)}``: for each genre, the first catalog in the
    hub's order that offers it."""
    genres = {}
    for addon, catalog in hub_catalogs:
        extra = catalog.extra_prop("genre")
        if extra is None or len(extra.options) < MIN_GENRE_OPTIONS:
            continue
        for genre in extra.options:
            if genre.lower() != "none":
                genres.setdefault(genre, (addon, catalog))
    return dict(sorted(genres.items(), key=lambda kv: kv[0].lower()))


@route("hub")
def hub_view(plugin, hub):
    handle = plugin.handle
    registry = get_registry()
    if hub not in registry.hubs():
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    catalogs = registry.hub_catalogs(hub)
    xbmcplugin.setPluginCategory(handle, hub_label(hub, registry))

    search_type = HUB_SEARCH_TYPES.get(hub)
    if search_type and registry.search_catalogs(search_type):
        item = xbmcgui.ListItem(L(30079, type=hub_label(hub, registry)))
        item.setArt(tile_art("search.png"))
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("new_search", type=search_type), item, isFolder=False)
    if hub_genres(catalogs):
        item = xbmcgui.ListItem(L(30351))
        item.setArt(tile_art("genres.png"))
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("hub_genres", hub=hub), item, isFolder=True)

    items = [catalog_item(plugin, addon, catalog, show_type=hub == "more") for addon, catalog in catalogs]
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.endOfDirectory(handle)


@route("hub_genres")
def hub_genres_view(plugin, hub):
    handle = plugin.handle
    registry = get_registry()
    known = hub in registry.hubs()
    genres = hub_genres(registry.hub_catalogs(hub)) if known else {}
    xbmcplugin.setPluginCategory(handle, f"{hub_label(hub, registry)} / {L(30351)}" if known else "")
    items = []
    for genre, (addon, catalog) in genres.items():
        item = xbmcgui.ListItem(genre, label2=addon.display_name(catalog))
        item.setArt(tile_art("genre.png"))
        item.getVideoInfoTag().setPlot(addon.display_name(catalog))
        url = plugin.url_for("catalog", addon=addon.key, type=catalog.type, id=catalog.id,
                             **slot_params(addon, catalog), **{FILTER_PREFIX + "genre": genre})
        items.append((url, item, True))
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.endOfDirectory(handle)


# ------------------------------------------------------------------ organising

@route("organise")
def organise(plugin):
    """Settings > Addons > Organise catalogs: one folder per hub."""
    handle = plugin.handle
    registry = get_registry()
    xbmcplugin.setPluginCategory(handle, L(30340))
    for hub in registry.hubs():
        entries = registry.hub_entries(hub)
        if not entries and not registry.is_custom_hub(hub):  # a new hub of your own starts empty
            continue
        shown = sum(1 for _, _, visible in entries if visible)
        item = xbmcgui.ListItem(f"{hub_label(hub, registry)}  "
                                f"[COLOR FF999999]{L(30356, shown=shown, hidden=len(entries) - shown)}[/COLOR]")
        item.setArt(hub_art(hub, registry))
        add_context_menu(item, _hub_actions(plugin, registry, hub))
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("organise_hub", hub=hub), item, isFolder=True)
    item = xbmcgui.ListItem(L(30357))
    item.setArt(tile_art("hub_new.png"))
    xbmcplugin.addDirectoryItem(handle, plugin.url_for("hub_create"), item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def _hub_actions(plugin, registry, hub):
    actions = [(L(30358), plugin.run_url("hub_create")), (L(30359), plugin.run_url("hub_rename", hub=hub))]
    if registry.is_custom_hub(hub):
        actions.append((L(30360), plugin.run_url("hub_remove", hub=hub)))
    return actions


def _ask_new_hub(registry):
    """Ask for a name and create the hub; its id, or None if cancelled."""
    name = xbmcgui.Dialog().input(L(30361), type=xbmcgui.INPUT_ALPHANUM).strip()
    return registry.create_hub(name) if name else None


@route("hub_create")
def hub_create(plugin):
    if _ask_new_hub(get_registry()):
        refresh_container()


@route("hub_rename")
def hub_rename(plugin, hub):
    registry = get_registry()
    if hub not in registry.hubs():
        return
    custom = registry.is_custom_hub(hub)
    current = hub_label(hub, registry)
    name = xbmcgui.Dialog().input(L(30361 if custom else 30362), defaultt=current, type=xbmcgui.INPUT_ALPHANUM)
    if name.strip() == current or (custom and not name.strip()):
        return
    registry.rename_hub(hub, name)  # empty: a built-in hub gets its own name back
    refresh_container()


@route("hub_remove")
def hub_remove(plugin, hub):
    registry = get_registry()
    if not registry.is_custom_hub(hub):
        return
    count = len(registry.hub_entries(hub))
    if xbmcgui.Dialog().yesno(L(30360), L(30363, name=hub_label(hub, registry), count=count)):
        registry.remove_hub(hub)
        refresh_container()


def _actions(addon, catalog):
    prefs = addon.prefs_for(catalog)
    return [
        ("toggle", L(30348) if prefs.home else L(30347)),
        ("rename", L(30254)),
        ("up", L(30012)), ("down", L(30013)), ("top", L(30251)), ("bottom", L(30252)),
        ("hub", L(30344)),
        ("pin", L(30346) if prefs.front is True else L(30345)),
    ]


@route("organise_hub")
def organise_hub(plugin, hub):
    handle = plugin.handle
    registry = get_registry()
    xbmcplugin.setPluginCategory(handle, f"{L(30340)} / {hub_label(hub, registry)}")
    for position, (addon, catalog, shown) in enumerate(registry.hub_entries(hub), 1):
        prefs = addon.prefs_for(catalog)
        name = addon.display_name(catalog, tidy=True)
        flags = []
        if not shown:
            flags.append(L(30349))
        if prefs.front is True:
            flags.append(L(30350))
        detail = f"{addon.name} · {type_label(catalog.type)}" + "".join(f" · {f}" for f in flags)
        text = f"{position}. {name}  [COLOR FF999999]{detail}[/COLOR]"
        item = xbmcgui.ListItem(text if shown else f"[COLOR FF777777]{position}. {name}  {detail}[/COLOR]")
        ref = {"addon": addon.key, "catalog": catalog.key}
        add_context_menu(item, [(label, plugin.run_url("organise_do", do=do, **ref))
                                for do, label in _actions(addon, catalog)])
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("organise_actions", **ref), item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


@route("organise_actions")
def organise_actions(plugin, addon, catalog):
    """Remote-friendly menu shown when a catalog is selected in a hub."""
    installed, entry = _catalog(addon, catalog)
    if entry is None:
        return
    actions = _actions(installed, entry)
    choice = xbmcgui.Dialog().select(installed.display_name(entry, tidy=True), [label for _, label in actions])
    if choice >= 0:
        organise_do(plugin, addon, catalog, actions[choice][0])


@route("organise_do")
def organise_do(plugin, addon, catalog, do):
    registry = get_registry()
    installed, entry = _catalog(addon, catalog, registry)
    if entry is None:
        return
    prefs = installed.prefs_for(entry)
    key = registry.search_key(installed, entry)
    moves = {"up": -1, "down": 1, "top": -100000, "bottom": 100000}
    if do == "toggle":
        registry.set_catalog_pref(addon, catalog, home=not prefs.home)
    elif do == "pin":
        registry.set_catalog_pref(addon, catalog, front=prefs.front is not True)
    elif do in moves:
        registry.move_in_hub(key, moves[do])
    elif do == "rename":
        current = installed.display_name(entry, tidy=True)
        name = xbmcgui.Dialog().input(L(30352), defaultt=current, type=xbmcgui.INPUT_ALPHANUM)
        if name == current:
            return
        registry.set_catalog_pref(addon, catalog, name=name)  # empty: back to the addon's name
    elif do == "hub":
        hubs = [h for h in registry.hubs() if h != installed.hub_of(entry)]
        choice = xbmcgui.Dialog().select(L(30344), [hub_label(h, registry) for h in hubs] + [L(30357)])
        if choice < 0:
            return
        target = hubs[choice] if choice < len(hubs) else _ask_new_hub(registry)
        if not target:
            return
        registry.set_catalog_pref(addon, catalog, hub=target)
    else:
        return
    refresh_container()


def _catalog(addon, catalog, registry=None):
    try:
        installed = (registry or get_registry()).get(addon)
    except StremioError:
        return None, None
    return installed, next((c for c in installed.manifest.catalogs if c.key == catalog), None)
