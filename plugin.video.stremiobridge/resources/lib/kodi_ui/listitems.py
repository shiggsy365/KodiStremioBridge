"""Stremio objects -> Kodi ListItems."""

import datetime

import xbmc
import xbmcgui

from stremio.models import CAST

from .common import ADDON, L, add_context_menu, get_mdblist, get_watchstate, select_opens_info, skin_active

# Stremio type -> (Kodi media type, container content)
_KODI_TYPES = {
    "movie": ("movie", "movies"),
    "series": ("tvshow", "tvshows"),
}
# Types whose items play directly; everything else (series, channel, ...) opens a folder.
PLAYABLE_TYPES = ("movie", "tv")
YOUTUBE_PLUGIN = "plugin://plugin.video.youtube/play/?video_id={}"


def preview_items(plugin, previews, state, hide_watched=False):
    """ListItems for catalog entries, with watch state: per movie, and for shows
    you've started, how many aired episodes you've watched. `hide_watched`
    leaves out watched movies and shows with every aired episode watched."""
    from .watching import show_progress  # watching imports this module

    rows = state.lookup([p.id for p in previews if p.type in PLAYABLE_TYPES])
    shows = [(p.id, p.type) for p in previews if p.type not in PLAYABLE_TYPES]
    started = {show_id for show_id, _ in shows if state.watched_episodes(show_id)}
    progress = show_progress(state, [s for s in shows if s[0] in started]) if started else {}
    items = []
    for preview in previews:
        row, shown = rows.get(preview.id), progress.get(preview.id)
        if hide_watched and ((row is not None and row.watched) or (shown and 0 < shown[1] <= shown[0])):
            continue
        items.append(preview_item(plugin, preview, row, started=preview.id in started, progress=shown))
    return items


def playable_entry(plugin, item, type_, video_id, **params):
    """``(url, item, is_folder)`` for something that plays when selected or,
    with "Selecting a movie or episode opens Extended info", opens that page."""
    add_context_menu(item, pick_streams_menu(plugin, type_, video_id, **params))
    show_id = params.get("meta")
    if select_opens_info() and (type_ == "movie" or (show_id and show_id != video_id)):
        url = plugin.url_for("extended_info", type=type_, id=show_id or video_id,
                             video=video_id if show_id else None)
        return url, item, False
    item.setProperty("IsPlayable", "true")
    return plugin.url_for("play", type=type_, id=video_id, **params), item, False


def pick_streams_menu(plugin, type_, video_id, **params):
    """With autoplay on: "Show Playable Streams", to choose a stream yourself."""
    if not ADDON.getSettingBool("autoplay"):
        return []
    url = plugin.url_for("play", type=type_, id=video_id, pick=1, **params)
    return [(L(30365), f"PlayMedia({url})")]


def content_for(type_):
    return _KODI_TYPES.get(type_, ("video", "videos"))[1]


GENRE_SEPARATOR = "  \u2022  "


def set_genres(item, genres):
    """Genres as Arctic Zephyr Stremio shows them ("Drama  •  Fantasy");
    Kodi's own ListItem.Genre joins them with slashes."""
    item.setProperty("stremiobridge.genres", GENRE_SEPARATOR.join(genres))


def _apply_preview(item, preview):
    """Art and info shared by catalog entries and full metas."""
    art = {"fanart": preview.background, "clearlogo": preview.logo}
    if preview.poster_shape == "poster":
        art.update(poster=preview.poster, thumb=preview.poster)
    else:
        art.update(thumb=preview.poster, landscape=preview.poster, icon=preview.poster)
    item.setArt({k: v for k, v in art.items() if v})

    tag = item.getVideoInfoTag()
    tag.setMediaType(_KODI_TYPES.get(preview.type, ("video",))[0])
    tag.setTitle(preview.name)
    tag.setPlot(preview.description)
    if preview.year:
        tag.setYear(preview.year)
    if preview.premiered:
        item.setProperty("ReleaseDate", _release_date(preview.premiered))
    if preview.genres:
        tag.setGenres(list(preview.genres))
        set_genres(item, preview.genres)
    if preview.imdb_rating is not None:
        tag.setRating(preview.imdb_rating, type="imdb", isdefault=True)
    if preview.runtime_seconds:
        tag.setDuration(preview.runtime_seconds)
    if preview.id.startswith("tt"):
        tag.setUniqueIDs({"imdb": preview.id}, "imdb")
    _apply_people(tag, preview)
    return tag


