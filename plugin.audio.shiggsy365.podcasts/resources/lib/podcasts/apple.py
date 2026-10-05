"""Apple's podcast directory: search, lookup and charts. None of it needs a key.

Search and lookup are the iTunes Search API. The charts (Top Podcasts, and
the per-genre charts behind Trending) come from the older iTunes RSS feed,
the only one that still takes a genre; Apple's current chart feed is the
fallback for Top Podcasts.
"""

import re
from urllib.parse import quote, urlencode

from . import PodcastError, http
from .models import Podcast

SEARCH_URL = "https://itunes.apple.com"
CHARTS_URL = "https://rss.marketingtools.apple.com"
GENRE_CHARTS_URL = "https://itunes.apple.com"

# Apple's top-level podcast genres (id, name).
GENRES = [
    (1301, "Arts"), (1321, "Business"), (1303, "Comedy"), (1304, "Education"), (1483, "Fiction"),
    (1511, "Government"), (1512, "Health & Fitness"), (1487, "History"), (1305, "Kids & Family"),
    (1502, "Leisure"), (1310, "Music"), (1489, "News"), (1314, "Religion & Spirituality"),
    (1533, "Science"), (1324, "Society & Culture"), (1545, "Sports"), (1318, "Technology"),
    (1488, "True Crime"), (1309, "TV & Film"),
]

CHART_LIMIT = 100
SEARCH_LIMIT = 50
LOOKUP_BATCH = 150  # ids per lookup request

CHART_TTL = 6 * 3600
SEARCH_TTL = 3600
LOOKUP_TTL = 7 * 86400


def artwork(url, size=600):
    """Apple artwork URLs name their size (".../100x100bb.jpg"); ask for a bigger one."""
    return re.sub(r"/\d+x\d+bb\.(jpg|png|webp)$", rf"/{size}x{size}bb.\1", url or "")


class Directory:
    def __init__(self, country="gb", cache=None, timeout=15):
        self.country = (country or "gb").lower()
        self.cache = cache
        self.timeout = timeout

    def _get(self, url, ttl):
        if self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None:
                return cached
        data = http.get_json(url, timeout=self.timeout)
        if self.cache is not None and ttl:
            self.cache.set(url, data, ttl)
        return data

    def search(self, term):
        query = urlencode({"term": term.strip(), "media": "podcast", "entity": "podcast",
                           "country": self.country.upper(), "limit": SEARCH_LIMIT})
        data = self._get(f"{SEARCH_URL}/search?{query}", SEARCH_TTL)
        return [p for p in map(_from_lookup, (data or {}).get("results") or []) if p]

    def lookup(self, apple_ids):
        """Podcasts for the ids that Apple still lists, in the order given."""
        ids = [str(i) for i in apple_ids if str(i).isdigit()]
        found = {}
        for start in range(0, len(ids), LOOKUP_BATCH):
            batch = ids[start:start + LOOKUP_BATCH]
            query = urlencode({"id": ",".join(batch), "entity": "podcast", "country": self.country.upper()})
            data = self._get(f"{SEARCH_URL}/lookup?{query}", LOOKUP_TTL)
            for podcast in map(_from_lookup, (data or {}).get("results") or []):
                if podcast:
                    found[podcast.apple_id] = podcast
        return [found[i] for i in ids if i in found]

    def top(self):
        """Apple's top podcasts chart. Chart entries have no feed URL: it's
        looked up when one is opened. The older RSS feed answers faster than
        Apple's current chart host (which can take seconds), so it comes first."""
        try:
            return self._rss_chart(f"{GENRE_CHARTS_URL}/{quote(self.country)}/rss/toppodcasts/limit={CHART_LIMIT}/json")
        except PodcastError:
            pass
        url = f"{CHARTS_URL}/api/v2/{quote(self.country)}/podcasts/top/{CHART_LIMIT}/podcasts.json"
        results = ((self._get(url, CHART_TTL) or {}).get("feed") or {}).get("results") or []
        return [Podcast(title=r.get("name") or "", author=r.get("artistName") or "",
                        image=artwork(r.get("artworkUrl100")), apple_id=str(r.get("id") or ""),
                        genre=", ".join(g.get("name", "") for g in r.get("genres") or [] if g.get("name") != "Podcasts"))
                for r in results if str(r.get("id") or "").isdigit()]

    def genre_chart(self, genre_id):
        return self._rss_chart(
            f"{GENRE_CHARTS_URL}/{quote(self.country)}/rss/toppodcasts/limit={CHART_LIMIT}/genre={int(genre_id)}/json")

    def _rss_chart(self, url):
        entries = ((self._get(url, CHART_TTL) or {}).get("feed") or {}).get("entry") or []
        if isinstance(entries, dict):  # a chart of one
            entries = [entries]
        return [p for p in map(_from_rss_entry, entries) if p]


def _from_lookup(result):
    if not result.get("collectionId") or result.get("kind") not in (None, "podcast"):
        return None
    return Podcast(
        title=result.get("collectionName") or result.get("trackName") or "",
        feed_url=result.get("feedUrl") or "",
        author=result.get("artistName") or "",
        image=result.get("artworkUrl600") or artwork(result.get("artworkUrl100")),
        apple_id=str(result["collectionId"]),
        genre=result.get("primaryGenreName") or "",
    )


def _label(entry, key):
    return ((entry.get(key) or {}).get("label") or "").strip()


def _from_rss_entry(entry):
    apple_id = ((entry.get("id") or {}).get("attributes") or {}).get("im:id") or ""
    if not apple_id.isdigit():
        return None
    images = entry.get("im:image") or []
    image = images[-1].get("label") if images else ""
    return Podcast(
        title=_label(entry, "im:name"),
        author=_label(entry, "im:artist"),
        image=artwork(image),
        apple_id=apple_id,
        genre=((entry.get("category") or {}).get("attributes") or {}).get("label") or "",
        description=_label(entry, "summary"),
    )
