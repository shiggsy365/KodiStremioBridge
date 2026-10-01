"""The user's installed Stremio addons, persisted as JSON.

Addons are keyed by transport URL rather than manifest id, so the same addon
can be installed several times with different configurations.
"""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field

from . import StremioError
from .client import base_url
from .models import Manifest

SCHEMA_VERSION = 1


HUBS = ("movies", "tvshows", "anime", "more")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B00-\u2BFF\u2300-\u23FF]+")


def default_hub(type_):
    """The hub a catalog of this Stremio type goes in unless the user moves it."""
    if type_ == "movie":
        return "movies"
    if type_ == "series":
        return "tvshows"
    if "anime" in type_.lower():
        return "anime"
    return "more"


def tidy_name(name):
    """Catalog name without emoji and doubled spaces ("Netflix 🍿" -> "Netflix")."""
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", name)).strip() or name


class DuplicateAddonError(StremioError):
    pass


class UnknownAddonError(StremioError):
    pass


@dataclass
class CatalogPrefs:
    home: bool = True    # shown in its hub (and type menu)
    search: bool = True  # used by Search
    front: bool = None   # pinned to the front page (only an explicit True pins)
    search_name: str = ""  # the user's name for it in search ("" = the addon's name)
    name: str = ""       # the user's name for it when browsing ("" = the addon's name)
    hub: str = ""        # the hub it's in ("" = the default for its type)

    def on_front_page(self, catalog):
        return catalog.default_front_page if self.front is None else self.front


@dataclass
class InstalledAddon:
    transport_url: str
    manifest: Manifest
    enabled: bool = True
    catalog_prefs: dict = field(default_factory=dict)
    fetched_at: float = 0.0  # when the manifest was last fetched (epoch seconds; 0 = unknown)

    @property
    def name(self):
        return self.manifest.name

    @property
    def key(self):
        """Short stable id used in plugin URLs, so configured tokens stay out of
        Kodi's logs and widget settings."""
        return hashlib.sha1(self.transport_url.encode("utf-8")).hexdigest()[:10]

    @property
    def base_url(self):
        return base_url(self.transport_url)

    def prefs_for(self, catalog):
        return self.catalog_prefs.get(catalog.key) or CatalogPrefs()

    def hub_of(self, catalog):
        return self.prefs_for(catalog).hub or default_hub(catalog.type)

    def display_name(self, catalog, tidy=False):
        """The catalog's name when browsing: the user's rename, else the addon's
        (with emoji removed if `tidy`)."""
        name = self.prefs_for(catalog).name
        if name:
            return name
        return tidy_name(catalog.name) if tidy else catalog.name

    def search_title(self, catalog):
        """The catalog's name in search: the user's rename, or the addon's name."""
        return self.prefs_for(catalog).search_name or catalog.name

    def home_catalogs(self):
        return [c for c in self.manifest.catalogs if c.is_browsable and self.prefs_for(c).home]

    def search_catalogs(self):
        return [c for c in self.manifest.catalogs if c.is_searchable_alone and self.prefs_for(c).search]

    def front_catalogs(self):
        return [c for c in self.manifest.catalogs if c.is_browsable and self.prefs_for(c).on_front_page(c)]

    def to_dict(self):
        return {
            "transportUrl": self.transport_url,
            "manifest": self.manifest.raw,
            "enabled": self.enabled,
            "fetchedAt": self.fetched_at,
            "catalogPrefs": {
                key: {"home": p.home, "search": p.search, **({} if p.front is None else {"front": p.front}),
                      **({"searchName": p.search_name} if p.search_name else {}),
                      **({"name": p.name} if p.name else {}), **({"hub": p.hub} if p.hub else {})}
                for key, p in self.catalog_prefs.items()
            },
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            transport_url=data["transportUrl"],
            manifest=Manifest.from_dict(data["manifest"]),
            enabled=bool(data.get("enabled", True)),
            fetched_at=float(data.get("fetchedAt") or 0),
            catalog_prefs={
                key: CatalogPrefs(
                    home=bool(p.get("home", True)),
                    search=bool(p.get("search", True)),
                    front=p["front"] if isinstance(p.get("front"), bool) else None,
                    search_name=p.get("searchName") if isinstance(p.get("searchName"), str) else "",
                    name=p.get("name") if isinstance(p.get("name"), str) else "",
                    hub=p.get("hub") if isinstance(p.get("hub"), str) else "",  # checked by the registry
                )
                for key, p in (data.get("catalogPrefs") or {}).items()
            },
        )


