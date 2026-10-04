"""Stremio objects -> Kodi ListItems."""

import xbmc
import xbmcgui

from stremio.models import CAST

from .common import ADDON, L, add_context_menu, skin_active

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
    if ADDON.getSettingBool("select_opens_info") and (type_ == "movie" or (show_id and show_id != video_id)):
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
    if preview.genres:
        tag.setGenres(list(preview.genres))
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


def item_menu(plugin, type_, id_, watched, watched_params=None, show_id=None, trailer="", browse_show=False,
              partly_watched=False):
    """The context menu every playable/browsable item gets. `show_id` is the
    show an episode belongs to (cast, trailer and "Browse show" use it).
    `partly_watched` (a show with some episodes watched) offers both marks."""
    owner = show_id or id_
    params = watched_params or {}
    # With our skin, Kodi's own Mark as watched covers movies and episodes (the
    # service passes it on); it doesn't offer it for shows and seasons.
    kodi_marks = skin_active() and (type_ in PLAYABLE_TYPES or bool(show_id) or bool(params.get("meta")))
    menu = [] if kodi_marks else [watched_menu(plugin, watched, type_, id_, **params)]
    if partly_watched and not watched:
        menu.append(watched_menu(plugin, True, type_, id_, **(watched_params or {})))
    if browse_show and show_id:
        menu.append((L(30064), f"ActivateWindow(Videos,{plugin.url_for('meta', type=type_, id=show_id)},return)"))
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
    if row is None:
        return
    tag = item.getVideoInfoTag()
    tag.setPlaycount(1 if row.watched else 0)
    if row.position > 0 and row.duration > 0:
        tag.setResumePoint(row.position, row.duration)


def watched_menu(plugin, watched, type_, id_, **params):
    """Context-menu entry toggling watched state (see the set_watched route)."""
    label = L(30191) if watched else L(30190)
    return label, plugin.run_url("set_watched", type=type_, id=id_, value=int(not watched), **params)


def preview_item(plugin, preview, row=None, started=False, progress=None):
    """Returns ``(url, ListItem, is_folder)`` for a catalog entry. `row` is its
    watch state (movies), if any; `started` means some of the show is watched;
    `progress` is ``(watched, aired)`` episodes for a started show."""
    item = xbmcgui.ListItem(preview.name)
    _apply_preview(item, preview)
    apply_watch(item, row)
    if progress:
        watched, aired = progress
        item.setLabel2(L(30321, watched=watched, total=aired))
        item.setProperty("TotalEpisodes", str(aired))
        item.setProperty("WatchedEpisodes", str(watched))
        item.setProperty("UnWatchedEpisodes", str(max(0, aired - watched)))
        if aired and watched >= aired:
            item.getVideoInfoTag().setPlaycount(1)
    add_context_menu(item, item_menu(plugin, preview.type, preview.id, bool(row and row.watched),
                                       trailer=preview.trailer, partly_watched=started))

    if preview.type in PLAYABLE_TYPES:
        return playable_entry(plugin, item, preview.type, preview.id)
    return plugin.url_for("meta", type=preview.type, id=preview.id), item, True


def meta_item(meta):
    """A ListItem carrying everything we know about `meta` (for the info dialog)."""
    item = xbmcgui.ListItem(meta.name)
    _apply_meta(item, meta)
    return item


def season_label(season):
    return L(30061) if season == 0 else L(30060, season=season)


def season_item(plugin, meta, season, episode_count, watched_count=0):
    item = xbmcgui.ListItem(season_label(season))
    art = {"poster": meta.poster, "tvshow.poster": meta.poster, "fanart": meta.background,
           "clearlogo": meta.logo}
    item.setArt({k: v for k, v in art.items() if v})
    tag = item.getVideoInfoTag()
    tag.setMediaType("season")
    tag.setTvShowTitle(meta.name)
    tag.setSeason(season)
    tag.setTitle(season_label(season))
    tag.setPlot(meta.description)
    item.setProperty("TotalEpisodes", str(episode_count))
    item.setProperty("WatchedEpisodes", str(watched_count))
    item.setProperty("UnWatchedEpisodes", str(max(0, episode_count - watched_count)))
    all_watched = episode_count > 0 and watched_count >= episode_count
    tag.setPlaycount(1 if all_watched else 0)
    add_context_menu(item, item_menu(plugin, meta.type, meta.id, all_watched, {"season": season},
                                       trailer=meta.trailer))
    url = plugin.url_for("season", type=meta.type, id=meta.id, season=season)
    return url, item, True


def episode_item(plugin, meta, video, released=True, row=None, browse_show=True):
    """A playable episode (or channel video). Unreleased ones are shown greyed out."""
    item = episode_listitem(meta, video, released)
    apply_watch(item, row)
    add_context_menu(item, item_menu(plugin, meta.type, video.id, bool(row and row.watched), {"meta": meta.id},
                                       show_id=meta.id, trailer=meta.trailer, browse_show=browse_show))
    return playable_entry(plugin, item, meta.type, video.id, meta=meta.id)


def episode_listitem(meta, video, released=True):
    title = video.title or (L(30065, episode=video.episode) if video.episode is not None else meta.name)
    if video.season is not None and video.episode is not None:
        label = f"{video.season}x{video.episode:02d}. {title}"
    else:
        label = title
    if not released:
        label = f"[COLOR grey]{label}  ({video.air_date})[/COLOR]"

    item = xbmcgui.ListItem(label)
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
        item = xbmcgui.ListItem(fallback_title)
        item.getVideoInfoTag().setTitle(fallback_title)
    item.setPath(path)
    return item
