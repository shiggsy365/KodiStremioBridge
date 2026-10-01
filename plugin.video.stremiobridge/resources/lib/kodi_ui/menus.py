"""Top-level menus."""

import xbmc
import xbmcgui
import xbmcplugin

from stremio.refresh import DAY, refresh_manifests

from .browse import catalog_item, tile_art
from .hubs import hub_item
from .common import (
    ADDON, L, WIDGETS_RELOAD, get_cache, get_client, get_mdblist, get_registry, get_watchstate, jsonrpc, log,
    notify,
)
from .router import route



@route("root")
def root(plugin):
    handle = plugin.handle
    registry = get_registry()
    if ADDON.getSettingBool("auto_refresh_manifests"):
        _refresh_stale_manifests(registry)

    state = get_watchstate()
    personal = []
    if ADDON.getSettingBool("show_continue") and state.continue_watching(1):
        personal.append((L(30180), "continue", "continue.png"))
    if ADDON.getSettingBool("show_next_up") and state.recent_shows(1):
        personal.append((L(30181), "next_up", "next_up.png"))
    if ADDON.getSettingBool("show_watchlist") and get_mdblist() is not None:
        personal.append((L(30300), "watchlist", "watchlist.png"))
    for label, action, art in personal:
        item = xbmcgui.ListItem(label)
        item.setArt(tile_art(art))
        xbmcplugin.addDirectoryItem(handle, plugin.url_for(action), item, isFolder=True)

    # Catalogs the user pinned, then the hubs that have something in them.
    pinned = [catalog_item(plugin, addon, catalog, show_type=True) for addon, catalog in registry.pinned_catalogs()]
    xbmcplugin.addDirectoryItems(handle, pinned, len(pinned))
    hubs = [hub_item(plugin, hub, registry) for hub in registry.hubs_in_use()]
    xbmcplugin.addDirectoryItems(handle, hubs, len(hubs))

    if not registry.all():
        hint = xbmcgui.ListItem(f"[I]{L(30004)}[/I]")
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("manage"), hint, isFolder=True)

    if registry.search_catalogs():
        item = xbmcgui.ListItem(L(30070))
        item.setArt(tile_art("search.png"))
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("search_menu"), item, isFolder=True)

    if ADDON.getSettingBool("show_widgets_folder"):
        item = xbmcgui.ListItem(L(30322))
        item.setArt(tile_art("widgets.png"))
        xbmcplugin.addDirectoryItem(handle, plugin.url_for("widgets"), item, isFolder=True)

    # Manage addons lives in the add-on's settings; with no addons yet, the hint above leads there.
    xbmcplugin.endOfDirectory(handle)


@route("widgets")
def widgets(plugin):
    """Ready-made widget sources: pick these in your skin's widget browser.
    Each path ends with the reload token, so the widget refreshes after you
    watch something (the add-on ignores the parameter)."""
    handle = plugin.handle
    reload_token = f"&reload=$INFO[Window(Home).Property({WIDGETS_RELOAD})]"
    entries = [(L(30180), plugin.url_for("continue"), "DefaultInProgressShows.png"),
               (L(30181), plugin.url_for("next_up"), "DefaultRecentlyAddedEpisodes.png")]
    if get_mdblist() is not None:
        entries.append((L(30300), plugin.url_for("watchlist"), "DefaultVideoPlaylists.png"))
    for label, url, icon in entries:
        item = xbmcgui.ListItem(label)
        item.setArt({"icon": icon})
        xbmcplugin.addDirectoryItem(handle, url + reload_token, item, isFolder=True)
    registry = get_registry()
    for hub in registry.hubs():
        for addon, catalog in registry.hub_catalogs(hub):
            url, item, folder = catalog_item(plugin, addon, catalog, show_type=True)
            xbmcplugin.addDirectoryItem(handle, url + reload_token, item, isFolder=True)
    xbmcplugin.setPluginCategory(handle, L(30322))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


@route("settings")
def settings(plugin):
    ADDON.openSettings()


OPENABLE = ("organise", "manage", "search_catalogs")  # folders opened from settings buttons


@route("open")
def open_folder(plugin, target):
    """Settings buttons open our folders through here: Kodi refuses
    ActivateWindow while the settings dialog is still closing, so wait for
    it (and any other modal dialog) to go first."""
    if target not in OPENABLE:
        return
    monitor = xbmc.Monitor()
    for _ in range(50):  # up to 5 s
        if not xbmc.getCondVisibility("System.HasActiveModalDialog") or monitor.abortRequested():
            break
        monitor.waitForAbort(0.1)
    xbmc.executebuiltin(f"ActivateWindow(Videos,{plugin.url_for(target)},return)")


KODI_PARENT_ITEMS = "filelists.showparentdiritems"


@route("toggle_parent_items")
def toggle_parent_items(plugin):
    """Kodi's own (global) "Show parent folder items" setting, from our settings
    for convenience: it applies to every list in Kodi, not only this add-on's."""
    shown = jsonrpc("Settings.GetSettingValue", setting=KODI_PARENT_ITEMS).get("value", True)
    if xbmcgui.Dialog().yesno(L(30310), L(30312 if shown else 30313)):
        jsonrpc("Settings.SetSettingValue", setting=KODI_PARENT_ITEMS, value=not shown)
        notify(L(30314 if shown else 30315))


@route("clear_cache")
def clear_cache(plugin):
    get_cache().clear()
    notify(L(30124))


def _refresh_stale_manifests(registry):
    """Pick up catalogs added or removed in an addon's own configuration, at
    most once a day per addon. Unreachable addons keep their current manifest."""
    stale = registry.stale_addons(DAY)
    if not stale:
        return
    refreshed, errors = refresh_manifests(get_client(), registry, stale)
    log(f"Refreshed {len(refreshed)} of {len(stale)} addon manifests")
    for name, exc in errors:
        log(f"Manifest refresh for {name} failed: {exc}")
