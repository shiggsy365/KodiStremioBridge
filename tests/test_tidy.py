"""Tidy Cache (service.shiggsy365.tidycache) logic."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "service.shiggsy365.tidycache", "resources", "lib"))

import tidy  # noqa: E402

NOW = datetime(2026, 10, 8, 12, 0, 0).timestamp()


def test_schedule_and_query():
    assert tidy.is_due(0, NOW, 7)                                  # never run
    assert not tidy.is_due(NOW - 6 * tidy.DAY, NOW, 7)
    assert tidy.is_due(NOW - 7 * tidy.DAY, NOW, 7)
    assert tidy.textures_query(NOW, 7)["filter"] == {
        "field": "lastused", "operator": "lessthan", "value": "2026-10-01 12:00:00"}
    assert "filter" not in tidy.textures_query(NOW, 0)             # 0: all cached artwork


def test_remove_textures(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "a1.jpg").write_bytes(b"x" * 1000)
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        if method == "Textures.GetTextures":
            return {"textures": [{"textureid": 1, "cachedurl": "a/a1.jpg"},
                                 {"textureid": 2, "cachedurl": "b/gone.jpg"},   # file already missing
                                 {"textureid": 3, "cachedurl": ""}]}
        return "OK"

    assert tidy.remove_textures(rpc, str(tmp_path), NOW, 7) == (3, 1000, True)
    assert [p["textureid"] for m, p in calls if m == "Textures.RemoveTexture"] == [1, 2, 3]

    # Interrupted (playback started): stops, and reports it didn't finish.
    calls.clear()
    budget = iter([True, False])
    assert tidy.remove_textures(rpc, str(tmp_path), NOW, 7, keep_going=lambda: next(budget)) == (1, 1000, False)


def test_remove_packages(tmp_path):
    old, fresh = tmp_path / "old-1.0.zip", tmp_path / "fresh-2.0.zip"
    old.write_bytes(b"x" * 500)
    fresh.write_bytes(b"x" * 300)
    os.utime(old, (NOW - 2 * 3600, NOW - 2 * 3600))
    os.utime(fresh, (NOW - 60, NOW - 60))                          # an install still in progress
    (tmp_path / "sub").mkdir()
    assert tidy.remove_packages(str(tmp_path), NOW) == (1, 500)
    assert not old.exists() and fresh.exists() and (tmp_path / "sub").exists()
    assert tidy.remove_packages(str(tmp_path / "missing"), NOW) == (0, 0)


def test_size_text():
    assert [tidy.size_text(n) for n in (500, 2048, 5 * 1024 ** 2, 3 * 1024 ** 3)] == [
        "500 bytes", "2.0 KB", "5.0 MB", "3.0 GB"]
