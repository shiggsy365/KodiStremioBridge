"""What's been watched, and where playback stopped.

One row per video, keyed by its Stremio id: ``tt0111161`` for a movie,
``tt0903747:1:2`` for an episode. The playback service writes it; listings,
Continue Watching and Next Up read it; MDBList sync merges into it.
"""

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field

WATCHED_RATIO = 0.9
MIN_RESUME_SECONDS = 120

# Safety net for remote sync: never apply more removals than this in one go
# (a broken or partial remote response must not wipe local history).
REMOVAL_MAX_FRACTION = 0.3
REMOVAL_MIN = 15
# Items watched this recently are never unwatched by a pull: the remote list
# may simply not include them yet.
REMOVAL_GRACE = 86400

WATCHED, RESUME = "watched", "resume"


@dataclass
class PlaybackEntry:
    """What's playing, as far as watch state (and MDBList) needs to know."""

    video_id: str
    type: str
    meta_id: str = ""
    season: int = None
    episode: int = None
    title: str = ""
    show_title: str = ""
    poster: str = ""
    thumb: str = ""
    fanart: str = ""
    binge_group: str = ""
    ids: dict = field(default_factory=dict)  # external ids of the movie, or of the show for episodes

    @property
    def is_episode(self):
        return self.season is not None and self.episode is not None

    def to_json(self):
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text):
        data = json.loads(text)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Row(PlaybackEntry):
    position: float = 0.0
    duration: float = 0.0
    watched: bool = False
    watched_at: float = 0.0
    updated_at: float = 0.0
    synced: bool = False

    @property
    def progress(self):
        return self.position / self.duration if self.duration else 0.0


_COLUMNS = [
    ("video_id", "TEXT PRIMARY KEY"), ("type", "TEXT"), ("meta_id", "TEXT"), ("season", "INTEGER"),
    ("episode", "INTEGER"), ("title", "TEXT"), ("show_title", "TEXT"), ("poster", "TEXT"), ("thumb", "TEXT"),
    ("fanart", "TEXT"), ("binge_group", "TEXT"), ("ids", "TEXT"), ("position", "REAL"), ("duration", "REAL"),
    ("watched", "INTEGER"), ("watched_at", "REAL"), ("updated_at", "REAL"), ("synced", "INTEGER"),
]
_NAMES = [name for name, _ in _COLUMNS]
_META_FIELDS = ["type", "meta_id", "season", "episode", "title", "show_title", "poster", "thumb", "fanart",
                "binge_group"]
_TEXT_FIELDS = [name for name in _META_FIELDS if name not in ("season", "episode")]


def next_episode(meta, watched, today):
    """The episode after the furthest one watched, if it has aired and isn't
    watched yet. `watched` is a set of (season, episode); specials are ignored."""
    episodes = sorted(
        (v for v in meta.videos if v.season and v.episode is not None),
        key=lambda v: (v.season, v.episode),
    )
    last = max((i for i, v in enumerate(episodes) if (v.season, v.episode) in watched), default=None)
    if last is None:
        return None
    for video in episodes[last + 1:]:
        if (video.season, video.episode) not in watched:
            return video if video.is_released(today) else None
    return None