def _apply_people(tag, source):
    """Cast (with photos and characters), directors, writers and trailer, so
    Kodi's own Information dialog shows them."""
    cast = [p for p in source.people if p.job == CAST]
    if cast:
        tag.setCast([xbmc.Actor(p.name, p.role, i, p.photo) for i, p in enumerate(cast)])
    if source.director:
        tag.setDirectors(list(source.director))
    if source.writer:
        tag.setWriters(list(source.writer))
    if source.trailer:
        tag.setTrailer(YOUTUBE_PLUGIN.format(source.trailer))


def item_menu(plugin, type_, id_, watched, watched_params=None, show_id=None, trailer=""):
    """The context menu every playable/browsable item gets. `show_id` is the
    show an episode belongs to (its trailer, watchlist and info page use it).
    One Mark as watched/unwatched entry, for what the item is: a movie, an
    episode, a season (`watched_params` has it) or a whole show."""
    owner = show_id or id_
    params = watched_params or {}
    # With our skin, Kodi's own Mark as watched covers movies and episodes (the
    # service passes it on); it doesn't offer it for shows and seasons.
    kodi_marks = skin_active() and (type_ in PLAYABLE_TYPES or bool(show_id) or bool(params.get("meta")))
    menu = [] if kodi_marks else [watched_menu(plugin, watched, type_, id_, **params)]
    episode = id_ if show_id and id_ != show_id else None  # an episode: its own page, within the show's
    if not skin_active():  # with our skin, Kodi's own Information entry shows the same page
        menu.append((L(30220), plugin.run_url("extended_info", type=type_, id=owner, video=episode)))
    from .library import library_menu  # library imports details, which imports this module

    menu += library_menu(plugin, type_, owner)
    from .watchlist import watchlist_menu

    menu += watchlist_menu(plugin, type_, owner)
    menu.append((L(30067), plugin.run_url("play_trailer", type=type_, id=owner, yt=trailer or None)))
    return menu


def _apply_meta(item, meta):
    tag = _apply_preview(item, meta)
    if meta.country:
        tag.setCountries([c.strip() for c in meta.country.split(",") if c.strip()])
    if meta.premiered:
        tag.setPremiered(meta.premiered)
    if meta.trailer:
        tag.setTrailer(YOUTUBE_PLUGIN.format(meta.trailer))
    return tag


def apply_meta_info(tag, meta, video=None):
    """Description and details of `meta` (or of `video`, one of its episodes)
    for an item built from watch state; its title and art are left alone."""
    tag.setPlot((video.overview if video else "") or meta.description)
    if video is not None and video.air_date:
        tag.setFirstAired(video.air_date)
        tag.setYear(int(video.air_date[:4]))
    elif video is None:
        if meta.year:
            tag.setYear(meta.year)
        if meta.premiered:
            tag.setPremiered(meta.premiered)
        if meta.imdb_rating is not None:
            tag.setRating(meta.imdb_rating, type="imdb", isdefault=True)
        if meta.runtime_seconds:
            tag.setDuration(meta.runtime_seconds)
    if meta.genres:
        tag.setGenres(list(meta.genres))
    if meta.id.startswith("tt"):
        tag.setUniqueIDs({"imdb": meta.id}, "imdb")
    _apply_people(tag, meta)


def apply_watch(item, row):
    """Watched tick and resume bar from a watch-state row (None = never played)."""
    mark_item_watched(item, bool(row and row.watched))
    if row is not None and row.position > 0 and row.duration > 0:
        item.getVideoInfoTag().setResumePoint(row.position, row.duration)


def mark_item_watched(item, watched):
    """Play count and Kodi's watched/unwatched overlay (ListItem.Overlay, which
    the skin's ticks and markers use): Kodi doesn't derive the overlay from the
    play count for add-on items."""
    item.getVideoInfoTag().setPlaycount(1 if watched else 0)
    overlay = xbmcgui.ICON_OVERLAY_WATCHED if watched else xbmcgui.ICON_OVERLAY_UNWATCHED
    item.setInfo("video", {"playcount": 1 if watched else 0, "overlay": overlay})


def watched_menu(plugin, watched, type_, id_, **params):
    """Context-menu entry toggling watched state (see the set_watched route)."""
    label = L(30191) if watched else L(30190)
    return label, plugin.run_url("set_watched", type=type_, id=id_, value=int(not watched), **params)


