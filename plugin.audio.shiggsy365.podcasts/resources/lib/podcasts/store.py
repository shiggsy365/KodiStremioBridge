"""Local state in one SQLite file: subscriptions, episode progress, sync
bookmarks and a small response cache.

Local state is the source of truth; gPodder sync (sync.py) pushes the rows
marked dirty and merges what other devices did, newest change winning.
"""

import json
import sqlite3
import time
from contextlib import closing

PLAYED_RATIO = 0.95       # this far through counts as played
PLAYED_REMAINING = 60     # ...as does stopping with less than this many seconds left
MIN_RESUME = 30           # shorter listens don't save a resume point

PLAYED, RESUME = "played", "resume"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, expires REAL, value TEXT);
CREATE TABLE IF NOT EXISTS subscriptions (
    feed_url TEXT PRIMARY KEY, title TEXT DEFAULT '', author TEXT DEFAULT '', image TEXT DEFAULT '',
    apple_id TEXT DEFAULT '', added INTEGER DEFAULT 0, active INTEGER DEFAULT 1, dirty INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS progress (
    feed_url TEXT, key TEXT, guid TEXT DEFAULT '', url TEXT DEFAULT '', title TEXT DEFAULT '',
    podcast_title TEXT DEFAULT '', image TEXT DEFAULT '', published INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0, duration INTEGER DEFAULT 0, played INTEGER DEFAULT 0,
    updated INTEGER DEFAULT 0, dirty INTEGER DEFAULT 0, PRIMARY KEY (feed_url, key));
CREATE INDEX IF NOT EXISTS progress_url ON progress (url);
CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT);
"""


def is_played(position, duration):
    return bool(duration) and (position >= duration * PLAYED_RATIO or duration - position < PLAYED_REMAINING)


def find(rows, episode):
    """`episode`'s row in a `progress_for` result: by key, else by audio URL."""
    row = rows.get(episode.key)
    if row is None and episode.url:
        row = rows.get(episode.url) or next((r for r in rows.values() if r["url"] == episode.url), None)
    return row


