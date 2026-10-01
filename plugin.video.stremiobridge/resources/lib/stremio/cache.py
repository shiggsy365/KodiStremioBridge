"""A small sqlite key/value cache with per-entry expiry.

Kodi runs every plugin call (and every widget) in its own process, so this has
to cope with several processes using the database at once: WAL mode plus a
busy timeout, and cache failures are logged and ignored rather than raised.
"""

import json
import os
import sqlite3
import threading
import time

# Expired entries are kept this long so they can still be served when an addon is down.
KEEP_STALE = 7 * 86400


class Cache:
    def __init__(self, path, log=None, clock=time.time):
        self._log = log or (lambda msg: None)
        self._clock = clock
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(key TEXT PRIMARY KEY, expires REAL NOT NULL, value TEXT NOT NULL)"
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS cache_expires ON cache (expires)")
        except sqlite3.Error as exc:
            self._log(f"Cache init failed: {exc}")

    def get(self, key, allow_stale=False):
        """``(value, is_fresh)``, or None if missing (or expired and not `allow_stale`)."""
        try:
            with self._lock:
                row = self._db.execute("SELECT expires, value FROM cache WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as exc:
            self._log(f"Cache read failed: {exc}")
            return None
        if row is None:
            return None
        fresh = row[0] > self._clock()
        if not fresh and not allow_stale:
            return None
        return json.loads(row[1]), fresh

    def set(self, key, value, ttl):
        try:
            with self._lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO cache (key, expires, value) VALUES (?, ?, ?)",
                    (key, self._clock() + ttl, json.dumps(value)),
                )
        except sqlite3.Error as exc:
            self._log(f"Cache write failed: {exc}")

    def purge(self, keep_stale=KEEP_STALE):
        try:
            with self._lock:
                self._db.execute("DELETE FROM cache WHERE expires < ?", (self._clock() - keep_stale,))
        except sqlite3.Error as exc:
            self._log(f"Cache purge failed: {exc}")

    def clear(self):
        with self._lock:
            self._db.execute("DELETE FROM cache")

    def close(self):
        self._db.close()
