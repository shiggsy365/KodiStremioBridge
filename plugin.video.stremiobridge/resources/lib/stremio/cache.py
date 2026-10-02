"""A small sqlite key/value cache with per-entry expiry.

Kodi runs every plugin call (and every widget) in its own process, so this has
to cope with several processes using the database at once: WAL mode plus a
busy timeout, and cache failures are logged and ignored rather than raised.

Values are stored as zlib-compressed JSON (addon responses shrink to about a
fifth; older uncompressed rows are still read). purge() keeps the values under
MAX_BYTES, dropping the entries closest to expiry first, and gives freed space
back to the device.
"""

import json
import os
import sqlite3
import threading
import time
import zlib

# Expired entries are kept this long so they can still be served when an addon is down.
KEEP_STALE = 7 * 86400
MAX_BYTES = 25 * 1024 ** 2


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
        try:
            value = row[1]
            data = zlib.decompress(value) if isinstance(value, bytes) else value
            return json.loads(data), fresh
        except (zlib.error, ValueError) as exc:
            self._log(f"Cache entry unreadable ({exc}); ignoring it")
            return None

    def set(self, key, value, ttl):
        try:
            with self._lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO cache (key, expires, value) VALUES (?, ?, ?)",
                    (key, self._clock() + ttl, sqlite3.Binary(zlib.compress(json.dumps(value).encode(), 6))),
                )
        except sqlite3.Error as exc:
            self._log(f"Cache write failed: {exc}")

    def purge(self, keep_stale=KEEP_STALE, max_bytes=MAX_BYTES):
        """Drop entries long expired, then (while the values take more than
        `max_bytes`) those closest to expiry, and shrink the file if much was freed."""
        try:
            with self._lock:
                removed = self._db.execute("DELETE FROM cache WHERE expires < ?",
                                           (self._clock() - keep_stale,)).rowcount
                total = self._db.execute("SELECT TOTAL(LENGTH(value)) FROM cache").fetchone()[0]
                if total > max_bytes:
                    doomed = []
                    for key, size in self._db.execute("SELECT key, LENGTH(value) FROM cache ORDER BY expires"):
                        if total <= max_bytes:
                            break
                        doomed.append((key,))
                        total -= size
                    self._db.executemany("DELETE FROM cache WHERE key = ?", doomed)
                    removed += len(doomed)
                pages = self._db.execute("PRAGMA page_count").fetchone()[0]
                free = self._db.execute("PRAGMA freelist_count").fetchone()[0]
                if removed and free > pages // 4:
                    self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    self._db.execute("VACUUM")
        except sqlite3.Error as exc:
            self._log(f"Cache purge failed: {exc}")

    def clear(self):
        with self._lock:
            self._db.execute("DELETE FROM cache")

    def close(self):
        self._db.close()
