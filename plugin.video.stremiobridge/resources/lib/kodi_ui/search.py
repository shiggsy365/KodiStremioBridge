"""Search across every catalog marked "use for search", plus search history.

``?action=search&query=…[&type=movie]`` is stable, so skins can call it too.
"""

import xbmcgui
import xbmcplugin

from stremio.catalog import search as run_search
from stremio.meta import cinemeta_search_targets

from .browse import FILTER_PREFIX, type_label
from .common import (
    ADDON, L, add_context_menu, get_client, get_history, get_registry, get_watchstate, log, notify,
    refresh_container, run_with_progress,
)
from .listitems import content_for, preview_items
from .router import route
from .views import end_listing, set_content


@route("search_menu")
def search_menu(plugin):
    handle = plugin.handle
    xbmcplugin.setPluginCategory(handle, L(30070))

    item = xbmcgui.ListItem(f"[B]{L(30071)}[/B]")
    item.setArt({"icon": "DefaultAddonsSearch.png"})
    xbmcplugin.addDirectoryItem(handle, plugin.url_for("new_search"), item, isFolder=False)

    item = xbmcgui.ListItem(L(30250))
    item.setArt({"icon": "DefaultAddonProgram.png"})
    item.setProperty("SpecialSort", "bottom")
    xbmcplugin.addDirectoryItem(handle, plugin.url_for("search_catalogs"), item, isFolder=True)

    for query in get_history().all():
        item = xbmcgui.ListItem(query)
        item.setArt({"icon": "DefaultAddonsSearch.png"})
        add_context_menu(item, [
            (L(30076), plugin.run_url("search_history_remove", query=query)),
            (L(30077), plugin.run_url("search_history_clear")),
        ])
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("search_window", query=query), item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


@route("new_search")
def new_search(plugin, type=None):
    """Ask for a query, remember it, then show the results window."""
    query = xbmcgui.Dialog().input(L(30072), type=xbmcgui.INPUT_ALPHANUM).strip()
    if not query:
        return
    get_history().add(query)
    from .searchwindow import search_window  # searchwindow imports this module

    search_window(plugin, query=query, type=type)


def find_results(query, type_=None, person=False):
    """Search every enabled search catalog (in the user's order), falling back
    to Cinemeta if none of them finds anything. Returns ``(groups, fallback)``:
    non-empty ``(addon, catalog, previews)`` results, and whether they came
    from the fallback. Tells the user when there's nothing (groups empty).

    `person`: the query is someone's name (actor/director). People-search
    catalogs are used if there are any; otherwise the normal ones plus
    Cinemeta, whose search matches cast and crew (title searches mostly don't)."""
    targets = get_registry().search_catalogs(type_)
    use_cinemeta = ADDON.getSettingBool("cinemeta_fallback")
    if person:
        people = [(a, c) for a, c in targets if c.is_people_search]
        if people:
            targets = people
        elif use_cinemeta:
            targets = targets + cinemeta_search_targets(type_)
    if not targets and not use_cinemeta:
        notify(L(30078), icon=xbmcgui.NOTIFICATION_WARNING)
        return [], False
    client = get_client()
    groups, cancelled = _search(client, targets, query, type_)
    fallback = False
    if not groups and not cancelled and use_cinemeta:
        # None of the user's search catalogs found anything (some addons' search
        # returns nothing at all): try Cinemeta, whose search also matches people.
        groups, cancelled = _search(client, cinemeta_search_targets(type_), query, type_)
        fallback = bool(groups)
    if not groups and not cancelled:
        notify(L(30073, query=query))
    return (groups if not cancelled else []), fallback


@route("search")
def search(plugin, query, type=None):
    """Search results as a folder (for skins/widgets); the menus use the
    results window instead (see searchwindow)."""
    handle = plugin.handle
    groups, fallback = find_results(query, type)
    if not groups:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return

    title = f"{L(30070)}: {query}"
    xbmcplugin.setPluginCategory(handle, title)
    if len(groups) == 1 or fallback:
        # One catalog found anything, or Cinemeta stood in (not an installed
        # addon, so no catalog folders to open): list the items straight away.
        previews = [p for _, _, found in groups for p in found]
        items = preview_items(plugin, previews, get_watchstate())
        xbmcplugin.addDirectoryItems(handle, items, len(items))
        types = {p.type for p in previews}
        set_content(handle, content_for(type or (types.pop() if len(types) == 1 else "")))
    else:
        for addon, catalog, previews in groups:
            label = (f"{type_label(catalog.type)} · {addon.search_title(catalog)}  "
                     f"[COLOR grey]{addon.name} ({len(previews)})[/COLOR]")
            item = xbmcgui.ListItem(label)
            art = previews[0].poster or addon.manifest.logo
            if art:
                item.setArt({"icon": art, "thumb": art, "poster": art})
            url = plugin.url_for("catalog", addon=addon.key, type=catalog.type, id=catalog.id,
                                 only=type if catalog.type == "all" else None,
                                 **{FILTER_PREFIX + "search": query})
            xbmcplugin.addDirectoryItem(handle, url, item, isFolder=True)
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle)


