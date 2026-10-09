import json
import os
import sqlite3
import zipfile

import pytest

import kodibackup


def addon(home, name, binary=False, version="1.0.0"):
    folder = home / "addons" / name
    folder.mkdir(parents=True, exist_ok=True)
    library = 'library_android="lib.so" library_linux="lib.so"' if binary else 'library="default.py"'
    (folder / "addon.xml").write_text(
        f'<?xml version="1.0"?>\n<addon id="{name}" version="{version}" name="x">\n'
        f'  <extension point="xbmc.python.pluginsource" {library}/>\n</addon>\n')
    return folder


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def addons_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE repo (id integer primary key, addonID text, checksum text, lastcheck text, "
                   "version text, nextcheck TEXT)")
        db.execute("INSERT INTO repo (addonID, checksum, lastcheck, version, nextcheck) "
                   "VALUES ('repository.xbmc.org', 'abc', '2026-10-09', '3.4.0', '2026-10-10')")


@pytest.fixture
def source(tmp_path):
    home = tmp_path / "source"
    addon(home, "plugin.video.stremiobridge")
    write(home / "addons/plugin.video.stremiobridge/resources/lib/__pycache__/x.pyc")
    addon(home, "inputstream.adaptive", binary=True, version="21.5.25")
    write(home / "addons/packages/skin-1.2.5.zip")
    write(home / "addons/skin.arctic.zephyr.stremio/media/Textures.xbt", "xbt")
    write(home / "userdata/guisettings.xml", "<settings/>")
    write(home / "userdata/profiles.xml", "<profiles/>")
    write(home / "userdata/addon_data/plugin.video.stremiobridge/watch.db", "history")
    write(home / "userdata/addon_data/plugin.video.stremiobridge/cache.db", "responses")
    write(home / "userdata/profiles/Vincent/addon_data/plugin.video.stremiobridge/watch.db", "his history")
    write(home / "userdata/profiles/Vincent/addon_data/plugin.video.stremiobridge/cache.db-wal", "responses")
    write(home / "userdata/Thumbnails/0/abc.jpg")
    write(home / "userdata/profiles/Vincent/Thumbnails/1/def.jpg")
    write(home / "userdata/Database/MyVideos131.db", "videos")
    write(home / "userdata/Database/Textures13.db")
    write(home / "userdata/Database/Epg16.db")
    addons_db(home / "userdata/Database/Addons33.db")
    write(home / "media/splash.jpg")
    write(home / "temp/kodi.log")
    return home


def test_backup_leaves_out_caches_and_binary_addons(source, tmp_path):
    files, binaries = kodibackup.plan(str(source))
    assert binaries == [("inputstream.adaptive", "21.5.25")]
    assert "userdata/profiles/Vincent/addon_data/plugin.video.stremiobridge/watch.db" in files
    assert "addons/skin.arctic.zephyr.stremio/media/Textures.xbt" in files
    assert "media/splash.jpg" in files and "userdata/Database/Addons33.db" in files
    for left_out in ("addons/packages/skin-1.2.5.zip", "userdata/Thumbnails/0/abc.jpg",
                     "userdata/profiles/Vincent/Thumbnails/1/def.jpg", "userdata/Database/Textures13.db",
                     "userdata/Database/Epg16.db", "userdata/addon_data/plugin.video.stremiobridge/cache.db",
                     "userdata/profiles/Vincent/addon_data/plugin.video.stremiobridge/cache.db-wal",
                     "addons/inputstream.adaptive/addon.xml", "temp/kodi.log"):
        assert left_out not in files
    assert not any("__pycache__" in f for f in files)

    target = tmp_path / "backup.zip"
    assert kodibackup.write_backup(str(source), str(target), files, {"binary_addons": binaries}) == len(files)
    manifest = kodibackup.read_manifest(str(target))
    assert manifest["files"] == len(files) and manifest["binary_addons"] == [["inputstream.adaptive", "21.5.25"]]
    with zipfile.ZipFile(target) as z:
        assert z.getinfo("addons/skin.arctic.zephyr.stremio/media/Textures.xbt").compress_type == zipfile.ZIP_STORED


def test_cancelling_a_backup_leaves_no_file(source, tmp_path):
    files, _ = kodibackup.plan(str(source))
    target = tmp_path / "backup.zip"
    with pytest.raises(kodibackup.BackupError):
        kodibackup.write_backup(str(source), str(target), files, {}, progress=lambda *a: False)
    assert not target.exists() and not (tmp_path / "backup.zip.part").exists()


def test_restore_replaces_the_device_and_keeps_its_own_binary_addons(source, tmp_path):
    files, binaries = kodibackup.plan(str(source))
    backup = tmp_path / "backup.zip"
    kodibackup.write_backup(str(source), str(backup), files, {"binary_addons": binaries, "device": "Stick"})

    device = tmp_path / "device"  # another device: its own add-ons, profiles and binaries
    addon(device, "plugin.video.other")
    addon(device, "pvr.iptvsimple", binary=True)
    write(device / "addons/packages/keep.zip")
    write(device / "userdata/guisettings.xml", "<old/>")
    write(device / "userdata/profiles/Someone/guisettings.xml")

    manifest, missing = kodibackup.restore(str(device), str(backup))
    assert manifest["device"] == "Stick"
    assert missing == ["inputstream.adaptive"]                         # listed, not installed here
    assert (device / "userdata/guisettings.xml").read_text() == "<settings/>"
    assert (device / "userdata/profiles/Vincent/addon_data/plugin.video.stremiobridge/watch.db").read_text() \
        == "his history"
    assert not (device / "userdata/profiles/Someone").exists()          # the device's own profiles are replaced
    assert (device / "addons/plugin.video.stremiobridge/addon.xml").exists()
    assert not (device / "addons/plugin.video.other").exists()
    assert (device / "addons/pvr.iptvsimple/addon.xml").exists()         # its binary add-on stays
    assert (device / "addons/packages/keep.zip").exists()
    assert not (device / "temp/sb-restore").exists()
    with sqlite3.connect(device / "userdata/Database/Addons33.db") as db:
        assert db.execute("SELECT checksum, lastcheck, nextcheck FROM repo").fetchall() == [("", "", "")]


def test_restore_refuses_what_isnt_a_backup_or_escapes(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(kodibackup.BackupError):
        kodibackup.read_manifest(str(bad))

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(kodibackup.MANIFEST, json.dumps({"format": 1}))
        z.writestr("userdata/../../outside.txt", "nope")
    home = tmp_path / "home"
    write(home / "userdata/guisettings.xml", "<mine/>")
    with pytest.raises(kodibackup.BackupError):
        kodibackup.restore(str(home), str(evil))
    assert (home / "userdata/guisettings.xml").read_text() == "<mine/>"   # untouched
    assert not (home / "temp/sb-restore").exists()
    assert not (tmp_path / "outside.txt").exists()

    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr(kodibackup.MANIFEST, json.dumps({"format": kodibackup.FORMAT + 1}))
    with pytest.raises(kodibackup.BackupError):
        kodibackup.read_manifest(str(newer))


def test_backup_name():
    assert kodibackup.backup_name("Fire TV Stick 4K", 0).startswith("kodi-backup-Fire-TV-Stick-4K-")
    assert kodibackup.backup_name("", 0).startswith("kodi-backup-kodi-")
