"""What the menus show: feeds (cached), My Podcasts and My Latest Episodes."""

from concurrent.futures import ThreadPoolExecutor

from . import PodcastError, feed
from .models import Episode, Podcast
from .store import find

FEED_WORKERS = 8
LATEST_DAYS = 30  # a podcast you've never played shows its newest episode only if it's this recent


class Library:
    def __init__(self, store, directory, feed_ttl=1800, latest_days=LATEST_DAYS, log=lambda msg: None,
                 fetch_feed=None):
        self.store = store
        self.directory = directory
        self.feed_ttl = feed_ttl
        self.latest_days = latest_days
        self.log = log
        self._fetch_feed = fetch_feed or (lambda url: feed.fetch(url))

    # ------------------------------------------------------------ podcasts

    def feed(self, feed_url, refresh=False):
        """``(Podcast, [Episode])``, newest episode first."""
        key = f"feed:{feed_url}"
        if not refresh:
            cached = self.store.get(key)
            if cached is not None:
                return Podcast.from_dict(cached["podcast"]), [Episode.from_dict(e) for e in cached["episodes"]]
        podcast, episodes = self._fetch_feed(feed_url)
        if self.feed_ttl:
            self.store.set(key, {"podcast": podcast.to_dict(), "episodes": [e.to_dict() for e in episodes]},
                           self.feed_ttl)
        return podcast, episodes

    def resolve(self, feed_url="", apple_id=""):
        """A Podcast with a feed URL, from either; chart entries only have Apple's id."""
        if feed_url:
            return Podcast(title="", feed_url=feed_url, apple_id=apple_id)
        found = self.directory.lookup([apple_id]) if apple_id else []
        if not found or not found[0].feed_url:
            raise PodcastError("Apple doesn't list a feed for this podcast")
        return found[0]

    def episode(self, feed_url, key):
        _, episodes = self.feed(feed_url)
        match = next((e for e in episodes if e.key == key or e.url == key), None)
        if match is None:  # maybe new since the feed was cached
            _, episodes = self.feed(feed_url, refresh=True)
            match = next((e for e in episodes if e.key == key or e.url == key), None)
        if match is None:
            raise PodcastError("This episode is no longer in the podcast's feed")
        return match

    def _feeds(self, feed_urls):
        """``{feed_url: (Podcast, [Episode])}`` fetched in parallel; failures are logged and left out."""
        def load(url):
            try:
                return url, self.feed(url)
            except PodcastError as exc:
                self.log(f"Skipping {url}: {exc}")
                return url, None

        urls = list(dict.fromkeys(feed_urls))
        if not urls:
            return {}
        with ThreadPoolExecutor(max_workers=min(FEED_WORKERS, len(urls))) as pool:
            return {url: result for url, result in pool.map(load, urls) if result is not None}

    def my_podcasts(self):
        """Subscriptions as Podcasts. Ones synced from another device arrive as
        a bare URL: their title and artwork come from the feed, once."""
        subscriptions = self.store.subscriptions()
        missing = [s["feed_url"] for s in subscriptions if not s["title"] or not s["image"]]
        for url, (podcast, _) in self._feeds(missing).items():
            self.store.update_details(podcast)
        if missing:
            subscriptions = self.store.subscriptions()
        return [Podcast(title=s["title"] or s["feed_url"], feed_url=s["feed_url"], author=s["author"],
                        image=s["image"], apple_id=s["apple_id"]) for s in subscriptions]

    # ------------------------------------------------------------ My Latest Episodes

    def latest(self, now):
        """``[(Episode, progress row or None)]``: episodes you're part-way
        through (most recent first), then for each other subscription the next
        episode by release date after the newest one you've played (Up Next).
        A podcast you've never played shows its newest episode if it's recent."""
        started = self.store.in_progress()
        subscribed = [s["feed_url"] for s in self.store.subscriptions()]
        feeds = self._feeds(subscribed + [r["feed_url"] for r in started if not r["title"]])
        results = []

        for row in started:
            episode = self._episode_from_row(row, feeds.get(row["feed_url"]))
            if episode is not None:
                results.append((episode, row))
        resuming = {episode.feed_url for episode, _ in results}

        upcoming = []
        for url in subscribed:
            if url not in feeds or url in resuming:  # its resume entry above comes first
                continue
            episode = self._next_episode(feeds[url][1], self.store.progress_for(url), now)
            if episode is not None:
                upcoming.append((episode, None))
        upcoming.sort(key=lambda pair: pair[0].published, reverse=True)
        return results + upcoming

    def _next_episode(self, episodes, rows, now):
        """`episodes` are newest first. None when you're up to date."""
        def listened(episode):
            row = find(rows, episode)
            return bool(row and (row["played"] or row["position"]))

        newest_listened = next((i for i, e in enumerate(episodes) if listened(e)), None)
        if newest_listened is None:
            recent = episodes and episodes[0].published >= now - self.latest_days * 86400
            return episodes[0] if recent else None
        # Walk forward in release order from there: the first one not listened to.
        return next((e for e in reversed(episodes[:newest_listened]) if not listened(e)), None)

    def unplayed_podcasts(self):
        """Subscriptions you haven't played any episode of yet."""
        listened = self.store.listened_feeds()
        return [p for p in self.my_podcasts() if p.feed_url not in listened]

    @staticmethod
    def _episode_from_row(row, loaded):
        if loaded is not None:
            podcast, episodes = loaded
            for episode in episodes:
                if episode.key == row["key"] or (row["url"] and episode.url == row["url"]) or \
                        (row["guid"] and episode.guid == row["guid"]):
                    return episode
        if not row["url"]:
            return None
        return Episode(feed_url=row["feed_url"], guid=row["guid"], title=row["title"] or row["url"].rsplit("/", 1)[-1],
                       url=row["url"], image=row["image"], published=row["published"], duration=row["duration"],
                       podcast_title=row["podcast_title"] or (loaded[0].title if loaded else ""))