class AddonRegistry:
    def __init__(self, path, log=None):
        self.path = path
        self._log = log or (lambda msg: None)
        self._search_order = []  # "addonkey|type/id" keys, user's search order
        self._hub_order = []     # same keys, user's browsing order (across all hubs)
        self._custom_hubs = []   # [{"id": "c1", "name": "Kids"}], the user's own hubs in order
        self._hub_names = {}     # built-in hub -> the user's name for it
        self._addons = self._load()

    # Persistence

    def _load(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self._search_order = [k for k in data.get("searchOrder") or [] if isinstance(k, str)]
            self._hub_order = [k for k in data.get("hubOrder") or [] if isinstance(k, str)]
            self._custom_hubs = [{"id": h["id"], "name": h["name"]} for h in data.get("customHubs") or []
                                 if isinstance(h, dict) and isinstance(h.get("id"), str)
                                 and isinstance(h.get("name"), str) and h["id"] not in HUBS]
            self._hub_names = {k: v for k, v in (data.get("hubNames") or {}).items()
                               if k in HUBS and isinstance(v, str) and v}
            addons = [InstalledAddon.from_dict(a) for a in data.get("addons", [])]
            known = set(HUBS) | {h["id"] for h in self._custom_hubs}
            for addon in addons:  # a catalog in a hub that's gone goes back to its usual one
                for prefs in addon.catalog_prefs.values():
                    if prefs.hub not in known:
                        prefs.hub = ""
            return addons
        except (OSError, ValueError, KeyError, TypeError, StremioError) as exc:
            # Keep the broken file for inspection instead of silently overwriting it.
            backup = self.path + ".corrupt"
            self._log(f"Could not read {self.path} ({exc}); moved to {backup}")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return []

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"version": SCHEMA_VERSION, "addons": [a.to_dict() for a in self._addons],
                 "searchOrder": self._search_order, "hubOrder": self._hub_order,
                 "customHubs": self._custom_hubs, "hubNames": self._hub_names},
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    # Queries

    def all(self):
        return list(self._addons)

    def enabled(self):
        return [a for a in self._addons if a.enabled]

    def get(self, ref):
        """Look up an addon by its key or transport URL."""
        for addon in self._addons:
            if ref in (addon.key, addon.transport_url):
                return addon
        raise UnknownAddonError(ref)

    def addons_for(self, resource, type_, id_=None):
        """Enabled addons that can serve `resource` for `type_`/`id_`, in user order."""
        return [a for a in self.enabled() if a.manifest.supports(resource, type_, id_)]

    def home_catalogs(self, type_=None):
        """``(addon, catalog)`` pairs shown when browsing, in user order."""
        return [
            (addon, catalog)
            for addon in self.enabled()
            for catalog in addon.home_catalogs()
            if type_ is None or catalog.type == type_
        ]

    @staticmethod
    def search_key(addon, catalog):
        return f"{addon.key}|{catalog.key}"

    # ------------------------------------------------------------ hubs

    def _browsable_in_order(self):
        """Every browsable catalog of enabled addons, in the user's order."""
        entries = [(a, c) for a in self.enabled() for c in a.manifest.catalogs if c.is_browsable]
        rank = {key: i for i, key in enumerate(self._hub_order)}
        return [e for _, e in sorted(enumerate(entries),
                                     key=lambda ie: (rank.get(self.search_key(*ie[1]), len(rank)), ie[0]))]

    def hub_entries(self, hub):
        """``(addon, catalog, shown)`` for every catalog in `hub`, in order (for organising)."""
        return [(a, c, a.prefs_for(c).home) for a, c in self._browsable_in_order() if a.hub_of(c) == hub]

    def hub_catalogs(self, hub):
        """``(addon, catalog)`` shown in `hub`, in the user's order."""
        return [(a, c) for a, c, shown in self.hub_entries(hub) if shown]

    def hubs(self):
        """Every hub: the built-in ones, then the user's own in creation order."""
        return list(HUBS) + [h["id"] for h in self._custom_hubs]

    def is_custom_hub(self, hub):
        return any(h["id"] == hub for h in self._custom_hubs)

    def hub_name(self, hub):
        """The user's name for `hub`, or "" (a built-in hub then uses its own label)."""
        custom = next((h["name"] for h in self._custom_hubs if h["id"] == hub), None)
        return custom if custom is not None else self._hub_names.get(hub, "")

    def create_hub(self, name):
        """Add a hub of the user's own (empty to start with); returns its id."""
        name = name.strip()
        if not name:
            raise ValueError("A hub needs a name")
        taken = {h["id"] for h in self._custom_hubs}
        hub_id = next(f"c{n}" for n in range(1, len(taken) + 2) if f"c{n}" not in taken)
        self._custom_hubs.append({"id": hub_id, "name": name})
        self.save()
        return hub_id

    def rename_hub(self, hub, name):
        """Rename a hub; for a built-in one an empty name restores its own."""
        name = name.strip()
        custom = next((h for h in self._custom_hubs if h["id"] == hub), None)
        if custom is not None:
            if not name:
                raise ValueError("A hub needs a name")
            custom["name"] = name
        elif hub in HUBS:
            if name:
                self._hub_names[hub] = name
            else:
                self._hub_names.pop(hub, None)
        else:
            raise ValueError(f"Unknown hub {hub!r}")
        self.save()

    def remove_hub(self, hub):
        """Remove one of the user's hubs; its catalogs go back to their usual
        hubs. (A built-in hub can't be removed: it disappears when empty.)"""
        if not self.is_custom_hub(hub):
            raise ValueError(f"Not a hub of your own: {hub!r}")
        self._custom_hubs = [h for h in self._custom_hubs if h["id"] != hub]
        for addon in self._addons:
            for prefs in addon.catalog_prefs.values():
                if prefs.hub == hub:
                    prefs.hub = ""
        self.save()

    def hubs_in_use(self):
        return [hub for hub in self.hubs() if self.hub_catalogs(hub)]

    def pinned_catalogs(self):
        """Catalogs the user pinned to the front page, in browsing order."""
        return [(a, c) for a, c in self._browsable_in_order()
                if a.prefs_for(c).front is True and a.prefs_for(c).home]

    def move_in_hub(self, key, delta):
        """Move a catalog `delta` places within its hub (clamped); other hubs keep
        their order. Returns the new index within the hub."""
        order = [self.search_key(a, c) for a, c in self._browsable_in_order()]
        if key not in order:
            raise UnknownAddonError(key)
        addon_key, catalog_key = key.split("|", 1)
        addon = self.get(addon_key)
        hub = addon.hub_of(next(c for c in addon.manifest.catalogs if c.key == catalog_key))
        slots = [i for i, k in enumerate(order) if self._hub_of_key(k) == hub]
        members = [order[i] for i in slots]
        index = members.index(key)
        new_index = max(0, min(len(members) - 1, index + delta))
        members.insert(new_index, members.pop(index))
        for slot, member in zip(slots, members):
            order[slot] = member
        self._hub_order = order
        self.save()
        return new_index

    def _hub_of_key(self, key):
        addon_key, catalog_key = key.split("|", 1)
        addon = self.get(addon_key)
        catalog = next((c for c in addon.manifest.catalogs if c.key == catalog_key), None)
        return addon.hub_of(catalog) if catalog else None

    def search_entries(self):
        """Every catalog that can be searched (of enabled addons), in the
        user's search order, as ``(addon, catalog, enabled)``. Catalogs not
        ordered yet (e.g. newly added) follow, in addon order."""
        entries = [(a, c) for a in self.enabled() for c in a.manifest.catalogs if c.is_searchable_alone]
        rank = {key: i for i, key in enumerate(self._search_order)}
        ordered = sorted(enumerate(entries),
                         key=lambda e: (rank.get(self.search_key(*e[1]), len(rank)), e[0]))
        return [(a, c, a.prefs_for(c).search) for _, (a, c) in ordered]

    def search_catalogs(self, type_=None):
        """``(addon, catalog)`` pairs used for search, in the user's search
        order. With a type, catalogs of type "all" are included too (filter
        their results)."""
        return [
            (addon, catalog)
            for addon, catalog, enabled in self.search_entries()
            if enabled and (type_ is None or catalog.type in (type_, "all"))
        ]

    def move_search_catalog(self, key, delta):
        """Move a search catalog `delta` places (clamped). Returns its new index."""
        order = [self.search_key(a, c) for a, c, _ in self.search_entries()]
        if key not in order:
            raise UnknownAddonError(key)
        index = order.index(key)
        new_index = max(0, min(len(order) - 1, index + delta))
        order.insert(new_index, order.pop(index))
        self._search_order = order
        self.save()
        return new_index

    def front_catalogs(self):
        """``(addon, catalog)`` pairs for the add-on's front page, in user order."""
        return [(addon, catalog) for addon in self.enabled() for catalog in addon.front_catalogs()]

    def stale_addons(self, max_age, now=None):
        """Addons whose manifest was fetched more than `max_age` seconds ago."""
        now = time.time() if now is None else now
        return [a for a in self._addons if now - a.fetched_at > max_age]

    def home_types(self):
        """Types that have at least one home catalog: movie, series first, then A-Z."""
        types = {catalog.type for _, catalog in self.home_catalogs()}
        preferred = [t for t in ("movie", "series") if t in types]
        return preferred + sorted(types - set(preferred))

    # Mutations (each one saves)

    def add(self, transport_url, manifest):
        if any(a.transport_url == transport_url for a in self._addons):
            raise DuplicateAddonError(transport_url)
        addon = InstalledAddon(transport_url=transport_url, manifest=manifest, fetched_at=time.time())
        self._addons.append(addon)
        self.save()
        return addon

    def remove(self, ref):
        addon = self.get(ref)
        self._addons.remove(addon)
        self.save()
        return addon

    def move(self, ref, delta):
        """Move an addon `delta` places (negative = up). Clamped to the list bounds."""
        addon = self.get(ref)
        index = self._addons.index(addon)
        new_index = max(0, min(len(self._addons) - 1, index + delta))
        if new_index != index:
            self._addons.insert(new_index, self._addons.pop(index))
            self.save()
        return new_index

    def set_enabled(self, ref, enabled):
        self.get(ref).enabled = enabled
        self.save()

    def update_manifest(self, ref, manifest, fetched_at=None):
        addon = self.get(ref)
        addon.manifest = manifest
        addon.fetched_at = time.time() if fetched_at is None else fetched_at
        # Drop prefs for catalogs the addon no longer offers.
        keys = {c.key for c in manifest.catalogs}
        addon.catalog_prefs = {k: p for k, p in addon.catalog_prefs.items() if k in keys}
        self.save()
        return addon

    def set_catalog_pref(self, ref, catalog_key, home=None, search=None, front=None, search_name=None,
                         name=None, hub=None):
        """Change one catalog's preferences; None leaves a field as it is
        (``search_name=""`` / ``name=""`` restore the addon's own name,
        ``hub=""`` the default hub)."""
        addon = self.get(ref)
        prefs = addon.catalog_prefs.get(catalog_key) or CatalogPrefs()
        if name is not None:
            prefs.name = name.strip()
        if hub is not None:
            prefs.hub = hub if hub in self.hubs() else ""
        if home is not None:
            prefs.home = home
        if search is not None:
            prefs.search = search
        if front is not None:
            prefs.front = front
        if search_name is not None:
            prefs.search_name = search_name.strip()
        addon.catalog_prefs[catalog_key] = prefs
        self.save()
        return prefs
