"""Streaming Catalogs (what's popular on Netflix, Disney+, Prime Video, Apple
TV+ and HBO Max in the UK), built in like Cinemeta: Arctic Zephyr Stremio's
info page uses it for "More on <service>" when a title is in one of them."""

from .aggregate import gather
from .catalog import fetch_catalog
from .models import Manifest
from .registry import InstalledAddon

STREAMING_URL = ("https://7a82163c306e-stremio-netflix-catalog-addon.baby-beamup.club/"
                 "bmZ4LGRucCxhbXAsYXRwLGhibTo6OjE3OTEwNjY4MzMxNzY6MDowOkdC/manifest.json")
# Plugin URLs name it with this key (it needn't be installed).
STREAMING_KEY = "streaming"
SERVICES = (("nfx", "Netflix"), ("dnp", "Disney+"), ("amp", "Prime Video"), ("atp", "Apple TV+"), ("hbm", "HBO Max"))

_MANIFEST = {
    "id": "pw.ers.netflix-catalog", "name": "Streaming Catalogs", "version": "builtin",
    "resources": ["catalog"], "types": ["movie", "series"], "idPrefixes": ["tt"],
    "catalogs": [{"type": type_, "id": service, "name": name}
                 for service, name in SERVICES for type_ in ("movie", "series")],
}


def streaming_addon():
    return InstalledAddon(STREAMING_URL, Manifest.from_dict(_MANIFEST))


def services_for(client, type_, id_):
    """``[(catalog id, service name)]`` of the services whose list has the title."""
    if type_ not in ("movie", "series") or not id_.startswith("tt"):
        return []
    addon = streaming_addon()
    catalogs = [c for c in addon.manifest.catalogs if c.type == type_]

    def has_it(catalog):
        return lambda: any(p.id == id_ for p in fetch_catalog(client, addon, catalog))

    results, _, _ = gather([(c.id, has_it(c)) for c in catalogs])
    found = {index for index, present in results if present}
    return [(c.id, c.name) for i, c in enumerate(catalogs) if i in found]
