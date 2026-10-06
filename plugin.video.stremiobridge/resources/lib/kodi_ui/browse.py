"""Browsing: type menu -> catalogs -> items, with filters and paging.

Every URL here only carries the addon key, type, catalog id, filters and skip,
so any of these folders can be used as a skin widget. Skin widgets and the
skin's hub rows page (and, in hubs, filter) in place: Next Page reloads the row
with the next page instead of opening it in the Videos window.
"""

import hashlib
import json
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

from .common import ADDON, L, get_client, get_registry, get_watchstate, log, notify, notify_widgets
from .listitems import content_for, preview_items
from .router import route
from .views import end_listing, set_content

TYPE_LABELS = {"movie": 30040, "series": 30041, "channel": 30042, "tv": 30043, "all": 30044}
FILTER_PREFIX = "f_"
# Re-fetch a manifest on demand (catalog missing, or a numbered one empty) at most this often.
ON_DEMAND_REFRESH_AGE = 600

# A row (skin widget or hub row) paged or filtered in place: home property
# ROW_STATE + row_key(...) holds {"pages": [[skip, page size], ...] (the pages
# after the first, in order), "filters": {name: value}, "first": position of
# its first title, "reloads": its path has the widgets' reload token}.
ROW_STATE = "stremiobridge.row."
# Arctic Zephyr Stremio's hub windows (Custom_Hub_11xx.xml)
HUB_WINDOWS = " | ".join(f"Window.IsActive({n})" for n in range(1111, 1120))

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
    in_videos = browsing_in_videos_window()
    # A widget or hub row shows the page and filters it was last moved to.
    key = row_key(addon, type, id, only) if not in_videos and skip == 0 else None
    row = row_state(key) if key else None
    if row:
        filters.update(row["filters"])
        if row["pages"]:
            skip, page_size = row["pages"][-1]
    in_hub = key is not None and browsing_in_hub()

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

    # Optional filter (e.g. genre) when browsing in the Videos window or a hub
    # row; never on home-screen widgets, which always show the catalog as it is.
    leading = 0
    if "search" not in filters and ((in_videos and skip == 0) or in_hub) and ADDON.getSettingBool("genre_filter"):
        for extra in catalog.filters:
            current = filters.get(extra.name) or (effective.get(extra.name) if extra.is_required else "")
            label = L(30295, name=extra.name.capitalize())
            if current and current.lower() != "none":
                label += f"  [COLOR FF999999]· {current}[/COLOR]"
            item = xbmcgui.ListItem(label)
            item.setArt(tile_art("filter_genre.png"))
            item.setProperty("SpecialSort", "top")
            if in_hub:
                item.setProperty("IsPlayable", "true")  # see row_page
            target = plugin.url_for("choose_filter", addon=addon, type=type, id=catalog.id, name=extra.name,
                                    only=only, row=key if in_hub else None,
                                    **{FILTER_PREFIX + k: v for k, v in filters.items()})
            xbmcplugin.addDirectoryItem(handle, target, item, isFolder=False)
            leading += 1
    if row is not None:
        leading += row_start(plugin, key, row, leading, addon=addon, type=type, id=id, only=only)

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
        if key:
            add_next_page(plugin, addon=addon, type=type, id=id, only=only, skip=next_skip, ps=page_size,
                          **slot_params(installed, catalog))
        else:
            add_next_page(plugin, url=catalog_url(skip=next_skip, ps=page_size))

    set_content(handle, content_for(only or catalog.type))
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle)
    if in_videos and skip:
        from .views import focus_item

        focus_item(0, path=catalog_url(skip=skip, ps=page_size))  # a later page: start at its first title


def add_next_page(plugin, url=None, **row):
    """The Next Page tile: opening `url`, or moving a row (`row`: row_page's
    parameters) on a page."""
    item = xbmcgui.ListItem(L(30050))
    item.setArt(tile_art("next_page.png"))
    item.setProperty("SpecialSort", "bottom")
    if url:
        xbmcplugin.addDirectoryItem(plugin.handle, url, item, isFolder=True)
    else:
        item.setProperty("IsPlayable", "true")  # see row_page
        xbmcplugin.addDirectoryItem(plugin.handle, plugin.url_for("row_page", **row), item, isFolder=False)


def row_start(plugin, key, row, leading, **params):
    """For a row past its first page, the Previous Page tile (`params`: which
    row, for row_page). Records where the row's first title is (after the
    `leading` tiles before it and this one) and whether it can reload in place.
    Returns the number of tiles added."""
    added = 0
    if row["pages"]:
        item = xbmcgui.ListItem(L(30413))
        item.setArt(tile_art("previous_page.png"))
        item.setProperty("SpecialSort", "top")
        item.setProperty("IsPlayable", "true")  # see row_page
        xbmcplugin.addDirectoryItem(plugin.handle, plugin.url_for("row_page", back=1, **params), item,
                                    isFolder=False)
        added = 1
    reloads = "reload" in plugin.names  # the widgets' reload token, empty until first used
    if (row["first"], row["reloads"]) != (leading + added, reloads):
        row.update(first=leading + added, reloads=reloads)
        save_row_state(key, row)
    return added


