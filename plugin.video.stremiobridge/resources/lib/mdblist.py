"""MDBList: scrobbling and watched-history sync (https://api.mdblist.com).

Authenticates with the user's API key (``?apikey=``). MDBList rate-limits to
300 writes and 1000 reads per 5 minutes per account; scrobbles and syncs here
stay far below that. No Kodi imports, so this is unit-testable.
"""

from datetime import datetime, timezone

from stremio.watchstate import PlaybackEntry

BASE_URL = "https://api.mdblist.com"
TIMEOUT = 15
ITEM_TTL = 12 * 3600  # title lookups (ratings, recommendations) change slowly
BATCH_SIZE = 100
SUPPORTED_IDS = {
    "movie": ("imdb", "tmdb", "trakt", "kitsu", "mdblist"),
    "show": ("imdb", "tmdb", "trakt", "tvdb", "mdblist"),
}
SCROBBLE_EVENTS = ("start", "pause", "stop")


class MDBListError(Exception):
    pass


class MDBListAuthError(MDBListError):
    """The API key was rejected."""


# ---------------------------------------------------------------- ids

def supported_ids(ids, kind):
    return {k: v for k, v in (ids or {}).items() if k in SUPPORTED_IDS[kind] and v not in (None, "")}


def stremio_id(ids):
    """The Stremio id our addons most likely use for these ids (IMDb first)."""
    if ids.get("imdb"):
        return str(ids["imdb"])
    for name in ("tmdb", "tvdb"):
        if ids.get(name):
            return f"{name}:{ids[name]}"
    return None


def _timestamp(value):
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- payloads

def scrobble_payload(entry, progress, app_version=""):
    """Body for /scrobble/*; None if the item has no id MDBList understands."""
    if entry.is_episode:
        ids = supported_ids(entry.ids, "show")
        if not ids:
            return None
        body = {"show": {"ids": ids, "season": {"number": entry.season, "episode": {"number": entry.episode}}}}
    else:
        ids = supported_ids(entry.ids, "movie")
        if not ids:
            return None
        body = {"movie": {"ids": ids}}
    body["progress"] = round(max(0.0, min(100.0, progress)), 2)
    if app_version:
        body["app_version"] = app_version
    return body


def has_ids(entry):
    return bool(supported_ids(entry.ids, "show" if entry.is_episode else "movie"))


def watched_payload(entries, watched_at=None, remove=False):
    """Body for /sync/watched or, with `remove`, /sync/watched/remove. Each
    entry's own ``watched_at`` (epoch seconds) is used when it has one, else
    `watched_at`. Entries without usable ids are skipped."""
    movies, shows = [], {}
    for entry in entries:
        stamp = getattr(entry, "watched_at", 0) or watched_at
        extra = {"watched_at": _iso(stamp)} if stamp and not remove else {}
        if entry.is_episode:
            ids = supported_ids(entry.ids, "show")
            if not ids:
                continue
            show = shows.setdefault(tuple(sorted(ids.items())), {"ids": ids, "seasons": {}})
            show["seasons"].setdefault(entry.season, []).append({"number": entry.episode, **extra})
        else:
            ids = supported_ids(entry.ids, "movie")
            if ids:
                movies.append({"ids": ids, **extra})
    body = {}
    if movies:
        body["movies"] = movies
    if shows:
        body["shows"] = [
            {"ids": show["ids"],
             "seasons": [{"number": number, "episodes": episodes} for number, episodes in show["seasons"].items()]}
            for show in shows.values()
        ]
    return body


def parse_watched(data):
    """/sync/watched response -> ``[(PlaybackEntry, watched_at_epoch)]``."""
    items = []
    for entry in data.get("movies") or []:
        movie = entry.get("movie") or {}
        ids = movie.get("ids") or {}
        video_id = stremio_id(ids)
        if video_id:
            items.append((PlaybackEntry(video_id=video_id, type="movie", title=movie.get("title") or "",
                                        ids=supported_ids(ids, "movie")),
                          _timestamp(entry.get("last_watched_at"))))
    for entry in data.get("episodes") or []:
        episode = entry.get("episode") or {}
        show = episode.get("show") or {}
        show_id = stremio_id(show.get("ids") or {})
        season, number = episode.get("season"), episode.get("number")
        if show_id and isinstance(season, int) and isinstance(number, int):
            items.append((PlaybackEntry(video_id=f"{show_id}:{season}:{number}", type="series", meta_id=show_id,
                                        season=season, episode=number, title=episode.get("title") or "",
                                        show_title=show.get("title") or "",
                                        ids=supported_ids(show.get("ids") or {}, "show")),
                          _timestamp(entry.get("last_watched_at"))))
    return items


def _lookup_path(type_, stremio_item_id):
    """``/{provider}/{movie|show}/{id}`` for a Stremio id, or None."""
    media = "movie" if type_ == "movie" else "show"
    base = stremio_item_id.split(":")[0] if stremio_item_id.startswith("tt") else stremio_item_id
    if base.startswith("tt"):
        return f"/imdb/{media}/{base}"
    for provider in ("tmdb", "tvdb"):
        if base.startswith(provider + ":"):
            return f"/{provider}/{media}/{base.split(':')[1]}"
    return None


