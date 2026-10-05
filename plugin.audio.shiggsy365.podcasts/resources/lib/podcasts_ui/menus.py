"""The add-on's folders and actions."""

import functools
import time
from datetime import datetime, timezone

import xbmc
import xbmcgui
import xbmcplugin

from podcasts import PodcastError
from podcasts.apple import GENRES
from podcasts.models import Podcast
from podcasts.store import find

from .common import (
    ADDON, ADDON_NAME, L, RELOAD_TOKEN, announce_playback, busy, clock_text, get_library, get_store,
    get_sync_client, log, notify, notify_widgets,
)
from .router import route

PLAYED_COLOUR = "FF8A8A8A"  # played episodes' titles are dimmed
HISTORY = "search_history"
HISTORY_SIZE = 20
SYNC_REQUEST = f"{ADDON.getAddonInfo('id')}.sync_request"  # read by the service


def request_sync():
    """Ask the service to sync soon (it debounces)."""
    xbmcgui.Window(10000).setProperty(SYNC_REQUEST, str(time.time()))


def guarded(func):
    """Show what went wrong instead of an empty folder and a Kodi error dialog."""
    @functools.wraps(func)
    def wrapper(plugin, *args, **kwargs):
        try:
            return func(plugin, *args, **kwargs)
        except PodcastError as exc:
            log(f"{func.__name__}: {exc}", xbmc.LOGWARNING)
            notify(L(30025, error=exc), xbmcgui.NOTIFICATION_WARNING, 5000)
            if plugin.handle >= 0 and func.__name__ != "play":
                xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
            elif plugin.handle >= 0:
                xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())
    return wrapper


def _folder(plugin, label, action, icon, widget=False, **params):
    """`widget`: worth picking as a home-screen widget, so its path carries the reload token."""
    item = xbmcgui.ListItem(label, offscreen=True)
    item.setArt({"icon": icon, "thumb": icon})
    url = plugin.url_for(action, **params) + (RELOAD_TOKEN if widget else "")
    xbmcplugin.addDirectoryItem(plugin.handle, url, item, isFolder=True)


def _end(plugin, content=None, cache=True):
    if content:
        xbmcplugin.setContent(plugin.handle, content)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=cache)


# ---------------------------------------------------------------- home


@route("root")
def root(plugin):
    _folder(plugin, L(30001), "my_podcasts", "DefaultMusicAlbums.png", widget=True)
    _folder(plugin, L(30031), "unplayed_podcasts", "DefaultMusicAlbums.png", widget=True)
    _folder(plugin, L(30002), "latest", "DefaultMusicRecentlyAdded.png", widget=True)
    _folder(plugin, L(30003), "trending", "DefaultMusicGenres.png")
    _folder(plugin, L(30004), "top", "DefaultMusicTop100.png")
    _folder(plugin, L(30005), "search", "DefaultAddonsSearch.png")
    _end(plugin)


# ---------------------------------------------------------------- podcasts


def _podcast_items(plugin, podcasts, store=None, counts=None):
    """`counts`: unplayed episodes per podcast, shown in place of the author."""
    store = store or get_store()
    subscribed = store.subscriptions()
    feeds = {s["feed_url"] for s in subscribed}
    apple_ids = {s["apple_id"] for s in subscribed if s["apple_id"]}
    for index, podcast in enumerate(podcasts):
        label2 = L(30032, count=counts[index]) if counts else podcast.author
        item = xbmcgui.ListItem(podcast.title, label2, offscreen=True)
        art = podcast.image or "DefaultMusicAlbums.png"
        item.setArt({"thumb": art, "icon": art, "poster": art, "fanart": podcast.image})
        tag = item.getMusicInfoTag()
        tag.setAlbum(podcast.title)
        tag.setArtist(podcast.author)
        tag.setComment(podcast.description)
        if podcast.genre:
            tag.setGenres([podcast.genre])
        params = {"feed": podcast.feed_url or None, "id": podcast.apple_id or None}
        if podcast.feed_url in feeds or podcast.apple_id in apple_ids:
            menu = [(L(30012), plugin.run_url("unsubscribe", **params))]
        else:
            menu = [(L(30011), plugin.run_url("subscribe", **params))]
        item.addContextMenuItems(menu)
        xbmcplugin.addDirectoryItem(plugin.handle, plugin.url_for("podcast", **params), item, isFolder=True)


