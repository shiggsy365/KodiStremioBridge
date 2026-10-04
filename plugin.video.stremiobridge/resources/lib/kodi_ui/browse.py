"""Browsing: type menu -> catalogs -> items, with filters and paging.

Every URL here only carries the addon key, type, catalog id, filters and skip,
so any of these folders can be used as a skin widget.
"""

import os
import time

import xbmc
import xbmcgui
import xbmcplugin

from stremio import StremioError
from stremio.catalog import catalog_family, catalog_slot, fetch_catalog, next_page, resolve_catalog, with_default_filters
from stremio.meta import CINEMETA_KEY, CINEMETA_URL, cinemeta_addon
from stremio.refresh import refresh_manifests
from stremio.streaming import STREAMING_KEY, STREAMING_URL, streaming_addon

from .common import ADDON, L, get_client, get_registry, get_watchstate, log, notify
from .listitems import content_for, preview_items
from .router import route
from .views import end_listing, set_content

TYPE_LABELS = {"movie": 30040, "series": 30041, "channel": 30042, "tv": 30043, "all": 30044}
FILTER_PREFIX = "f_"
# Re-fetch a manifest on demand (catalog missing, or a numbered one empty) at most this often.
ON_DEMAND_REFRESH_AGE = 600

# Plugin URLs can name these by key: Cinemeta (Arctic Zephyr Stremio's widgets)
# and Streaming Catalogs (its info page), installed or not.
BUILT_IN = {CINEMETA_KEY: cinemeta_addon, STREAMING_KEY: streaming_addon}


def type_label(type_):
    """Known types are translated; others are tidied up ("anime.movie" -> "Anime Movie")."""
    string_id = TYPE_LABELS.get(type_)
    if string_id:
        return L(string_id)
    words = type_.replace(".", " ").replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or type_


def catalog_item(plugin, addon, catalog, show_type=False):
    """A folder entry for a catalog, as used by type menus and the front page."""
    detail = f"{type_label(catalog.type)} · {addon.name}" if show_type else addon.name
    name = addon.display_name(catalog, tidy=ADDON.getSettingBool("tidy_names"))
    item = xbmcgui.ListItem(f"{name}  [COLOR grey]{detail}[/COLOR]")
    if addon.manifest.logo:
        item.setArt({"icon": addon.manifest.logo, "thumb": addon.manifest.logo})
    return plugin.url_for("catalog", addon=addon.key, type=catalog.type, id=catalog.id,
                          **slot_params(addon, catalog)), item, True


def slot_params(addon, catalog):
    """For numbered catalogs (e.g. BingeCat's rotating "because you watched"
    rows), remember the position in the family so links survive rotation."""
    slot = catalog_slot(addon, catalog)
    return {"slot": slot[0], "of": slot[1]} if slot else {}


@route("type")
def type_menu(plugin, type):
    handle = plugin.handle
    xbmcplugin.setPluginCategory(handle, type_label(type))
    registry = get_registry()
    if registry.search_catalogs(type):
        item = xbmcgui.ListItem(f"[B]{L(30079, type=type_label(type))}[/B]")
        item.setArt({"icon": "DefaultAddonsSearch.png"})
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("new_search", type=type), item, isFolder=False)

    items = [catalog_item(plugin, addon, catalog) for addon, catalog in registry.home_catalogs(type)]
    xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.endOfDirectory(handle)


