"""Playback: fetch streams from every addon, pick one, resolve it for Kodi."""

import os
import shutil
import threading

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from stremio.streams import (
    TORRENT, URL, StreamPrefs, describe, fallback_order, fetch_streams, header_string, parse_keywords,
    play_path, prepare_streams, probe,
)
from stremio.models import external_ids
from stremio.subtitles import download_subtitles, fetch_subtitles, parse_languages, select_subtitles
from stremio.watchstate import PlaybackEntry

from .common import (
    ADDON, ADDON_NAME, L, announce_playback, clock_text, get_client, get_registry, get_watchstate, log, notify,
    run_with_progress,
)
from .details import load_meta
from .listitems import playback_item
from .router import route

INPUTSTREAM = "inputstream.adaptive"
ELEMENTUM = "plugin.video.elementum"
MAX_RESOLUTIONS = {0: None, 1: 2160, 2: 1080, 3: 720}
TORRENTS_HIDE, TORRENTS_ELEMENTUM = 0, 1
# Reuse a show's last source group if it was played within this long.
SAME_SOURCE_WINDOW = 24 * 3600


def has_addon(addon_id):
    return xbmc.getCondVisibility(f"System.HasAddon({addon_id})")


def stream_prefs():
    return StreamPrefs(
        sort=ADDON.getSettingInt("sort_mode"),
        max_resolution=MAX_RESOLUTIONS.get(ADDON.getSettingInt("max_resolution")),
        hide_cam=ADDON.getSettingBool("hide_cam"),
        exclude=parse_keywords(ADDON.getSettingString("exclude_keywords")),
        allow_torrents=ADDON.getSettingInt("torrent_mode") == TORRENTS_ELEMENTUM and has_addon(ELEMENTUM),
    )


@route("play")
def play(plugin, type, id, meta=None, binge=None, resume=None, start=None, tries=None, pick=None):
    """`id` is what streams are requested for (e.g. ``tt123:1:2`` for an
    episode); `meta` is the show's id when that differs. `binge` asks for a
    stream from the same source group (sent by Up Next for the next episode).
    `resume=1` resumes from the saved position, `resume=0` starts from the
    beginning (the Extended info dialog asks).
    `start`/`tries` come from the service when a stream failed after starting:
    skip the streams already tried (autoplay order) and count the retries.
    `pick=1` ("Show Playable Streams") shows the stream list even with autoplay on."""
    if resume in ("1", "0"):  # asked already (e.g. by Extended info): resume or from the start
        plugin.resume = resume == "1"
    handle = plugin.handle
    pick = pick == "1"

    def cancel():
        if handle >= 0:
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
    registry = get_registry()
    addons = registry.addons_for("stream", type, id)
    if not addons:
        notify(L(30172), icon=xbmcgui.NOTIFICATION_WARNING)
        return cancel()

    client = get_client()
    streams, errors, cancelled = _fetch_with_progress(client, addons, type, id)
    for label, exc in errors:
        log(f"Streams from {label} failed: {exc}")
    if cancelled:
        return cancel()

    prefs = stream_prefs()
    candidates = prepare_streams(streams, prefs)
    log(f"{len(streams)} streams for {type} {id}, {len(candidates)} after filters")
    if not candidates:
        notify(L(30176, count=len(streams)) if streams else L(30173), icon=xbmcgui.NOTIFICATION_WARNING)
        return cancel()

    state = get_watchstate()
    if pick and plugin.resume is None and not _ask_resume(plugin, state.get(id)):
        return cancel()
    group = binge or ""
    if not group and meta and ADDON.getSettingBool("same_source_next"):
        group = state.binge_group(meta, SAME_SOURCE_WINDOW)
    preferred = same_source(candidates, group)
    if preferred is not None:
        log(f"Same source as last time ({preferred.addon})")
    retry = None
    if ADDON.getSettingBool("autoplay") and not pick:
        # Try streams in order until one actually serves video.
        order = fallback_order(candidates, preferred)
        skip = int(start or 0)
        stream, cancelled = first_working(client, order[skip:])
        if cancelled:
            return cancel()
        if stream is None:
            notify(L(30179, count=len(candidates)), icon=xbmcgui.NOTIFICATION_ERROR)
            return cancel()
        # If Kodi then fails to play it, the service starts again from the next one.
        retry = {"type": type, "id": id, "meta": meta, "binge": binge, "next": order.index(stream) + 1,
                 "total": len(order), "tries": int(tries or 0)}
    else:
        stream = None
        if preferred is not None and not pick:
            stream, cancelled = first_working(client, [preferred])
            if cancelled:
                return cancel()
        if stream is None:
            stream = _choose(candidates, addons)
    if stream is None:
        return cancel()

    use_inputstream = stream.kind == URL and stream.is_adaptive and has_addon(INPUTSTREAM)
    path = play_path(stream, prefs.allow_torrents, headers_in_url=not use_inputstream)
    if path is None:
        notify(L(30175), icon=xbmcgui.NOTIFICATION_ERROR)
        return cancel()

    info = load_meta(type, meta or id, quiet=True)
    item = playback_item(path, info, id, fallback_title=stream.filename or stream.name)
    if use_inputstream:
        _use_inputstream(item, stream)
    if stream.kind != TORRENT and ADDON.getSettingBool("subtitles_enabled"):
        _attach_subtitles(item, client, registry, type, id, stream)

    # Tell the playback service what this is, and where to resume if Kodi's
    # "Resume from…" was chosen (plugin.resume is None when Kodi didn't ask).
    row = state.get(id)
    offset = row.position if plugin.resume and row else 0.0
    if retry is not None:
        retry["resume"] = bool(offset)
    announce_playback(playback_entry(type, id, meta, info, stream), offset, retry)

    # Never log the path: debrid links and configured addons carry tokens.
    log(f"Playing {type} {id} from {stream.addon}: {describe(stream)[0] or stream.kind}")
    if handle >= 0:
        xbmcplugin.setResolvedUrl(handle, True, item)
    else:
        xbmc.Player().play(path, item)