@route("my_podcasts")
@guarded
def my_podcasts(plugin):
    library = get_library()
    podcasts = library.my_podcasts()
    if not podcasts:
        notify(L(30009), time_ms=5000)
    _podcast_items(plugin, podcasts, library.store)
    _end(plugin, "albums", cache=False)


@route("unplayed_podcasts")
@guarded
def unplayed_podcasts(plugin):
    library = get_library()
    waiting = library.unplayed_podcasts()
    _podcast_items(plugin, [p for p, _ in waiting], library.store, counts=[n for _, n in waiting])
    _end(plugin, "albums", cache=False)


@route("trending")
def trending(plugin):
    for genre_id, name in GENRES:
        _folder(plugin, name, "genre", "DefaultMusicGenres.png", id=genre_id)
    _end(plugin)


@route("genre")
@guarded
def genre(plugin, id):
    library = get_library()
    xbmcplugin.setPluginCategory(plugin.handle, L(30029, genre=dict(GENRES).get(int(id), "")))
    _podcast_items(plugin, library.directory.genre_chart(id), library.store)
    _end(plugin, "albums", cache=False)


@route("top")
@guarded
def top(plugin):
    library = get_library()
    _podcast_items(plugin, library.directory.top(), library.store)
    _end(plugin, "albums", cache=False)


# ---------------------------------------------------------------- search


def _history(store):
    return [q for q in store.get_state(HISTORY, []) if isinstance(q, str)]


@route("search")
def search(plugin):
    store = get_store()
    _folder(plugin, L(30006), "new_search", "DefaultAddonsSearch.png")
    for query in _history(store):
        item = xbmcgui.ListItem(query, offscreen=True)
        item.setArt({"icon": "DefaultAddonsSearch.png"})
        item.addContextMenuItems([(L(30027), plugin.run_url("forget_search", query=query)),
                                  (L(30008), plugin.run_url("clear_searches"))])
        xbmcplugin.addDirectoryItem(plugin.handle, plugin.url_for("search_results", query=query), item, isFolder=True)
    _end(plugin, cache=False)


@route("new_search")
def new_search(plugin):
    query = xbmcgui.Dialog().input(L(30007)).strip()
    xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
    if query:
        store = get_store()
        store.set_state(HISTORY, ([query] + [q for q in _history(store) if q.lower() != query.lower()])[:HISTORY_SIZE])
        xbmc.executebuiltin(f"Container.Update({plugin.url_for('search_results', query=query)})")


@route("search_results")
@guarded
def search_results(plugin, query):
    library = get_library()
    results = library.directory.search(query)
    if not results:
        notify(L(30026))
    xbmcplugin.setPluginCategory(plugin.handle, query)
    _podcast_items(plugin, results, library.store)
    _end(plugin, "albums", cache=False)


@route("forget_search")
def forget_search(plugin, query):
    store = get_store()
    store.set_state(HISTORY, [q for q in _history(store) if q != query])
    xbmc.executebuiltin("Container.Refresh")


@route("clear_searches")
def clear_searches(plugin):
    get_store().set_state(HISTORY, [])
    xbmc.executebuiltin("Container.Refresh")


# ---------------------------------------------------------------- episodes


def _released(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc) if epoch else None


