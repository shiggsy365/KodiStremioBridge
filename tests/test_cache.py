from stremio.cache import KEEP_STALE, Cache


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_fresh_stale_and_missing(tmp_path):
    clock = Clock()
    cache = Cache(str(tmp_path / "c.db"), clock=clock)
    assert cache.get("k") is None
    cache.set("k", {"metas": [1, 2]}, ttl=60)
    assert cache.get("k") == ({"metas": [1, 2]}, True)

    clock.now += 61
    assert cache.get("k") is None
    assert cache.get("k", allow_stale=True) == ({"metas": [1, 2]}, False)


def test_purge_keeps_recently_expired(tmp_path):
    clock = Clock()
    cache = Cache(str(tmp_path / "c.db"), clock=clock)
    cache.set("old", 1, ttl=10)
    cache.set("new", 2, ttl=10)
    clock.now += KEEP_STALE + 11  # "old" expired more than KEEP_STALE ago
    cache.set("new", 2, ttl=10)
    cache.purge()
    assert cache.get("old", allow_stale=True) is None
    assert cache.get("new") == (2, True)


def test_shared_between_instances_and_clear(tmp_path):
    path = str(tmp_path / "c.db")
    Cache(path).set("k", "v", ttl=60)
    other = Cache(path)
    assert other.get("k") == ("v", True)
    other.clear()
    assert Cache(path).get("k") is None


def test_errors_are_logged_not_raised(tmp_path):
    messages = []
    cache = Cache(str(tmp_path / "c.db"), log=messages.append)
    cache.close()
    assert cache.get("k") is None
    cache.set("k", 1, ttl=5)
    assert len(messages) == 2


def test_values_are_compressed_and_old_rows_still_read(tmp_path):
    import json
    import sqlite3

    cache = Cache(str(tmp_path / "c.db"))
    big = {"videos": [{"overview": "The same long text. " * 20} for _ in range(200)]}
    cache.set("big", big, ttl=60)
    assert cache.get("big") == (big, True)
    db = sqlite3.connect(str(tmp_path / "c.db"))
    stored = db.execute("SELECT value FROM cache WHERE key = 'big'").fetchone()[0]
    assert isinstance(stored, bytes) and len(stored) < len(json.dumps(big)) / 5
    db.execute("INSERT INTO cache (key, expires, value) VALUES ('legacy', 9e12, ?)", (json.dumps([1, 2]),))
    db.commit()
    assert cache.get("legacy") == ([1, 2], True)                                  # written before compression
    db.execute("INSERT INTO cache (key, expires, value) VALUES ('broken', 9e12, ?)", (b"not zlib",))
    db.commit()
    assert cache.get("broken") is None


def test_purge_caps_the_size_dropping_entries_closest_to_expiry(tmp_path):
    import os

    cache = Cache(str(tmp_path / "c.db"))
    noise = lambda n: os.urandom(30000).hex()  # incompressible
    for n in range(40):
        cache.set(f"k{n}", noise(n), ttl=100 + n)                                 # k0 expires first
    cache.purge(max_bytes=600_000)
    left = [n for n in range(40) if cache.get(f"k{n}")]
    assert left and left == list(range(40 - len(left), 40))                       # the latest-expiring survive
    assert os.path.getsize(tmp_path / "c.db") < 40 * 60000                        # space given back