def row_key(addon, type_, id_, only=None):
    """Names one catalog's row state (see ROW_STATE)."""
    return hashlib.md5(f"{addon}|{type_}|{id_}|{only or ''}".encode()).hexdigest()[:12]


def row_state(key):
    try:
        state = json.loads(xbmcgui.Window(10000).getProperty(ROW_STATE + key) or "{}")
    except ValueError:
        state = {}
    return {"pages": [tuple(p) for p in state.get("pages", [])], "filters": state.get("filters", {}),
            "first": state.get("first", 0), "reloads": state.get("reloads", False)}


def save_row_state(key, state):
    xbmcgui.Window(10000).setProperty(ROW_STATE + key, json.dumps(state))


def reload_row(control, key, wait=10.0):
    """Reload the widgets (the row's path ends in the reload token) and, once
    row `control` has its new items, focus its first title."""
    monitor = xbmc.Monitor()
    notify_widgets()
    updating = f"Container({control}).IsUpdating"
    started = waited = 0.0
    while waited < wait:  # wait for the reload to start, then to end
        if monitor.waitForAbort(0.1):
            return
        waited += 0.1
        if xbmc.getCondVisibility(updating):
            started = waited
        elif started or waited > 1.5:
            break
    xbmc.executebuiltin(f"SetFocus({control},{row_state(key)['first']},absolute)")


def _answer(plugin):
    """Kodi "plays" a row's tiles and waits: tell it there's nothing to play."""
    if plugin.handle >= 0:
        xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())


def _row_control(state, wait=3.0):
    """The focused row's control id, if the row can reload in place (its path
    carries the widgets' reload token). Kodi's busy dialog hides it for a
    moment after we answer."""
    if not state["reloads"]:
        return None
    monitor = xbmc.Monitor()
    for _ in range(int(wait / 0.1)):
        control = xbmc.getInfoLabel("System.CurrentControlId")
        if control:
            return control
        if monitor.waitForAbort(0.1):
            break
    return None


@route("row_page")
def row_page(plugin, addon=None, type=None, id=None, only=None, skip=None, ps=None, back=None, slot=None,
             of=None, list=None):
    """Next Page (`skip`, `ps`) or Previous Page (`back`) in a widget or hub
    row of a catalog, or of `list` "continue" (Continue Watching; `skip` is
    then the page number): the row reloads on that page, focused on its first
    title. A row that can't reload opens the page in the Videos window
    instead. The tiles are "playable": Kodi opens anything else from a widget
    in the Videos window."""
    _answer(plugin)
    key = row_key(list, "", "") if list else row_key(addon, type, id, only)
    state = row_state(key)
    control = _row_control(state)
    if control is None:
        if list and skip is not None:
            xbmc.executebuiltin(f'ActivateWindow(Videos,"{plugin.url_for(list, page=skip)}",return)')
        elif skip is not None:
            filters = {FILTER_PREFIX + k: v for k, v in state["filters"].items()}
            url = plugin.url_for("catalog", addon=addon, type=type, id=id, only=only, skip=skip, ps=ps,
                                 slot=slot, of=of, **filters)
            xbmc.executebuiltin(f'ActivateWindow(Videos,"{url}",return)')
        return
    if back:
        state["pages"] = state["pages"][:-1]
    elif skip is not None:
        state["pages"] = state["pages"] + [(int(skip), int(ps) if ps else None)]
    save_row_state(key, state)
    reload_row(control, key)


def tile_art(name):
    """Art for our own poster-shaped tiles (resources/media)."""
    path = os.path.join(ADDON.getAddonInfo("path"), "resources", "media", name)
    return {"poster": path, "thumb": path, "icon": path}


def browsing_in_videos_window():
    """True when the user is browsing in the Videos window (not a skin widget
    loading on another window)."""
    return xbmc.getCondVisibility("Window.IsActive(videos)")


def browsing_in_hub():
    """True when the list is loading in one of Arctic Zephyr Stremio's hubs."""
    return xbmc.getCondVisibility(HUB_WINDOWS)


@route("choose_filter")
def choose_filter(plugin, addon, type, id, name, only=None, row=None, **params):
    """Pick a value for one of a catalog's filters (e.g. genre), then show the
    catalog with it. "All" (optional filters only) shows it unfiltered. In a
    hub row (`row`: its row_key), the row reloads with it, on its first page."""
    if row:
        _answer(plugin)
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
    state = row_state(row) if row else None
    control = _row_control(state) if row else None
    if control is not None:
        state.update(pages=[], filters=filters)
        save_row_state(row, state)
        return reload_row(control, row)
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