def _search(client, targets, query, type_):
    """``(groups, cancelled)``: non-empty ``(addon, catalog, previews)`` results."""
    if not targets:
        return [], False
    results, errors, cancelled = run_with_progress(
        L(30074), 30075, lambda progress: run_search(client, targets, query, progress)
    )
    for label, exc in errors:
        log(f"Search in {label} failed: {exc}")
    if type_:
        # Catalogs of type "all" return every type; keep only the one asked for.
        results = [(a, c, [p for p in previews if p.type == type_]) for a, c, previews in results]
    return [(addon, catalog, previews) for addon, catalog, previews in results if previews], cancelled


# ------------------------------------------------------------------ search catalog manager

@route("search_catalogs")
def search_catalogs(plugin):
    """Every searchable catalog in search order: enable/disable and reorder."""
    handle = plugin.handle
    xbmcplugin.setPluginCategory(handle, L(30250))
    for position, (addon, catalog, enabled) in enumerate(get_registry().search_entries(), 1):
        check = "[COLOR lime]✔[/COLOR]" if enabled else "[COLOR grey]✘[/COLOR]"
        title = addon.search_title(catalog)
        renamed = f"  [COLOR FF999999]({catalog.name})[/COLOR]" if title != catalog.name else ""
        name = f"{position}. {type_label(catalog.type)} · {title}{renamed}"
        label = f"{check}  {name}  [COLOR grey]{addon.name}[/COLOR]"
        item = xbmcgui.ListItem(label if enabled else f"{check}  [COLOR grey]{name}  {addon.name}[/COLOR]")
        if addon.manifest.logo:
            item.setArt({"icon": addon.manifest.logo, "thumb": addon.manifest.logo})
        ref = {"addon": addon.key, "catalog": catalog.key}
        add_context_menu(item, [
            (L(30011) if not enabled else L(30010), plugin.run_url("toggle_search_catalog", **ref)),
            (L(30254), plugin.run_url("rename_search_catalog", **ref)),
            (L(30012), plugin.run_url("move_search_catalog", delta=-1, **ref)),
            (L(30013), plugin.run_url("move_search_catalog", delta=1, **ref)),
            (L(30251), plugin.run_url("move_search_catalog", delta=-10000, **ref)),
            (L(30252), plugin.run_url("move_search_catalog", delta=10000, **ref)),
        ])
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("search_catalog_actions", **ref), item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


@route("search_catalog_actions")
def search_catalog_actions(plugin, addon, catalog):
    """Remote-friendly menu shown when a search catalog is selected."""
    registry = get_registry()
    installed = registry.get(addon)
    enabled = installed.prefs_for(next(c for c in installed.manifest.catalogs if c.key == catalog)).search
    actions = [(L(30011) if not enabled else L(30010), None), (L(30254), "rename"), (L(30012), -1),
               (L(30013), 1), (L(30251), -10000), (L(30252), 10000)]
    choice = xbmcgui.Dialog().select(installed.name, [label for label, _ in actions])
    if choice < 0:
        return
    if actions[choice][1] is None:
        toggle_search_catalog(plugin, addon, catalog)
    elif actions[choice][1] == "rename":
        rename_search_catalog(plugin, addon, catalog)
    else:
        move_search_catalog(plugin, addon, catalog, actions[choice][1])


@route("toggle_search_catalog")
def toggle_search_catalog(plugin, addon, catalog):
    registry = get_registry()
    installed = registry.get(addon)
    current = installed.prefs_for(next(c for c in installed.manifest.catalogs if c.key == catalog)).search
    registry.set_catalog_pref(addon, catalog, search=not current)
    refresh_container()


@route("rename_search_catalog")
def rename_search_catalog(plugin, addon, catalog):
    registry = get_registry()
    installed = registry.get(addon)
    current = installed.search_title(next(c for c in installed.manifest.catalogs if c.key == catalog))
    name = xbmcgui.Dialog().input(L(30255), defaultt=current, type=xbmcgui.INPUT_ALPHANUM)
    if name == current:
        return
    registry.set_catalog_pref(addon, catalog, search_name=name)  # empty: back to the addon's name
    refresh_container()


@route("move_search_catalog")
def move_search_catalog(plugin, addon, catalog, delta):
    registry = get_registry()
    registry.move_search_catalog(f"{registry.get(addon).key}|{catalog}", int(delta))
    refresh_container()


@route("search_history_remove")
def search_history_remove(plugin, query):
    get_history().remove(query)
    refresh_container()


@route("search_history_clear")
def search_history_clear(plugin):
    get_history().clear()
    refresh_container()
