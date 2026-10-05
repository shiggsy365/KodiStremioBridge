"""Build the Kodi repository served by GitHub Pages from docs/.

    .venv/bin/python tools/build_repo.py

For each add-on in ADDONS this writes docs/<id>/<id>-<version>.zip (plus a
.sha256 next to it, and the icon/fanart Kodi shows before installing), then
docs/addons.xml, docs/addons.xml.md5 and docs/index.html. index.html links the
repository zip, so docs/ can be added in Kodi as a file source and the zip
installed from there. Older zips of each add-on are removed.

Bump an add-on's version in its addon.xml before building, or Kodi won't see
the update.
"""

import hashlib
import html
import os
import re
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "docs")
ADDONS = ["plugin.video.stremiobridge", "plugin.audio.shiggsy365.podcasts", "script.shiggsy365.dispatcharrbridge",
          "service.shiggsy365.tidycache", "skin.arctic.zephyr.stremio",
          "repository.shiggsy365"]
REPOSITORY = "repository.shiggsy365"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", "~")
# Fixed timestamp: zips of unchanged files are byte-identical, so rebuilding
# doesn't churn the git history.
ZIP_TIME = (2024, 1, 1, 0, 0, 0)


def addon_info(addon_dir):
    root = ET.parse(os.path.join(addon_dir, "addon.xml")).getroot()
    assets = {}
    for meta in root.iter("extension"):
        if meta.get("point") == "xbmc.addon.metadata":
            for asset in meta.iter("assets"):
                assets = {child.tag: child.text.strip() for child in asset if child.text}
    return root.get("id"), root.get("version"), assets


def files_of(addon_dir):
    for folder, dirs, files in os.walk(addon_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.startswith(".") and not name.endswith(SKIP_SUFFIXES):
                yield os.path.join(folder, name)


def build_zip(addon_dir, addon_id, version):
    folder = os.path.join(OUT, addon_id)
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):
        if old.startswith(addon_id + "-") and old.endswith((".zip", ".zip.sha256")):
            os.remove(os.path.join(folder, old))
    path = os.path.join(folder, f"{addon_id}-{version}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files_of(addon_dir):
            arcname = os.path.join(addon_id, os.path.relpath(file, addon_dir)).replace(os.sep, "/")
            info = zipfile.ZipInfo(arcname, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(file, "rb") as f:
                zf.writestr(info, f.read())
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(path + ".sha256", "w") as f:
        f.write(digest)
    return path


def copy_assets(addon_dir, addon_id, assets):
    """Icon and fanart beside the zip, at the paths addon.xml names, so Kodi can
    show them in the repository before the add-on is installed."""
    for relative in assets.values():
        source = os.path.join(addon_dir, relative)
        if os.path.isfile(source):
            target = os.path.join(OUT, addon_id, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(source, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())


def addon_xml_body(addon_dir):
    with open(os.path.join(addon_dir, "addon.xml"), encoding="utf-8") as f:
        text = f.read()
    return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", text).rstrip() + "\n"


def write_index(entries):
    links = "\n".join(
        f'    <li><a href="{html.escape(rel)}">{html.escape(os.path.basename(rel))}</a></li>' for rel in entries)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shiggsy365 Kodi Repository</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f151c; color: #e6edf3; margin: 0 auto;
         max-width: 720px; padding: 24px 16px; line-height: 1.5; }}
  a {{ color: #4cc3e6; }}
  code {{ background: #1c2733; padding: 1px 6px; border-radius: 4px; }}
</style>
</head>
<body>
<h1>shiggsy365 Kodi Repository</h1>
<p>In Kodi: <b>Settings &rarr; File manager &rarr; Add source</b>, enter
<code>https://shiggsy365.github.io/KodiStremioBridge/</code>, then
<b>Add-ons &rarr; Install from zip file</b> and pick the repository zip below.
Then install <b>Stremio Bridge</b> from <b>Install from repository</b>.</p>
<ul>
{links}
</ul>
</body>
</html>
"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def main():
    os.makedirs(OUT, exist_ok=True)
    bodies, links = [], []
    for name in ADDONS:
        addon_dir = os.path.join(ROOT, name)
        addon_id, version, assets = addon_info(addon_dir)
        path = build_zip(addon_dir, addon_id, version)
        copy_assets(addon_dir, addon_id, assets)
        bodies.append(addon_xml_body(addon_dir))
        rel = os.path.relpath(path, OUT).replace(os.sep, "/")
        if addon_id == REPOSITORY:
            # Also at the top level: "Install from zip file" lists it straight away.
            top = os.path.join(OUT, os.path.basename(path))
            for old in os.listdir(OUT):
                if old.startswith(REPOSITORY + "-") and old.endswith(".zip"):
                    os.remove(os.path.join(OUT, old))
            with open(path, "rb") as src, open(top, "wb") as dst:
                dst.write(src.read())
            links.insert(0, os.path.basename(path))
        else:
            links.append(rel)
        print(f"{addon_id} {version}: {rel} ({os.path.getsize(path) // 1024} KB)")
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + "".join(bodies) + "</addons>\n"
    with open(os.path.join(OUT, "addons.xml"), "w", encoding="utf-8") as f:
        f.write(xml)
    with open(os.path.join(OUT, "addons.xml.md5"), "w") as f:
        f.write(hashlib.md5(xml.encode("utf-8")).hexdigest() + "\n")
    write_index(links)
    # GitHub Pages runs Jekyll by default; this serves the files as they are.
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    print(f"Wrote {os.path.relpath(OUT, ROOT)}/addons.xml, addons.xml.md5 and index.html")


if __name__ == "__main__":
    main()