class Store:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return _Connection(db)

    def _now(self):
        return int(self.clock())

    # ------------------------------------------------------------ cache

    def get(self, key):
        with self._db() as db:
            row = db.execute("SELECT expires, value FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None or row["expires"] < self.clock():
            return None
        return json.loads(row["value"])

    def set(self, key, value, ttl):
        with self._db() as db:
            db.execute("REPLACE INTO cache VALUES (?, ?, ?)", (key, self.clock() + ttl, json.dumps(value)))

    def forget(self, key):
        with self._db() as db:
            db.execute("DELETE FROM cache WHERE key = ?", (key,))

    def purge(self):
        with self._db() as db:
            db.execute("DELETE FROM cache WHERE expires < ?", (self.clock(),))

    # ------------------------------------------------------------ subscriptions

    def subscriptions(self):
        """Active subscriptions, most recently added first."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM subscriptions WHERE active = 1 ORDER BY added DESC, title").fetchall()
        return [dict(r) for r in rows]

    def is_subscribed(self, feed_url):
        with self._db() as db:
            row = db.execute("SELECT active FROM subscriptions WHERE feed_url = ?", (feed_url,)).fetchone()
        return bool(row and row["active"])

    def subscribe(self, podcast, dirty=True):
        with self._db() as db:
            db.execute(
                """INSERT INTO subscriptions (feed_url, title, author, image, apple_id, added, active, dirty)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT (feed_url) DO UPDATE SET active = 1, dirty = excluded.dirty, added = excluded.added,
                     title = COALESCE(NULLIF(excluded.title, ''), title),
                     author = COALESCE(NULLIF(excluded.author, ''), author),
                     image = COALESCE(NULLIF(excluded.image, ''), image),
                     apple_id = COALESCE(NULLIF(excluded.apple_id, ''), apple_id)""",
                (podcast.feed_url, podcast.title, podcast.author, podcast.image, podcast.apple_id,
                 self._now(), int(dirty)))

    def unsubscribe(self, feed_url, dirty=True):
        with self._db() as db:
            db.execute("UPDATE subscriptions SET active = 0, dirty = ? WHERE feed_url = ?", (int(dirty), feed_url))

    def update_details(self, podcast):
        """Fill in a subscription's title and artwork (gPodder only syncs the URL)."""
        with self._db() as db:
            db.execute(
                """UPDATE subscriptions SET title = COALESCE(NULLIF(?, ''), title),
                     author = COALESCE(NULLIF(?, ''), author), image = COALESCE(NULLIF(?, ''), image)
                   WHERE feed_url = ?""",
                (podcast.title, podcast.author, podcast.image, podcast.feed_url))

    def pending_subscriptions(self):
        """``(added, removed)`` feed URLs not yet pushed."""
        with self._db() as db:
            rows = db.execute("SELECT feed_url, active FROM subscriptions WHERE dirty = 1").fetchall()
        return [r["feed_url"] for r in rows if r["active"]], [r["feed_url"] for r in rows if not r["active"]]

    def subscriptions_pushed(self, added, removed):
        with self._db() as db:
            db.executemany("UPDATE subscriptions SET dirty = 0 WHERE feed_url = ? AND active = 1",
                           [(u,) for u in added])
            db.executemany("DELETE FROM subscriptions WHERE feed_url = ? AND active = 0", [(u,) for u in removed])

    def rename_feeds(self, pairs):
        """Servers may hand back a cleaned-up URL for one we sent: ``[(old, new)]``."""
        with self._db() as db:
            for old, new in pairs:
                if old and new and old != new:
                    db.execute("UPDATE OR IGNORE subscriptions SET feed_url = ? WHERE feed_url = ?", (new, old))
                    db.execute("UPDATE OR IGNORE progress SET feed_url = ? WHERE feed_url = ?", (new, old))

    # ------------------------------------------------------------ progress

    def progress_for(self, feed_url):
        """``{episode key: row}`` for one podcast."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM progress WHERE feed_url = ?", (feed_url,)).fetchall()
        return {r["key"]: dict(r) for r in rows}

    def progress(self, feed_url, key):
        with self._db() as db:
            row = db.execute("SELECT * FROM progress WHERE feed_url = ? AND key = ?", (feed_url, key)).fetchone()
        return dict(row) if row else None

    def record(self, episode, position, duration):
        """Save where playback of `episode` stopped. Returns PLAYED, RESUME or
        None (too short to count, nothing saved)."""
        position, duration = int(position), int(duration or episode.duration or 0)
        if is_played(position, duration):
            self._write(episode, duration, duration, True)
            return PLAYED
        if position >= MIN_RESUME:
            self._write(episode, position, duration, False)
            return RESUME
        return None

    def set_played(self, episode, played):
        """Mark played, or unplayed (which also forgets the resume point)."""
        duration = episode.duration or (self.progress(episode.feed_url, episode.key) or {}).get("duration") or 0
        self._write(episode, duration if played else 0, duration, played)

    def _write(self, episode, position, duration, played, updated=None, dirty=True):
        with self._db() as db:
            # A sync may have stored this episode under its audio URL (or guid) already.
            existing = db.execute("SELECT key FROM progress WHERE feed_url = ? AND key IN (?, ?) "
                                  "ORDER BY key = ? DESC LIMIT 1",
                                  (episode.feed_url, episode.key, episode.url, episode.key)).fetchone()
            key = existing["key"] if existing else episode.key
            db.execute(
                """INSERT INTO progress (feed_url, key, guid, url, title, podcast_title, image, published,
                                         position, duration, played, updated, dirty)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (feed_url, key) DO UPDATE SET
                     guid = COALESCE(NULLIF(excluded.guid, ''), guid), url = COALESCE(NULLIF(excluded.url, ''), url),
                     title = COALESCE(NULLIF(excluded.title, ''), title),
                     podcast_title = COALESCE(NULLIF(excluded.podcast_title, ''), podcast_title),
                     image = COALESCE(NULLIF(excluded.image, ''), image),
                     published = MAX(excluded.published, published),
                     position = excluded.position, duration = MAX(excluded.duration, 0),
                     played = excluded.played, updated = excluded.updated, dirty = excluded.dirty""",
                (episode.feed_url, key, episode.guid, episode.url, episode.title, episode.podcast_title,
                 episode.image, episode.published, int(position), int(duration), int(played),
                 self._now() if updated is None else int(updated), int(dirty)))

    def in_progress(self):
        """Started, unfinished episodes, most recently played first."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM progress WHERE played = 0 AND position > 0 "
                              "ORDER BY updated DESC").fetchall()
        return [dict(r) for r in rows]

    def listened_feeds(self):
        """Feed URLs with at least one played or started episode."""
        with self._db() as db:
            rows = db.execute("SELECT DISTINCT feed_url FROM progress WHERE played = 1 OR position > 0").fetchall()
        return {r["feed_url"] for r in rows}

    def dirty_progress(self):
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM progress WHERE dirty = 1").fetchall()]

    def progress_pushed(self, rows):
        """Clear the dirty flag, unless the row changed again since it was read."""
        with self._db() as db:
            db.executemany("UPDATE progress SET dirty = 0 WHERE feed_url = ? AND key = ? AND updated = ?",
                           [(r["feed_url"], r["key"], r["updated"]) for r in rows])

    def apply_remote(self, feed_url, url, guid, position, duration, timestamp):
        """Merge another device's play position. Applied only if it's newer
        than what this device has; returns whether it was."""
        position, duration, timestamp = int(position or 0), int(duration or 0), int(timestamp)
        played = is_played(position, duration)
        if played:
            position = duration
        with self._db() as db:
            row = None
            if guid:
                row = db.execute("SELECT * FROM progress WHERE feed_url = ? AND (guid = ? OR key = ?)",
                                 (feed_url, guid, guid)).fetchone()
            if row is None and url:
                row = db.execute("SELECT * FROM progress WHERE feed_url = ? AND (url = ? OR key = ?)",
                                 (feed_url, url, url)).fetchone()
            if row is None:
                db.execute("INSERT INTO progress (feed_url, key, guid, url, position, duration, played, updated) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (feed_url, guid or url, guid or "", url or "", position, duration, int(played), timestamp))
                return True
            if row["updated"] >= timestamp:
                return False
            db.execute("UPDATE progress SET position = ?, duration = ?, played = ?, updated = ?, dirty = 0, "
                       "guid = COALESCE(NULLIF(guid, ''), ?), url = COALESCE(NULLIF(url, ''), ?) "
                       "WHERE feed_url = ? AND key = ?",
                       (position, duration or row["duration"], int(played), timestamp, guid or "", url or "",
                        feed_url, row["key"]))
            return True

    # ------------------------------------------------------------ sync bookmarks

    def get_state(self, name, default=None):
        with self._db() as db:
            row = db.execute("SELECT value FROM state WHERE name = ?", (name,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, name, value):
        with self._db() as db:
            db.execute("REPLACE INTO state VALUES (?, ?)", (name, json.dumps(value)))


class _Connection:
    """sqlite3's own context manager commits but doesn't close."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, *exc):
        with closing(self.db):
            if exc_type is None:
                self.db.commit()
            else:
                self.db.rollback()
        return False
