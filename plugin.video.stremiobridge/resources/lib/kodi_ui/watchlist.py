"""Your MDBList watchlist in Kodi: a Watchlist list (front page / widget),
Add to / Remove from watchlist, and, with "Add my MDBList watchlist to the
library", the matching library change straight away."""

import xbmcgui
import xbmcplugin

from mdblist import MDBListError, watchlist_previews
from stremio.library import WATCHLIST
from stremio.models import MetaPreview, external_ids

from .common import (
    ADDON, L, busy, get_cache, get_library, get_mdblist, get_watchstate, log, notify, notify_widgets,
    refresh_container,
)
from .details import load_meta
from .library import add_title, clean, scan, sources_configured
from .listitems import preview_items
from .router import route
from .views import end_listing, set_content

WATCHLIST_TYPES = ("movie", "series")
CACHE_KEY = "mdblist:watchlist"
CACHE_SECONDS = 600  # membership for menus; changes made here update it at once


def watchlist_items(client=None, refresh=False):
    """The watchlist (cached briefly), or None without MDBList / on error."""
    client = client or get_mdblist()
    if client is None:
        return None
    cache = get_cache()
    cached = None if refresh else cache.get(CACHE_KEY)
    if cached:
        return cached[0]
    try:
        items = client.watchlist()
    except MDBListError as exc:
        log(f"MDBList watchlist unavailable: {exc}")
        stale = cache.get(CACHE_KEY, allow_stale=True)
        return stale[0] if stale else None
    cache.set(CACHE_KEY, items, CACHE_SECONDS)
    return items


_membership = {}


def on_watchlist(type_, id_):
    """For menus: is this title on the watchlist? None if MDBList isn't set up."""
    if "keys" not in _membership:
        items = watchlist_items() if get_mdblist() is not None else None
        _membership["keys"] = None if items is None else {(i["type"], i["id"]) for i in items}
    keys = _membership["keys"]
    return None if keys is None else (("movie" if type_ == "movie" else "series"), id_) in keys


def watchlist_menu(plugin, type_, id_):
    """Context-menu entry: Add to / Remove from watchlist (MDBList users only)."""
    if type_ not in WATCHLIST_TYPES:
        return []
    member = on_watchlist(type_, id_)
    if member is None:
        return []
    action, label = ("watchlist_remove", L(30302)) if member else ("watchlist_add", L(30301))
    return [(label, plugin.run_url(action, type=type_, id=id_))]


def watchlist_metas(items):
    """The watchlist's titles with their details from the meta addon (backgrounds,
    logos, genres, ratings: MDBList gives only the title, year and poster)."""
    from .watching import preferred_previews  # watching imports more than the menus here need

    previews = [p for p in (MetaPreview.from_dict(e) for e in watchlist_previews(items)) if p]
    return preferred_previews(previews, always=True)


@route("watchlist")
def watchlist_view(plugin):
    handle = plugin.handle
    items = watchlist_items()
    if items is None:
        notify(L(30212), icon=xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    previews = watchlist_metas(items)
    xbmcplugin.setPluginCategory(handle, L(30300))
    listed = preview_items(plugin, previews, get_watchstate())
    xbmcplugin.addDirectoryItems(handle, listed, len(listed))
    types = {p.type for p in previews}
    set_content(handle, "movies" if types == {"movie"} else "tvshows" if types == {"series"} else "videos")
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    end_listing(handle, cacheToDisc=False)


def change_watchlist(type_, id_, add):
    """Add/remove on MDBList (and the library, if it follows the watchlist).
    True if it worked."""
    client = get_mdblist()
    if client is None:
        notify(L(30212), icon=xbmcgui.NOTIFICATION_WARNING)
        return False
    with busy():
        meta = load_meta(type_, id_, quiet=True)
        ids = dict(meta.external_ids) if meta else external_ids(id_)
        try:
            (client.watchlist_add if add else client.watchlist_remove)(type_, ids)
        except MDBListError as exc:
            log(f"Watchlist change failed: {exc}")
            notify(L(30214), icon=xbmcgui.NOTIFICATION_ERROR)
            return False
        watchlist_items(client, refresh=True)
        _membership.clear()
        if ADDON.getSettingBool("library_watchlist"):
            _follow_in_library(type_, id_, add, meta)
    name = meta.name if meta else id_
    notify(L(30303 if add else 30304, name=name))
    notify_widgets(watch_only=True)
    return True


def _follow_in_library(type_, id_, add, meta):
    library = get_library()
    if add and not library.contains(type_, id_) and sources_configured():
        folder = add_title(type_, id_, source=WATCHLIST, meta=meta)
        if folder:
            scan(folder)
    elif not add:
        entry = library.get(type_, id_)
        if entry and entry["source"] == WATCHLIST:
            library.remove(type_, id_)
            clean()


@route("watchlist_add")
def watchlist_add(plugin, type, id):
    if change_watchlist(type, id, True):
        refresh_container()


@route("watchlist_remove")
def watchlist_remove(plugin, type, id):
    if change_watchlist(type, id, False):
        refresh_container()
