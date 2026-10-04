"""Finding the full meta object for an item."""

from . import AddonRequestError, StremioError
from .models import Manifest, Meta
from .registry import InstalledAddon

CINEMETA_URL = "https://v3-cinemeta.strem.io/manifest.json"
CINEMETA_NAME = "Cinemeta"


class MetaNotFound(StremioError):
    pass


_CINEMETA_MANIFEST = {
    "id": "com.linvo.cinemeta", "name": CINEMETA_NAME, "version": "builtin",
    "resources": ["catalog", "meta"], "types": ["movie", "series"], "idPrefixes": ["tt"],
    "catalogs": [
        {"type": "movie", "id": "top", "name": "Popular", "extra": [{"name": "search"}, {"name": "skip"}]},
        {"type": "series", "id": "top", "name": "Popular", "extra": [{"name": "search"}, {"name": "skip"}]},
        {"type": "movie", "id": "imdbRating", "name": "Featured", "extra": [{"name": "skip"}]},
        {"type": "series", "id": "imdbRating", "name": "Featured", "extra": [{"name": "skip"}]},
    ],
}
# Plugin URLs can name Cinemeta with this key even when it isn't installed
# (e.g. the widgets Arctic Zephyr Stremio ships).
CINEMETA_KEY = "cinemeta"


def cinemeta_addon():
    return InstalledAddon(CINEMETA_URL, Manifest.from_dict(_CINEMETA_MANIFEST))


def cinemeta_search_targets(type_=None):
    """``(addon, catalog)`` pairs for searching Cinemeta without it being
    installed (a fallback when the user's own search catalogs find nothing)."""
    addon = cinemeta_addon()
    return [(addon, c) for c in addon.manifest.catalogs
            if (type_ is None or c.type == type_) and c.extra_prop("search") is not None]


def cinemeta_fallback(type_, id_):
    """Cinemeta covers IMDb movies and series even when it isn't installed."""
    if type_ in ("movie", "series") and id_.startswith("tt"):
        return [(CINEMETA_NAME, CINEMETA_URL)]
    return []


def fetch_meta(client, addons, type_, id_, fallbacks=()):
    """Ask `addons` (then `fallbacks`) in order; return ``(source_name, Meta)``.

    `addons` are installed addons that support meta for this type/id; `fallbacks`
    are ``(name, transport_url)`` pairs tried afterwards unless already asked.
    Addons that fail or answer ``{"meta": null}`` are skipped.
    """
    name, data = fetch_meta_data(client, addons, type_, id_, fallbacks)
    return name, Meta.from_dict(data, type_)


def fetch_meta_data(client, addons, type_, id_, fallbacks=()):
    """Like fetch_meta, but ``(source_name, the addon's meta dict)``, for
    callers that keep a slimmed copy of it (see slim_meta)."""
    candidates = [(a.name, a.transport_url) for a in addons]
    asked = {url for _, url in candidates}
    candidates += [(name, url) for name, url in fallbacks if url not in asked]

    errors = []
    for name, url in candidates:
        try:
            data = client.get_resource(url, "meta", type_, id_)
        except AddonRequestError as exc:
            errors.append(f"{name}: {exc}")
            continue
        raw = data.get("meta") if isinstance(data, dict) else None
        if Meta.from_dict(raw, type_) is not None:
            return name, raw
        errors.append(f"{name}: no meta")
    raise MetaNotFound(f"No meta for {type_} {id_}" + (f" ({'; '.join(errors)})" if errors else ""))


def slim_meta(data, video_id=None):
    """A copy of an addon's meta dict keeping only the video `video_id` (or
    none): what a Continue Watching or Next Up entry needs, a fraction of the
    size of a long-running show's full episode list."""
    slim = dict(data)
    slim["videos"] = [v for v in data.get("videos") or [] if isinstance(v, dict) and video_id and v.get("id") == video_id]
    return slim