def _episode_item(plugin, episode, row, show_podcast=False, extra_menu=()):
    played = bool(row and row["played"])
    if played and ADDON.getSettingBool("hide_played"):
        return
    position = row["position"] if row and not played else 0
    duration = episode.duration or (row["duration"] if row else 0)
    released = _released(episode.published)
    label = f"{episode.podcast_title}: {episode.title}" if show_podcast and episode.podcast_title else episode.title
    if played:
        # Kodi gives music items no watched overlay, so skins can't tick them: say it in the label.
        label = f"[COLOR {PLAYED_COLOUR}]{label}[/COLOR]"
        label2 = L(30030)
    elif position and duration:
        label2 = L(30020, time=clock_text(max(duration - position, 0)))
    else:
        label2 = released.strftime("%d %b %Y") if released else ""
    item = xbmcgui.ListItem(label, label2, offscreen=True)
    art = episode.image or "DefaultMusicSongs.png"
    item.setArt({"thumb": art, "icon": art, "fanart": episode.image})
    tag = item.getMusicInfoTag()
    tag.setMediaType("song")
    tag.setTitle(episode.title)
    tag.setArtist(episode.podcast_title)
    tag.setAlbum(episode.podcast_title)
    tag.setComment(episode.description)
    if duration:
        tag.setDuration(int(duration))
    if released:
        tag.setReleaseDate(released.strftime("%Y-%m-%d"))
        tag.setYear(released.year)
    if played:
        tag.setPlayCount(1)
    item.setProperty("IsPlayable", "true")
    params = {"feed": episode.feed_url, "key": episode.key}
    menu = [(L(30014), plugin.run_url("mark", played="0", **params)) if played or position else None,
            (L(30013), plugin.run_url("mark", played="1", **params)) if not played else None]
    if show_podcast:
        # ActivateWindow, not Container.Update: that does nothing from a home-screen widget.
        menu.append((L(30024), f"ActivateWindow(Music,{plugin.url_for('podcast', feed=episode.feed_url)},return)"))
    menu += list(extra_menu)
    item.addContextMenuItems([entry for entry in menu if entry])
    xbmcplugin.addDirectoryItem(plugin.handle, plugin.url_for("play", **params), item, isFolder=False)


@route("podcast")
@guarded
def podcast(plugin, feed=None, id=None):
    library = get_library()
    resolved = library.resolve(feed or "", id or "")
    details, episodes = library.feed(resolved.feed_url)
    rows = library.store.progress_for(resolved.feed_url)
    xbmcplugin.setPluginCategory(plugin.handle, details.title)
    params = {"feed": resolved.feed_url, "id": resolved.apple_id or None}
    subscribed = library.store.is_subscribed(resolved.feed_url)
    toggle = (L(30012), plugin.run_url("unsubscribe", **params)) if subscribed else \
        (L(30011), plugin.run_url("subscribe", **params))
    refresh_entry = (L(30015), plugin.run_url("refresh", feed=resolved.feed_url))
    for episode in episodes:
        _episode_item(plugin, episode, find(rows, episode), extra_menu=[toggle, refresh_entry])
    xbmcplugin.addSortMethod(plugin.handle, xbmcplugin.SORT_METHOD_UNSORTED)
    xbmcplugin.addSortMethod(plugin.handle, xbmcplugin.SORT_METHOD_DATE)
    xbmcplugin.addSortMethod(plugin.handle, xbmcplugin.SORT_METHOD_TITLE)
    _end(plugin, "songs", cache=False)


@route("latest")
@guarded
def latest(plugin):
    library = get_library()
    entries = library.latest()
    if not entries:
        notify(L(30010), time_ms=5000)
    for episode, row in entries:
        _episode_item(plugin, episode, row, show_podcast=True)
    _end(plugin, "songs", cache=False)


# ---------------------------------------------------------------- actions