def preview_item(plugin, preview, row=None, started=False, progress=None):
    """Returns ``(url, ListItem, is_folder)`` for a catalog entry. `row` is its
    watch state (movies), if any; `started` means some of the show is watched;
    `progress` is ``(watched, aired)`` episodes for a started show."""
    item = xbmcgui.ListItem(preview.name, offscreen=True)
    _apply_preview(item, preview)
    apply_watch(item, row)
    if progress:
        watched, aired = progress
        item.setLabel2(L(30321, watched=watched, total=aired))
        item.setProperty("TotalEpisodes", str(aired))
        item.setProperty("WatchedEpisodes", str(watched))
        item.setProperty("UnWatchedEpisodes", str(max(0, aired - watched)))
    fully_watched = bool(row and row.watched) or bool(progress and 0 < progress[1] <= progress[0])
    if fully_watched:
        mark_item_watched(item, True)
    apply_title_actions(item, plugin, preview.type, preview.id, fully_watched)
    add_context_menu(item, item_menu(plugin, preview.type, preview.id, bool(row and row.watched),
                                       trailer=preview.trailer))

    if preview.type in PLAYABLE_TYPES:
        return playable_entry(plugin, item, preview.type, preview.id)
    if select_opens_info():  # the info page, with its season browser
        return plugin.url_for("extended_info", type=preview.type, id=preview.id), item, False
    return plugin.url_for("meta", type=preview.type, id=preview.id), item, True


def meta_item(meta):
    """A ListItem carrying everything we know about `meta` (for the info dialog)."""
    item = xbmcgui.ListItem(meta.name, offscreen=True)
    _apply_meta(item, meta)
    return item


def episode_code(video):
    """"S1 E3" (or "" for a video without numbers)."""
    if video is None or video.season is None or video.episode is None:
        return ""
    return L(30412, season=video.season, episode=video.episode)


def season_label(season):
    return L(30061) if season == 0 else L(30060, season=season)


def season_item(plugin, meta, season, episode_count, watched_count=0):
    item = xbmcgui.ListItem(season_label(season), offscreen=True)
    art = {"poster": meta.poster, "tvshow.poster": meta.poster, "fanart": meta.background,
           "clearlogo": meta.logo}
    item.setArt({k: v for k, v in art.items() if v})
    tag = item.getVideoInfoTag()
    tag.setMediaType("season")
    tag.setTvShowTitle(meta.name)
    tag.setSeason(season)
    tag.setTitle(season_label(season))
    tag.setPlot(meta.description)
    _apply_people(tag, meta)
    item.setProperty("TotalEpisodes", str(episode_count))
    item.setProperty("WatchedEpisodes", str(watched_count))
    item.setProperty("UnWatchedEpisodes", str(max(0, episode_count - watched_count)))
    all_watched = episode_count > 0 and watched_count >= episode_count
    mark_item_watched(item, all_watched)
    apply_info_actions(item, plugin, meta, watched=all_watched, season=season)
    add_context_menu(item, item_menu(plugin, meta.type, meta.id, all_watched, {"season": season},
                                       trailer=meta.trailer))
    url = plugin.url_for("season", type=meta.type, id=meta.id, season=season)
    item.setProperty("stremiobridge.season_path", url)  # the episodes, for a skin's preview of them
    if select_opens_info():  # the info page, on this season
        return plugin.url_for("extended_info", type=meta.type, id=meta.id, season=season), item, False
    return url, item, True


def episode_item(plugin, meta, video, released=True, row=None):
    """A playable episode (or channel video). Unreleased ones are shown greyed out."""
    item = episode_listitem(meta, video, released)
    apply_watch(item, row)
    apply_info_actions(item, plugin, meta, video, bool(row and row.watched))
    add_context_menu(item, item_menu(plugin, meta.type, video.id, bool(row and row.watched), {"meta": meta.id},
                                       show_id=meta.id, trailer=meta.trailer))
    return playable_entry(plugin, item, meta.type, video.id, meta=meta.id)


def _release_date(iso):
    try:
        return datetime.date.fromisoformat(iso).strftime("%d %B %Y")
    except (TypeError, ValueError):
        return ""


def apply_info_actions(item, plugin, meta, video=None, watched=False, season=None, header=True):
    """Properties Arctic Zephyr Stremio uses for the buttons on Kodi's info
    panel, for a movie, a show, one `season` of it or one of its episodes.
    Without `header` (the page's own episode cards) only what a card needs."""
    item.setProperty("ReleaseDate", _release_date(video.air_date if video else meta.premiered))
    shown = video.season if video is not None else season  # the season the show browser opens on
    focus = video
    if video is None and meta.videos:
        from .details import visible_seasons  # details imports this module
        from .infodialog import episode_to_play

        seasons = visible_seasons(meta)
        # The browser opens on what the Play button plays
        target = episode_to_play(meta, get_watchstate(), season)
        if shown is None:
            shown = target.season if target and target.season in seasons else (seasons[0] if seasons else None)
        focus = target if target and target.season == shown else None
    apply_title_actions(item, plugin, meta.type, meta.id, watched, video.id if video else None, season,
                        browse=bool(meta.videos), default_season=shown, header=header)
    if focus is not None:
        item.setProperty("stremiobridge.focus_video", focus.id)  # the browser opens on this episode