def parse_recommendations(data):
    """MDBList ``recommendations`` -> Stremio-style catalog entries (dicts)."""
    items = []
    for rec in (data or {}).get("recommendations") or []:
        ids = rec.get("ids") or {}
        stremio_item_id = stremio_id(ids)
        if not stremio_item_id or not rec.get("title"):
            continue
        items.append({
            "id": stremio_item_id,
            "type": "series" if rec.get("mediatype") == "show" else "movie",
            "name": rec["title"],
            "poster": rec.get("poster") or "",
            "releaseInfo": str(rec.get("release_year") or ""),
            "description": rec.get("description") or "",
        })
    return items


# Each rating source's scale maximum, to bring them all to 0-100.
RATING_SCALES = {
    "imdb": 10,          # IMDb 0-10
    "tomatoes": 100,     # Rotten Tomatoes critics %
    "popcorn": 100,      # Rotten Tomatoes audience %
    "metacritic": 100,   # Metacritic 0-100
    "trakt": 100,        # Trakt %
    "tmdb": 100,         # TMDB %
    "letterboxd": 5,     # Letterboxd 0-5
    "rogerebert": 4,     # Roger Ebert 0-4
    "myanimelist": 10,   # MyAnimeList 0-10
}


def overall_rating(data):
    """``(score_0_to_100, number_of_sources)`` averaged over MDBList's own
    score and every rating source it has a value for; None if there are none."""
    scores = []
    if isinstance((data or {}).get("score"), (int, float)) and data["score"] > 0:
        scores.append(float(data["score"]))  # MDBList's score, already 0-100
    for rating in (data or {}).get("ratings") or []:
        if not isinstance(rating, dict):
            continue
        scale, value = RATING_SCALES.get(rating.get("source")), rating.get("value")
        if scale and isinstance(value, (int, float)) and value > 0:
            scores.append(min(100.0, value * 100.0 / scale))
    if not scores:
        return None
    return sum(scores) / len(scores), len(scores)


def parse_playback(sessions):
    """/sync/playback -> ``[(PlaybackEntry, position_s, duration_s, updated_epoch)]``.
    Sessions without a runtime can't be turned into a position and are skipped."""
    items = []
    for session in sessions if isinstance(sessions, list) else []:
        try:
            progress = float(session.get("progress") or 0)
            duration = int(session.get("runtime") or 0) * 60
            updated = float(session.get("updated_at_ts") or _timestamp(session.get("updated_at")))
        except (TypeError, ValueError):
            continue
        if not duration or progress <= 0:
            continue
        if session.get("type") == "episode":
            episode = session.get("episode") or {}
            show = session.get("show") or episode.get("show") or {}
            show_id = stremio_id(show.get("ids") or {})
            season, number = episode.get("season"), episode.get("number", episode.get("episode"))
            if not (show_id and isinstance(season, int) and isinstance(number, int)):
                continue
            entry = PlaybackEntry(video_id=f"{show_id}:{season}:{number}", type="series", meta_id=show_id,
                                  season=season, episode=number, title=episode.get("title") or "",
                                  show_title=show.get("title") or "", ids=supported_ids(show.get("ids") or {}, "show"))
        else:
            movie = session.get("movie") or {}
            video_id = stremio_id(movie.get("ids") or {})
            if not video_id:
                continue
            entry = PlaybackEntry(video_id=video_id, type="movie", title=movie.get("title") or "",
                                  ids=supported_ids(movie.get("ids") or {}, "movie"))
        items.append((entry, progress / 100.0 * duration, float(duration), updated))
    return items


def parse_watchlist(data):
    """/watchlist/items -> ``[{"type", "id", "title", "year", "ids"}]`` (Stremio ids)."""
    items = []
    for kind, type_ in (("movies", "movie"), ("shows", "series")):
        for entry in (data or {}).get(kind) or []:
            ids = dict(entry.get("ids") or {})
            ids.setdefault("imdb", entry.get("imdb_id"))
            ids.setdefault("tvdb", entry.get("tvdb_id"))
            if type_ == "movie" or ids.get("tmdb") is None:
                ids.setdefault("tmdb", entry.get("id"))
            ids = supported_ids(ids, "movie" if type_ == "movie" else "show")
            video_id = stremio_id(ids)
            if video_id and entry.get("title"):
                items.append({"type": type_, "id": video_id, "title": entry["title"],
                              "year": entry.get("release_year"), "ids": ids,
                              "poster": entry.get("poster") or "", "description": entry.get("description") or ""})
    return items


def watchlist_previews(items):
    """Watchlist items -> Stremio-style catalog entries (for MetaPreview)."""
    return [{"id": i["id"], "type": i["type"], "name": i["title"], "poster": i.get("poster") or "",
             "releaseInfo": str(i.get("year") or ""), "description": i.get("description") or ""} for i in items]


# ---------------------------------------------------------------- client