@route("play")
@guarded
def play(plugin, feed, key):
    library = get_library()
    episode = library.episode(feed, key)
    row = find(library.store.progress_for(feed), episode)
    offset = row["position"] if row and not row["played"] else 0
    # Kodi has no resume points for music items, so it says "resume:false"
    # without asking; only "resume:true" means it asked (from its own bookmark).
    if offset and plugin.resume is not True and ADDON.getSettingBool("ask_resume"):
        choice = xbmcgui.Dialog().contextmenu([L(30018, time=clock_text(offset)), L(30019)])
        if choice < 0:
            xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())
            return
        offset = offset if choice == 0 else 0
    item = xbmcgui.ListItem(episode.title, path=episode.url, offscreen=True)
    # Podcast URLs often end in a query string, not ".mp3": without a type Kodi
    # hands them to its video player, which keeps its own resume bookmarks.
    item.setMimeType(episode.mime if episode.mime.startswith(("audio/", "video/")) else "audio/mpeg")
    item.setContentLookup(False)
    item.setArt({"thumb": episode.image, "icon": episode.image, "fanart": episode.image})
    tag = item.getMusicInfoTag()
    tag.setMediaType("song")
    tag.setTitle(episode.title)
    tag.setArtist(episode.podcast_title)
    tag.setAlbum(episode.podcast_title)
    if episode.duration:
        tag.setDuration(episode.duration)
    log(f"Playing {episode.title} from {int(offset)}s (saved {row['position'] if row else 0}s, Kodi resume flag {plugin.resume})")
    announce_playback(episode, offset)
    xbmcplugin.setResolvedUrl(plugin.handle, True, item)


@route("mark")
@guarded
def mark(plugin, feed, key, played):
    library = get_library()
    library.store.set_played(library.episode(feed, key), played == "1")
    request_sync()
    notify_widgets()
    xbmc.executebuiltin("Container.Refresh")


def _details(library, feed, id):
    podcast = library.resolve(feed or "", id or "")
    if not podcast.title or not podcast.image:
        details, _ = library.feed(podcast.feed_url)
        podcast = Podcast(title=details.title, feed_url=podcast.feed_url, author=details.author,
                          image=details.image or podcast.image, apple_id=podcast.apple_id)
    return podcast


@route("subscribe")
@guarded
def subscribe(plugin, feed=None, id=None):
    library = get_library()
    with busy():
        podcast = _details(library, feed, id)
    library.store.subscribe(podcast)
    request_sync()
    notify_widgets()
    notify(L(30016, title=podcast.title))
    xbmc.executebuiltin("Container.Refresh")


@route("unsubscribe")
@guarded
def unsubscribe(plugin, feed=None, id=None):
    library = get_library()
    if not feed:  # a chart entry: find the subscription by Apple id
        feed = next((s["feed_url"] for s in library.store.subscriptions() if s["apple_id"] == id), None) \
            or library.resolve("", id).feed_url
    title = next((s["title"] for s in library.store.subscriptions() if s["feed_url"] == feed), "") or feed
    library.store.unsubscribe(feed)
    request_sync()
    notify_widgets()
    notify(L(30017, title=title))
    xbmc.executebuiltin("Container.Refresh")


@route("refresh")
@guarded
def refresh(plugin, feed):
    with busy():
        get_library().feed(feed, refresh=True)
    xbmc.executebuiltin("Container.Refresh")


@route("sync_now")
def sync_now(plugin):
    from podcasts.sync import sync

    client = get_sync_client()
    if client is None:
        notify(L(30028), time_ms=5000)
        return
    try:
        with busy():
            summary = sync(get_store(), client, log=log)
    except PodcastError as exc:
        log(f"Sync failed: {exc}", xbmc.LOGWARNING)
        notify(L(30023, error=exc), xbmcgui.NOTIFICATION_ERROR, 6000)
        return
    notify_widgets()
    notify(L(30022, sent=summary["subscriptions_sent"] + summary["positions_sent"],
             received=summary["subscriptions_received"] + summary["positions_received"]))
    xbmc.executebuiltin("Container.Refresh")
