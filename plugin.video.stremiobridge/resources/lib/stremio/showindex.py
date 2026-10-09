"""A long-running show's meta, split up in the cache so the busy paths can read
only what they need.

A soap's meta is thousands of episodes; reading all of it took about a second
on a Fire TV Stick, and the info page reads it in each of its lists. Next to
the addon's cached response (keyed by its URL) this keeps:

- an index: the meta without its videos, and each video's id, season, episode
  and release date: enough for seasons, watched counts and what to play next;
- each season's videos in full, for the seasons that are shown or played.

They expire at the same moment as the response they were made from, so they're
used only while that response is the one in the cache: when it's refreshed
(or missing, or expired), callers read the full meta, which makes them again.
"""


from .models import Meta, Video
from .record import dataclass, replace

# Shows with fewer videos are read whole: their meta is small enough.
MIN_VIDEOS = 300


def index_key(url):
    return f"showindex:{url}"


def season_key(url, season):
    return f"showseason:{'' if season is None else season}:{url}"


def is_current(cache, url):
    """Whether the index for `url` (a cached meta response) matches the response."""
    expires = cache.expires(url)
    return expires is not None and cache.expires(index_key(url)) == expires


def save(cache, url, raw):
    """Make the index and seasons of `raw`, the meta dict in `url`'s cached
    response. False if that response isn't cached."""
    expires = cache.expires(url)
    if expires is None:
        return False
    index, seasons = [], {}
    for data in raw.get("videos") or []:
        video = Video.from_dict(data)
        if video is None:
            continue
        index.append([video.id, video.season, video.episode, video.released])
        seasons.setdefault(video.season, []).append(data)
    for season, videos in seasons.items():
        cache.set_until(season_key(url, season), videos, expires)
    # Written last: a current index vouches for the seasons
    cache.set_until(index_key(url), {"meta": {k: v for k, v in raw.items() if k != "videos"}, "index": index},
                    expires)
    return True


@dataclass
class ShowIndex:
    url: str
    summary: dict  # the addon's meta dict, without videos
    meta: Meta     # every video, with only its id, season, episode and release date

    def raw_season(self, cache, season):
        """The addon's dicts for one season's videos, or None if they're gone."""
        key = season_key(self.url, season)
        if cache.expires(key) != cache.expires(self.url):
            return None
        hit = cache.get(key, allow_stale=True)
        return hit[0] if hit else None

    def with_seasons(self, cache, seasons):
        """`meta` with these seasons' videos in full (titles, plots, pictures),
        or None if any of them are gone."""
        detailed = {}
        for season in set(seasons):
            videos = self.raw_season(cache, season)
            if videos is None:
                return None
            detailed.update((v.id, v) for v in (Video.from_dict(d) for d in videos) if v)
        if not detailed:
            return self.meta
        return replace(self.meta, videos=tuple(detailed.get(v.id, v) for v in self.meta.videos))


def load(cache, url, type_, now):
    """The ShowIndex of `url`'s cached meta response, or None if it has none or
    the response isn't fresh (callers then read the full meta, which refreshes it)."""
    expires = cache.expires(url)
    if expires is None or expires <= now or cache.expires(index_key(url)) != expires:
        return None
    hit = cache.get(index_key(url), allow_stale=True)
    if not hit:
        return None
    summary, index = hit[0]["meta"], hit[0]["index"]
    meta = Meta.from_dict(dict(summary, videos=[]), type_)
    if meta is None:
        return None
    videos = tuple(Video(id_, season=season, episode=episode, released=released)
                   for id_, season, episode, released in index)
    return ShowIndex(url, summary, replace(meta, videos=videos))