class MDBListClient:
    def __init__(self, api_key, session=None, timeout=TIMEOUT, base_url=BASE_URL, cache=None):
        """`cache` (a stremio.cache.Cache) keeps title lookups for ITEM_TTL."""
        self.api_key = api_key
        self.cache = cache
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._session = session

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _request(self, method, path, params=None, body=None):
        import requests

        query = {"apikey": self.api_key, **{k: v for k, v in (params or {}).items() if v not in (None, "")}}
        try:
            response = self.session.request(method, self.base_url + path, params=query, json=body,
                                            timeout=self.timeout)
        except requests.RequestException as exc:
            raise MDBListError(f"MDBList request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise MDBListAuthError("MDBList rejected the API key")
        if response.status_code >= 400:
            raise MDBListError(f"MDBList {path}: HTTP {response.status_code} {response.text[:200]}")
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise MDBListError(f"MDBList {path}: invalid JSON") from exc

    def item(self, type_, stremio_item_id):
        """Title data (ratings, recommendations, ...) for a movie/show; {} if the
        id can't be looked up on MDBList."""
        path = _lookup_path(type_, stremio_item_id)
        if path is None:
            return {}
        key = "mdblist:" + path
        cached = self.cache.get(key) if self.cache is not None else None
        if cached:
            return cached[0]
        data = self._request("GET", path, params={"append_to_response": "recommendations"})
        if self.cache is not None and isinstance(data, dict):
            self.cache.set(key, data, ITEM_TTL)
        return data if isinstance(data, dict) else {}

    def recommendations(self, type_, stremio_item_id):
        """Similar titles for a movie/show, as Stremio-style catalog entries."""
        return parse_recommendations(self.item(type_, stremio_item_id))

    def last_activities(self):
        """Also serves as a check that the API key works."""
        return self._request("GET", "/sync/last_activities")

    def watchlist(self, limit=1000):
        """Everything on the user's MDBList watchlist (all pages)."""
        return parse_watchlist(self._paged("/watchlist/items", limit, append_to_response="poster,description"))

    def watchlist_add(self, type_, ids):
        self._watchlist_change("add", type_, ids)

    def watchlist_remove(self, type_, ids):
        self._watchlist_change("remove", type_, ids)

    def _watchlist_change(self, action, type_, ids):
        kind = "movie" if type_ == "movie" else "show"
        usable = supported_ids(ids, kind)
        if not usable:
            raise MDBListError("No IMDb/TMDB/TVDB id to identify this title on MDBList")
        self._request("POST", f"/watchlist/items/{action}", body={kind + "s": [{"ids": usable}]})

    def playback(self):
        """Paused playback sessions (resume points) from all devices."""
        return self._request("GET", "/sync/playback")

    def scrobble(self, event, payload):
        if event not in SCROBBLE_EVENTS:
            raise ValueError(event)
        return self._request("POST", f"/scrobble/{event}", body=payload)

    def _paged(self, path, limit, **params):
        """GET every page of a cursor-paginated endpoint; list fields merged."""
        merged, cursor = {}, None
        while True:
            data = self._request("GET", path, params={"limit": limit, "cursor": cursor, **params})
            if not isinstance(data, dict):
                break
            for key, value in data.items():
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
            cursor = (data.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
        return merged

    def watched(self, limit=1000):
        """The full watched history (all pages merged)."""
        return self._paged("/sync/watched", limit)

    def add_watched(self, entries, watched_at=None):
        for i in range(0, len(entries), BATCH_SIZE):
            body = watched_payload(entries[i:i + BATCH_SIZE], watched_at)
            if body:
                self._request("POST", "/sync/watched", body=body)

    def remove_watched(self, entries):
        for i in range(0, len(entries), BATCH_SIZE):
            body = watched_payload(entries[i:i + BATCH_SIZE], remove=True)
            if body:
                self._request("POST", "/sync/watched/remove", body=body)


# ---------------------------------------------------------------- sync

def _activity_marker(activities):
    """What changed-detection compares: everything except the server clock."""
    return {k: v for k, v in (activities or {}).items() if k != "server_time"}


def sync(client, state, previous_activities=None, force=False):
    """Two-way watched sync.

    1. Push locally watched items MDBList hasn't confirmed yet.
    2. If MDBList's activity markers changed since `previous_activities` (or
       `force`), pull the full watched list and merge it (see
       ``WatchState.merge_remote``).

    Returns ``(summary, activities)``; store `activities` for next time.
    """
    summary = {"pushed": 0, "added": 0, "removed": 0, "removal_skipped": False, "pulled": False, "resumes": 0}
    pending = state.unsynced_watched()
    if pending:
        pushable = [r for r in pending if has_ids(r)]
        client.add_watched(pushable)
        state.mark_synced([r.video_id for r in pending])  # unpushable ones never will be
        summary["pushed"] = len(pushable)

    activities = client.last_activities()
    if force or pending or _activity_marker(activities) != _activity_marker(previous_activities):
        added, removed, skipped = state.merge_remote(
            parse_watched(client.watched()), protect={r.video_id for r in pending})
        summary.update(added=added, removed=removed, removal_skipped=skipped, pulled=True)
    # Resume points from other devices (cheap; not covered by the activity markers).
    summary["resumes"] = state.merge_resume(parse_playback(client.playback()))
    return summary, activities
