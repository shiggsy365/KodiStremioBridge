"""Arctic Zephyr Stremio's Home widgets, per Kodi profile.

Skin Shortcuts builds every profile's home menu into one file in the skin, but
with the logged-in profile's widgets for all of them, so each profile needed
the menu rebuilt (and the home screen loaded again) when it logged in. Instead,
the menu's Home, Movies and TV Shows items name their widgets' lists and titles
by reference to home-window properties (sbhome.*; the skin's
shortcuts/overrides.xml), the same for every profile, and the service fills
those in from the logged-in profile's choices before the home screen shows.

The choices are still made in Customise Home: Skin Shortcuts saves them in this
profile's properties file, and take_over() moves them into this add-on's data
for the profile (home_widgets.json), putting the references back in their place.
Which rows an item has (widgetEnable.N), and their layout, stay Skin Shortcuts'
own: the same for every profile.
"""

import ast
import json
import os
import re

import xbmcgui
import xbmcvfs

from .common import ADDON_ID, SKIN_ID, WIDGETS_RELOAD, log, profile_dir, skin_active

ITEMS = ("10000", "movies", "tvshows")  # labelIDs of the home menu's Home, Movies and TV Shows
SLOTS = range(1, 7)
FIELDS = ("widgetPath", "widgetName")
PROPERTIES_FILE = f"special://profile/addon_data/script.skinshortcuts/{SKIN_ID}.properties"
STORE = "home_widgets.json"

_OURS = f"plugin://{ADDON_ID}/"
DEFAULTS = {  # as the skin had them (until a profile chooses others)
    "10000": [(f"{_OURS}?action=continue", "Continue Watching"),
              (f"{_OURS}?action=watchlist", "MDBList Watchlist")],
    "movies": [(f"{_OURS}?action=catalog&addon=cinemeta&type=movie&id=imdbRating", "Featured Movies"),
               (f"{_OURS}?action=catalog&addon=cinemeta&type=movie&id=top", "Popular Movies")],
    "tvshows": [(f"{_OURS}?action=catalog&addon=cinemeta&type=series&id=imdbRating", "Featured Series"),
                (f"{_OURS}?action=catalog&addon=cinemeta&type=series&id=top", "Popular Series")],
}


def field(name, slot):
    """Skin Shortcuts' property name: ``widgetPath``, ``widgetPath.2``, ..."""
    return name if slot == 1 else f"{name}.{slot}"


FIELD_NAMES = {field(name, slot) for name in FIELDS for slot in SLOTS}


def home_property(item, name):
    return f"sbhome.{item}.{name}"


def reference(item, name):
    """What the menu holds instead of the value (the skin's overrides.xml)."""
    return f"$INFO[Window(Home).Property({home_property(item, name)})]"


def default(item, name):
    base, _, slot = name.partition(".")
    pairs = DEFAULTS.get(item, [])
    index = int(slot or 1) - 1
    if index >= len(pairs):
        return ""
    return pairs[index][0 if base == "widgetPath" else 1]


def _clean(value):
    """A saved widget path without its reload token: $INFO[...] isn't looked up
    inside a property's value, so publish() adds the token itself."""
    value = re.sub(r"\$INFO\[[^\]]*\]", "", str(value))
    return re.sub(r"[&?]reload=(?=&|$)", "", value)


def _store_path():
    return os.path.join(profile_dir(), STORE)


def load_store():
    try:
        with open(_store_path(), encoding="utf-8") as f:
            store = json.load(f)
        return store if isinstance(store, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_properties(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        try:
            return ast.literal_eval(raw)  # Skin Shortcuts' older format
        except (ValueError, SyntaxError):
            return None


def properties_path():
    return xbmcvfs.translatePath(PROPERTIES_FILE)


def take_over():
    """Move this profile's widget choices for ITEMS out of Skin Shortcuts'
    properties into our store, leaving references in their place (it builds a
    customised menu from the saved properties alone, not the skin's defaults).
    A row that's gone was removed in Customise Home: so is its choice. True if
    the file changed (the menu then has to be rebuilt once)."""
    path = properties_path()
    rows = _read_properties(path)
    if not isinstance(rows, list):
        return False
    store, out, present, moved = load_store(), [], set(), False
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 4 and row[0] == "mainmenu" \
                and row[1] in ITEMS and row[2] in FIELD_NAMES:
            item, name, value = row[1], row[2], row[3]
            present.add((item, name))
            if value != reference(item, name):  # chosen in Customise Home
                store.setdefault(item, {})[name] = _clean(value) if name.startswith("widgetPath") else value
                row = [row[0], item, name, reference(item, name)] + list(row[4:])
                moved = True
        out.append(row)
    forgotten = False
    for item in list(store):
        for name in list(store[item]):
            if (item, name) not in present:
                del store[item][name]
                forgotten = True
    if moved or forgotten:
        os.makedirs(profile_dir(), exist_ok=True)
        with open(_store_path(), "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1)
    if not moved:
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=4)  # as Skin Shortcuts writes it
    log("Home widgets: this profile's choices taken over from Customise Home")
    return True


def publish():
    """Fill in the menu's sbhome.* properties for the logged-in profile."""
    if not skin_active():
        return
    home = xbmcgui.Window(10000)
    token = home.getProperty(WIDGETS_RELOAD)
    store = load_store()
    for item in ITEMS:
        chosen = store.get(item, {})
        for name in sorted(FIELD_NAMES):
            value = chosen.get(name, default(item, name))
            if name.startswith("widgetPath") and value.startswith(_OURS) and token:
                value = f"{value}&reload={token}"  # widgets reload when it changes (common.notify_widgets)
            home.setProperty(home_property(item, name), value)
