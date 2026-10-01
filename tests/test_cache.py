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