class WatchState:
    def __init__(self, path, watched_ratio=WATCHED_RATIO, min_resume=MIN_RESUME_SECONDS, clock=time.time):
        self.watched_ratio = watched_ratio
        self.min_resume = min_resume
        self._clock = clock
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(f"CREATE TABLE IF NOT EXISTS progress ({', '.join(f'{n} {t}' for n, t in _COLUMNS)})")
        self._db.execute("CREATE INDEX IF NOT EXISTS progress_meta ON progress (meta_id)")

    # ------------------------------------------------------------ reading

    def _rows(self, where="", params=()):
        with self._lock:
            cursor = self._db.execute(f"SELECT {', '.join(_NAMES)} FROM progress {where}", params)
            return [self._row(values) for values in cursor.fetchall()]

    @staticmethod
    def _row(values):
        data = dict(zip(_NAMES, values))
        data["ids"] = json.loads(data["ids"] or "{}")
        for key in ("watched", "synced"):
            data[key] = bool(data[key])
        for key in ("position", "duration", "watched_at", "updated_at"):
            data[key] = data[key] or 0.0
        for key in _TEXT_FIELDS:
            data[key] = data[key] or ""
        return Row(**data)

    def get(self, video_id):
        rows = self._rows("WHERE video_id = ?", (video_id,))
        return rows[0] if rows else None

    def lookup(self, video_ids):
        """``{video_id: Row}`` for the ids that have any state."""
        video_ids = list(dict.fromkeys(video_ids))
        found = {}
        for i in range(0, len(video_ids), 500):
            chunk = video_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            found.update({r.video_id: r for r in self._rows(f"WHERE video_id IN ({marks})", chunk)})
        return found

    def continue_watching(self, limit=50):
        return self._rows("WHERE position > 0 ORDER BY updated_at DESC LIMIT ?", (limit,))

    def recent_shows(self, limit=50, with_time=False):
        """``(show_id, type)`` for shows with watched episodes, most recently
        active first; with `with_time`, ``(show_id, type, last_activity)``."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT meta_id, MAX(type), MAX(MAX(updated_at), MAX(watched_at)) AS active FROM progress "
                "WHERE season IS NOT NULL AND watched = 1 AND meta_id != '' "
                "GROUP BY meta_id ORDER BY active DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
        if with_time:
            return [(meta_id, type_ or "series", active or 0.0) for meta_id, type_, active in rows]
        return [(meta_id, type_ or "series") for meta_id, type_, _ in rows]

    def watched_episodes(self, meta_id):
        with self._lock:
            cursor = self._db.execute(
                "SELECT season, episode FROM progress WHERE meta_id = ? AND watched = 1 AND season IS NOT NULL",
                (meta_id,))
            return {(s, e) for s, e in cursor.fetchall()}

    def binge_group(self, meta_id, max_age):
        """The source group last used for this show, if within `max_age` seconds."""
        rows = self._rows("WHERE meta_id = ? AND binge_group != '' AND updated_at >= ? "
                          "ORDER BY updated_at DESC LIMIT 1", (meta_id, self._clock() - max_age))
        return rows[0].binge_group if rows else ""

    def unsynced_watched(self):
        return self._rows("WHERE watched = 1 AND synced = 0")

    def watched_count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM progress WHERE watched = 1").fetchone()[0]

    # ------------------------------------------------------------ writing

    def _upsert(self, row):
        values = asdict(row)
        values["ids"] = json.dumps(values["ids"] or {})
        values["watched"] = int(values["watched"])
        values["synced"] = int(values["synced"])
        with self._lock:
            self._db.execute(
                f"INSERT OR REPLACE INTO progress ({', '.join(_NAMES)}) VALUES ({', '.join('?' * len(_NAMES))})",
                [values[n] for n in _NAMES])

    def _merged(self, entry):
        """The stored row for `entry` (or a new one), with entry's metadata applied."""
        row = self.get(entry.video_id) or Row(video_id=entry.video_id, type=entry.type)
        for name in _META_FIELDS:
            value = getattr(entry, name)
            if value not in (None, ""):
                setattr(row, name, value)
        if entry.ids:
            row.ids = {**row.ids, **entry.ids}
        return row

    def record(self, entry, position, duration):
        """Store playback progress. Returns WATCHED, RESUME or None (too short
        to count; any earlier resume point is kept)."""
        if not duration or duration <= 0:
            return None
        now = self._clock()
        row = self._merged(entry)
        if position >= duration * self.watched_ratio:
            if not row.watched:
                row.synced = False
            row.watched, row.watched_at, row.position = True, now, 0.0
            status = WATCHED
        elif position >= self.min_resume:
            row.position, status = float(position), RESUME
        else:
            return None
        row.duration, row.updated_at = float(duration), now
        self._upsert(row)
        return status

    def touch(self, entry):
        """Remember what's being played (e.g. its source group) without progress."""
        row = self._merged(entry)
        row.updated_at = self._clock()
        self._upsert(row)

    def set_watched(self, entries, watched, synced=False):
        now = self._clock()
        for entry in entries:
            row = self._merged(entry)
            if bool(row.watched) != watched or row.position:
                row.watched, row.position = watched, 0.0
                row.watched_at = now if watched else 0.0
                row.synced = synced
                row.updated_at = now if watched else row.updated_at
                self._upsert(row)

    def clear_resume(self, video_id):
        with self._lock:
            self._db.execute("UPDATE progress SET position = 0 WHERE video_id = ?", (video_id,))

    def mark_synced(self, video_ids):
        with self._lock:
            self._db.executemany("UPDATE progress SET synced = 1 WHERE video_id = ?", [(v,) for v in video_ids])

    def merge_remote(self, remote, full=True, protect=()):
        """Apply a remote watched list: ``[PlaybackEntry-like with watched_at]``.

        Everything in it becomes watched (and synced). With `full`, locally
        watched items that were synced before but are missing remotely are
        unwatched, unless that would remove suspiciously many (then nothing is
        removed). Ids in `protect` (e.g. just pushed) and items watched within
        REMOVAL_GRACE are never removed. Returns ``(added, removed, removal_skipped)``.
        """
        remote_ids = set()
        added = 0
        for entry, watched_at in remote:
            remote_ids.add(entry.video_id)
            row = self._merged(entry)
            if not row.watched or not row.synced:
                added += not row.watched
                row.watched, row.synced, row.position = True, True, 0.0
                row.watched_at = watched_at or row.watched_at or self._clock()
                row.updated_at = max(row.updated_at, row.watched_at)
                self._upsert(row)

        if not full:
            return added, 0, False
        synced = [r for r in self._rows("WHERE watched = 1 AND synced = 1")]
        cutoff = self._clock() - REMOVAL_GRACE
        gone = [r for r in synced
                if r.video_id not in remote_ids and r.video_id not in protect and r.watched_at < cutoff]
        if gone and len(gone) > max(REMOVAL_MIN, len(synced) * REMOVAL_MAX_FRACTION):
            return added, 0, True
        for row in gone:
            row.watched, row.synced, row.watched_at = False, False, 0.0
            self._upsert(row)
        return added, len(gone), False

    def merge_resume(self, remote):
        """Apply resume points from another device: ``[(entry, position, duration,
        updated_epoch)]``. A remote point wins only if it's newer than this
        device's state for the item, long enough to count, and short of watched.
        Returns how many were applied."""
        applied = 0
        for entry, position, duration, updated in remote:
            if not duration or position < self.min_resume or position >= duration * self.watched_ratio:
                continue
            row = self._merged(entry)
            if updated <= max(row.updated_at, row.watched_at):
                continue
            row.position, row.duration, row.updated_at = float(position), float(duration), float(updated)
            self._upsert(row)
            applied += 1
        return applied

    def clear(self):
        with self._lock:
            self._db.execute("DELETE FROM progress")

    def close(self):
        self._db.close()