def _ask_resume(plugin, row):
    """Kodi's "Resume from…" question, for playback started from our own
    context menu (Kodi only asks when an item is selected). Sets
    plugin.resume; False if the user backed out."""
    if not row or row.position <= 0:
        return True
    choice = xbmcgui.Dialog().contextmenu([L(30227, time=clock_text(row.position)), L(30228)])
    if choice < 0:
        return False
    plugin.resume = choice == 0
    return True


def first_working(client, ordered):
    """The first stream in `ordered` whose link works (see streams.probe), with
    a cancellable "Trying stream 3 of 25" dialog. Returns ``(stream, cancelled)``;
    stream is None if none worked."""
    dialog = xbmcgui.DialogProgress()
    dialog.create(ADDON_NAME, L(30177, number=1, total=len(ordered)))
    try:
        for number, stream in enumerate(ordered, 1):
            headline = describe(stream)[0] or stream.name or stream.kind
            dialog.update(int((number - 1) * 100 / len(ordered)),
                          f"{L(30177, number=number, total=len(ordered))}[CR]{stream.addon} · {headline}")
            result = _probe_cancellable(client, stream, dialog)
            if result is None:
                return None, True
            ok, reason = result
            if ok:
                return stream, False
            log(f"Stream {number}/{len(ordered)} from {stream.addon} failed: {reason}")
        return None, False
    finally:
        dialog.close()


def _probe_cancellable(client, stream, dialog):
    """Probe in the background so Cancel works straight away; None if cancelled."""
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("r", probe(client.session, stream)), daemon=True)
    worker.start()
    while worker.is_alive():
        if dialog.iscanceled():
            return None
        worker.join(0.2)
    if dialog.iscanceled():
        return None
    return result.get("r", (False, "probe crashed"))


def same_source(candidates, group):
    """The best candidate from `group` (Stremio's bingeGroup), if any."""
    if not group:
        return None
    return next((s for s in candidates if s.binge_group == group), None)


def playback_entry(type_, id_, meta_id, info, stream):
    video = next((v for v in info.videos if v.id == id_), None) if info else None
    entry = PlaybackEntry(
        video_id=id_, type=type_, meta_id=(meta_id or (info.id if info and video else "")),
        binge_group=stream.binge_group,
        ids=dict(info.external_ids) if info else external_ids(meta_id or id_),
    )
    if info is not None:
        entry.poster, entry.fanart = info.poster, info.background
        if video is not None:
            entry.season, entry.episode, entry.title = video.season, video.episode, video.title
            entry.show_title, entry.thumb = info.name, video.thumbnail or info.background
        else:
            entry.title, entry.thumb = info.name, info.background
    else:
        entry.title = stream.filename or stream.name
    return entry


def _fetch_with_progress(client, addons, type_, id_):
    return run_with_progress(
        L(30170), 30171, lambda progress: fetch_streams(client, addons, type_, id_, progress)
    )


def _choose(candidates, addons):
    items = []
    for stream in candidates:
        headline, details = describe(stream)
        label = f"[B]{headline or stream.name or stream.kind}[/B]  [COLOR grey]{stream.addon}[/COLOR]"
        item = xbmcgui.ListItem(label, label2=details)
        logo = addons[stream.addon_index].manifest.logo
        if logo:
            item.setArt({"icon": logo, "thumb": logo})
        items.append(item)
    index = xbmcgui.Dialog().select(L(30174), items, useDetails=True)
    return candidates[index] if index >= 0 else None


def _use_inputstream(item, stream):
    item.setProperty("inputstream", INPUTSTREAM)
    if stream.headers:
        headers = header_string(stream.headers)
        item.setProperty(f"{INPUTSTREAM}.manifest_headers", headers)
        item.setProperty(f"{INPUTSTREAM}.stream_headers", headers)
    item.setMimeType(stream.adaptive_mimetype)
    item.setContentLookup(False)


def _attach_subtitles(item, client, registry, type_, id_, stream):
    subtitles = list(stream.subtitles)
    addons = registry.addons_for("subtitles", type_, id_)
    if addons:
        fetched, errors = fetch_subtitles(client, addons, type_, id_, stream.subtitle_extra())
        subtitles += fetched
        for label, exc in errors:
            log(f"Subtitles from {label} failed: {exc}")

    chosen = select_subtitles(subtitles, parse_languages(ADDON.getSettingString("subtitle_languages")))
    if not chosen:
        return
    dest = os.path.join(xbmcvfs.translatePath("special://temp"), "stremiobridge", "subtitles")
    shutil.rmtree(dest, ignore_errors=True)
    paths = download_subtitles(client, chosen, dest)
    log(f"{len(paths)} of {len(subtitles)} subtitles attached")
    if paths:
        item.setSubtitles(paths)