@route("catalog")
def catalog_view(plugin, addon, type, id, skip="0", ps=None, only=None, slot=None, of=None, **params):
    """`only` keeps items of one type (for catalogs of type "all"); `slot`/`of`
    locate a rotating catalog whose id has changed (see `slot_params`)."""
    handle = plugin.handle
    filters = {k[len(FILTER_PREFIX):]: v for k, v in params.items() if k.startswith(FILTER_PREFIX) and v}
    skip, page_size = int(skip), int(ps) if ps else None

    registry = get_registry()
    try:
        installed = registry.get(addon)
    except StremioError:
        if addon not in BUILT_IN:
            return _fail(handle, L(30052, name=id))
        installed = BUILT_IN[addon]()  # lists that work without the addon being installed
    slot, of = (int(slot), int(of)) if slot is not None and of is not None else (None, None)

    def resolve():
        return resolve_catalog(installed, type, id, slot, of)

    catalog = resolve()
    if catalog is None and addon not in BUILT_IN and _refresh_if_due(registry, installed):
        installed = registry.get(addon)
        catalog = resolve()
    if catalog is None:
        return _fail(handle, L(30052, name=f"{installed.name} / {id}"))

    def catalog_url(**changes):
        state = {FILTER_PREFIX + k: v for k, v in filters.items()}
        state["only"] = only
        state.update(changes)
        return plugin.url_for("catalog", addon=addon, type=type, id=catalog.id,
                              **slot_params(installed, catalog), **state)

    title = installed.display_name(catalog, tidy=ADDON.getSettingBool("tidy_names")) + "".join(
        f" / {v}" for v in filters.values())
    xbmcplugin.setPluginCategory(handle, title)

    # Required filters (e.g. genre) use their first option, like Stremio; there is
    # no picker. `f_<name>` URL params still override them, e.g. from a skin widget.
    effective = with_default_filters(catalog, filters)
    client = get_client()
    try:
        previews = fetch_catalog(client, installed, catalog, effective, skip)
        # An empty numbered catalog may have rotated away since the manifest was
        # fetched (addons answer old ids with nothing): refresh and look again.
        if not previews and skip == 0 and catalog_family(catalog.id):
            old_slot = catalog_slot(installed, catalog) or (None, None)
            if _refresh_if_due(registry, installed):
                installed = registry.get(addon)
                replacement = resolve_catalog(installed, type, catalog.id, *old_slot)
                if replacement is not None and replacement.id != catalog.id:
                    catalog = replacement
                    effective = with_default_filters(catalog, filters)
                    previews = fetch_catalog(client, installed, catalog, effective, skip)
    except StremioError as exc:
        log(f"Catalog {installed.name} {catalog.key} failed: {exc}")
        return _fail(handle, L(30052, name=f"{installed.name} / {catalog.name}"))

    # Optional filter (e.g. genre) when browsing in the Videos window; never on
    # home-screen widgets, which always show the catalog as it is.
    if skip == 0 and "search" not in filters and browsing_in_videos_window() \
            and ADDON.getSettingBool("genre_filter"):
        for extra in catalog.filters:
            current = filters.get(extra.name) or (effective.get(extra.name) if extra.is_required else "")
            label = L(30295, name=extra.name.capitalize())
            if current and current.lower() != "none":
                label += f"  [COLOR FF999999]· {current}[/COLOR]"
            item = xbmcgui.ListItem(label)
            item.setArt(tile_art("filter_genre.png"))
            item.setProperty("SpecialSort", "top")
            target = plugin.url_for("choose_filter", addon=addon, type=type, id=catalog.id, name=extra.name,
                                    only=only, **{FILTER_PREFIX + k: v for k, v in filters.items()})
            xbmcplugin.addDirectoryItem(handle, target, item, isFolder=False)

    shown = [p for p in previews if p.type == only] if only else previews
    if installed.transport_url in (CINEMETA_URL, STREAMING_URL):
        from .watching import preferred_previews  # watching imports listitems, as does this

        shown = preferred_previews(shown)
    items = preview_items(plugin, shown, get_watchstate(), hide_watched=ADDON.getSettingBool("hide_watched"))
    xbmcplugin.addDirectoryItems(handle, items, len(items))

    # Paging looks at what the addon returned, not what `only` kept.
    following = next_page(catalog, skip, len(previews), page_size)
    if following:
        next_skip, page_size = following
        item = xbmcgui.ListItem(L(30050))
        item.setArt(tile_art("next_page.png"))
        item.setProperty("SpecialSort", "bottom")
        xbmcplugin.addDirectoryItem(handle, catalog_url(skip=next_skip, ps=page_size), item, isFolder=True)

    set_content(handle, content_for(only or catalog.type))
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle)


def tile_art(name):
    """Art for our own poster-shaped tiles (resources/media)."""
    path = os.path.join(ADDON.getAddonInfo("path"), "resources", "media", name)
    return {"poster": path, "thumb": path, "icon": path}


def browsing_in_videos_window():
    """True when the user is browsing in the Videos window (not a skin widget
    loading on another window)."""
    return xbmc.getCondVisibility("Window.IsActive(videos)")


@route("choose_filter")
def choose_filter(plugin, addon, type, id, name, only=None, **params):
    """Pick a value for one of a catalog's filters (e.g. genre), then show the
    catalog with it. "All" (optional filters only) shows it unfiltered."""
    filters = {k[len(FILTER_PREFIX):]: v for k, v in params.items() if k.startswith(FILTER_PREFIX) and v}
    try:
        installed = get_registry().get(addon)
    except StremioError:
        return
    catalog = resolve_catalog(installed, type, id)
    extra = catalog.extra_prop(name) if catalog else None
    if extra is None or not extra.options:
        return
    values = [o for o in extra.options if o.lower() != "none"]
    labels = values if extra.is_required else [L(30296)] + values
    current = filters.get(name, "")
    preselect = labels.index(current) if current in labels else 0
    choice = xbmcgui.Dialog().select(name.capitalize(), labels, preselect=preselect)
    if choice < 0:
        return
    if extra.is_required or choice > 0:
        filters[name] = labels[choice]
    else:
        filters.pop(name, None)
    url = plugin.url_for("catalog", addon=installed.key, type=type, id=catalog.id, only=only,
                         **slot_params(installed, catalog), **{FILTER_PREFIX + k: v for k, v in filters.items()})
    xbmc.executebuiltin(f"Container.Update({url})")


def _refresh_if_due(registry, installed):
    """Re-fetch one addon's manifest now, unless that happened very recently."""
    if time.time() - installed.fetched_at < ON_DEMAND_REFRESH_AGE:
        return False
    refreshed, errors = refresh_manifests(get_client(), registry, [installed])
    for name, exc in errors:
        log(f"On-demand manifest refresh for {name} failed: {exc}")
    return bool(refreshed)


def _fail(handle, message):
    notify(message, icon=xbmcgui.NOTIFICATION_ERROR)
    xbmcplugin.endOfDirectory(handle, succeeded=False)
