"""Global Search in Kodi: ask for the search text, search every source, show
the rows. Start it with RunScript(script.shiggsy365.globalsearch) from any
menu item or button, or RunScript(script.shiggsy365.globalsearch,query=...)."""

import json
import threading

import xbmc
import xbmcaddon
import xbmcgui

from . import sources
from .window import ResultsWindow

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
LAST_QUERY = f"{ADDON_ID}.last_query"
LIBRARY_ROWS = {"movies": 32020, "tvshows": 32021, "episodes": 32022, "artists": 32023, "albums": 32024,
                "songs": 32025}


def L(string_id, **kwargs):
    text = ADDON.getLocalizedString(string_id)
    return text.format(**kwargs) if kwargs else text


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def rpc(method, params=None):
    response = json.loads(xbmc.executeJSONRPC(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})))
    if "error" in response:
        raise RuntimeError(f"{method}: {response['error'].get('message')}")
    return response.get("result") or {}


def has_addon(addon_id):
    return xbmc.getCondVisibility(f"System.HasAddon({addon_id})")


def searches(query):
    """``[(label, function returning [Row])]`` for the enabled sources, in display order."""
    limit = max(5, ADDON.getSettingInt("max_items"))
    library = L(32026)

    def library_rows(fetch, shape):
        def run():
            return [sources.Row(f"{library} · {L(LIBRARY_ROWS[key])}", items, shape) for key, items in fetch(rpc, query, limit)]
        return run

    def stremio():
        return [sources.Row(f"Stremio Bridge · {title}", items) for title, items in sources.stremio_rows(rpc, query, limit)]

    def spotify(categories):
        def run():
            return [sources.Row(f"Spotify · {title}", items, sources.SQUARE)
                    for title, items in sources.spotify_rows(rpc, query, limit, categories)]
        return run

    found = []
    if ADDON.getSettingBool("library_video"):
        found.append((L(32027), library_rows(sources.library_video_rows, sources.POSTER)))
    if ADDON.getSettingBool("library_music"):
        found.append((L(32028), library_rows(sources.library_music_rows, sources.SQUARE)))
    if ADDON.getSettingBool("stremio_bridge") and has_addon(sources.STREMIO_BRIDGE):
        found.append(("Stremio Bridge", stremio))
    if has_addon(sources.SPOTIFY) and ADDON.getSettingBool("spotify"):
        every = sources.SPOTIFY_MUSIC + sources.SPOTIFY_PODCASTS
        if ADDON.getSettingBool("spotify_load"):
            chosen = [c for c, setting in zip(every, ("spotify_songs", "spotify_artists", "spotify_albums",
                                                      "spotify_playlists", "spotify_podcasts", "spotify_episodes"))
                      if ADDON.getSettingBool(setting)]
            if chosen:
                found.append(("Spotify", spotify(chosen)))
        else:
            icons = f"special://home/addons/{sources.SPOTIFY}/resources"
            tiles = sources.spotify_shortcuts(query, every, icons)
            found.append(("Spotify", lambda: [sources.Row(L(32037, query=query), tiles, sources.SQUARE, counted=False)]))
    return found


def gather(query):
    """Run every source in parallel with a progress dialog. Returns the rows
    in source order, or None if cancelled."""
    jobs = searches(query)
    results = [None] * len(jobs)

    def run(index, function):
        try:
            results[index] = function()
        except Exception as exc:  # one source failing mustn't spoil the rest
            log(f"{jobs[index][0]} search failed: {exc}", xbmc.LOGWARNING)
            results[index] = []

    threads = [threading.Thread(target=run, args=(i, fn), daemon=True) for i, (_, fn) in enumerate(jobs)]
    for thread in threads:
        thread.start()
    progress = xbmcgui.DialogProgress()
    progress.create(L(32000), L(32001, query=query))
    monitor = xbmc.Monitor()
    try:
        while any(t.is_alive() for t in threads):
            done = sum(1 for r in results if r is not None)
            waiting = ", ".join(label for (label, _), r in zip(jobs, results) if r is None)
            progress.update(int(100 * done / max(1, len(jobs))), L(32002, sources=waiting))
            if progress.iscanceled() or monitor.waitForAbort(0.2):
                return None
    finally:
        progress.close()
    return [row for rows in results for row in rows or [] if row.items]


def ask(default=""):
    keyboard = xbmc.Keyboard(default, L(32003))
    keyboard.doModal()
    return keyboard.getText().strip() if keyboard.isConfirmed() else ""


def run(argv):
    params = dict(arg.split("=", 1) for arg in argv[1:] if "=" in arg)
    home = xbmcgui.Window(10000)
    query = params.get("query", "").strip() or ask(home.getProperty(LAST_QUERY) if ADDON.getSettingBool(
        "remember_query") else "")
    if not query:
        return
    home.setProperty(LAST_QUERY, query)
    rows = gather(query)
    if rows is None:
        return
    if not rows:
        xbmcgui.Dialog().notification(L(32000), L(32004, query=query), ADDON.getAddonInfo("icon"))
        return
    window = ResultsWindow("globalsearch-results.xml", ADDON.getAddonInfo("path"), "Default", "1080i")
    window.heading = L(32005, query=query)
    window.rows = rows
    window.doModal()
    del window