def apply_title_actions(item, plugin, type_, id_, watched, video=None, season=None, browse=None,
                        default_season=None, header=True):
    """The info panel's buttons for title `id_` (or its episode `video`, or its
    `season`): Play, Streams, Mark watched, Watchlist and Similar. `browse`
    (None: unless it's a movie) makes the in-page show browser its default panel.
    Without `header`, Watchlist and Similar are left out (they're the title's)."""
    from .library import LIBRARY_TYPES  # library and watchlist import details, which imports this module
    from .watchlist import on_watchlist

    item.setProperty("stremiobridge.type", type_)
    item.setProperty("stremiobridge.id", id_)
    if video is not None:
        item.setProperty("stremiobridge.video", video)
    whole_season = season if video is None and type_ not in PLAYABLE_TYPES else None
    if whole_season is not None:
        item.setProperty("stremiobridge.season", str(whole_season))
    target = {"type": type_, "id": id_, "video": video, "season": whole_season}
    item.setProperty("stremiobridge.play_action", plugin.url_for("info_play", **target))
    item.setProperty("stremiobridge.streams_action", plugin.url_for("info_play", pick=1, **target))
    item.setProperty("stremiobridge.watched_action", plugin.url_for(
        "info_toggle", what="watched", value=int(not watched), **target))
    if browse if browse is not None else type_ not in PLAYABLE_TYPES:
        item.setProperty("stremiobridge.browse", "true")
        if default_season is not None:
            item.setProperty("stremiobridge.default_season", str(default_season))
    if not header:
        return
    if get_mdblist() is not None:
        item.setProperty("stremiobridge.similar", "true")
    listed = on_watchlist(type_, id_) if type_ in LIBRARY_TYPES else None
    if listed is not None:
        item.setProperty("stremiobridge.watchlist", "true" if listed else "")
        item.setProperty("stremiobridge.watchlist_action", plugin.url_for(
            "info_toggle", what="watchlist", value=int(not listed), **target))


def episode_listitem(meta, video, released=True):
    title = video.title or (L(30065, episode=video.episode) if video.episode is not None else meta.name)
    if video.season is not None and video.episode is not None:
        label = f"{video.season}x{video.episode:02d}. {title}"
    else:
        label = title
    if not released:
        label = f"[COLOR grey]{label}  ({video.air_date})[/COLOR]"

    item = xbmcgui.ListItem(label, offscreen=True)
    art = {"thumb": video.thumbnail or meta.background or meta.poster, "poster": meta.poster,
           "tvshow.poster": meta.poster, "fanart": meta.background, "clearlogo": meta.logo}
    item.setArt({k: v for k, v in art.items() if v})

    tag = item.getVideoInfoTag()
    tag.setMediaType("episode" if video.season is not None else "video")
    tag.setTitle(title)
    tag.setTvShowTitle(meta.name)
    tag.setPlot(video.overview or meta.description)
    if video.season is not None:
        tag.setSeason(video.season)
    if video.episode is not None:
        tag.setEpisode(video.episode)
    if video.air_date:
        tag.setFirstAired(video.air_date)
        tag.setYear(int(video.air_date[:4]))
    if meta.genres:
        tag.setGenres(list(meta.genres))
        set_genres(item, meta.genres)
    _apply_people(tag, meta)
    return item


def single_video_item(plugin, meta, row=None):
    """For metas without videos (or with a defaultVideoId): one playable entry."""
    item = meta_item(meta)
    video_id = meta.default_video_id or meta.id
    apply_watch(item, row)
    add_context_menu(item, item_menu(plugin, meta.type, video_id, bool(row and row.watched), {"meta": meta.id},
                                       show_id=meta.id, trailer=meta.trailer))
    return playable_entry(plugin, item, meta.type, video_id, meta=meta.id)


def playback_item(path, meta, video_id, fallback_title=""):
    """The resolved ListItem: the stream path plus whatever info we have."""
    video = next((v for v in meta.videos if v.id == video_id), None) if meta else None
    if video is not None:
        item = episode_listitem(meta, video)
    elif meta is not None:
        item = meta_item(meta)
    else:
        item = xbmcgui.ListItem(fallback_title, offscreen=True)
        item.getVideoInfoTag().setTitle(fallback_title)
    item.setPath(path)
    return item
