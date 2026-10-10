"""Fetching and paging catalogs."""

import re

from . import AddonRequestError
from .aggregate import gather
from .models import MetaPreview

# Addons don't advertise a page size, and many (Cinemeta included) drop a few
# items from each page server-side, e.g. 49 or 48 items from a page of 50. So we
# round the first page's count up to a common page size, advance `skip` by that
# nominal size, and treat a page as full if it is at least mostly filled.
COMMON_PAGE_SIZES = (10, 20, 25, 30, 40, 50, 60, 75, 100, 150, 200, 250, 500)
FULL_PAGE_RATIO = 0.8
MIN_FULL_PAGE = 10


def find_catalog(addon, type_, catalog_id):
    for catalog in addon.manifest.catalogs:
        if catalog.type == type_ and catalog.id == catalog_id:
            return catalog
    return None


# Some addons generate catalogs whose id ends in a changing number, e.g. BingeCat's
# "because you watched" rows: aicat_because_watched_movie_seed_1435 ("The Batman")
# later becomes ..._seed_<another film>. Catalogs sharing the part before that
# number form a "family", and links remember their position (slot) within it.
_NUMBERED = re.compile(r"^(.+?)[_.-]\d+$")


def catalog_family(catalog_id):
    """The id without its trailing number, or None if it doesn't end in one."""
    match = _NUMBERED.match(catalog_id)
    return match.group(1) if match else None


def family_members(addon, type_, family):
    return [c for c in addon.manifest.catalogs if c.type == type_ and catalog_family(c.id) == family]


def catalog_slot(addon, catalog):
    """``(slot, family_size)`` for `catalog`, or None for un-numbered ids."""
    family = catalog_family(catalog.id)
    if family is None:
        return None
    members = family_members(addon, catalog.type, family)
    return (members.index(catalog), len(members)) if catalog in members else None


def resolve_catalog(addon, type_, catalog_id, slot=None, family_size=None):
    """The catalog with this exact id; failing that, whatever catalog now holds
    the same slot in its family, provided the family still has `family_size`
    members. Rotation keeps the size the same; if the size changed, catalogs
    were added or removed and the slot no longer means the same thing."""
    catalog = find_catalog(addon, type_, catalog_id)
    if catalog is not None or slot is None:
        return catalog
    family = catalog_family(catalog_id)
    members = family_members(addon, type_, family) if family else []
    if family_size is not None and len(members) != family_size:
        return None
    return members[slot] if 0 <= slot < len(members) else None


def with_default_filters(catalog, filters):
    """`filters` plus the first option of every required filter the user hasn't
    set, which is what Stremio does (e.g. Cinemeta's "by year" opens on the
    latest year, and many addons list "None" first)."""
    merged = dict(filters)
    for extra in catalog.filters:
        if extra.is_required and not merged.get(extra.name):
            merged[extra.name] = extra.options[0]
    return merged


def ordered_extra(catalog, filters, skip=0):
    """Extra args in manifest order, which some addons rely on."""
    values = {k: v for k, v in filters.items() if v}
    if skip:
        values["skip"] = skip
    ordered = [(e.name, values.pop(e.name)) for e in catalog.extra if e.name in values]
    return ordered + list(values.items())


def fetch_catalog(client, addon, catalog, filters=None, skip=0, refresh=False, stale_ok=False):
    """`stale_ok`: see StremioClient.get_resource."""
    data = client.get_resource(
        addon.transport_url, "catalog", catalog.type, catalog.id,
        ordered_extra(catalog, filters or {}, skip), refresh=refresh, stale_ok=stale_ok,
    )
    metas = data.get("metas") if isinstance(data, dict) else None
    if not isinstance(metas, list):
        raise AddonRequestError(f"{addon.name} returned no 'metas' for {catalog.key}")
    return [p for p in (MetaPreview.from_dict(m, catalog.type) for m in metas) if p]


def nominal_page_size(count):
    return next((size for size in COMMON_PAGE_SIZES if size >= count), count)


def next_page(catalog, skip, count, page_size=None):
    """``(next_skip, page_size)`` if there is probably another page, else None.

    `page_size` is the size settled on for the first page and carried in the
    URL; pass None on the first page. A declared ``pageSize`` is only trusted if
    the first page is consistent with it: some addons declare 50 but serve 20,
    and stepping by 50 would silently skip 30 items per page.
    """
    if not catalog.supports_paging or count == 0:
        return None
    if page_size:
        size = page_size
    elif catalog.page_size and count >= catalog.page_size * FULL_PAGE_RATIO:
        size = catalog.page_size
    elif count < MIN_FULL_PAGE:
        return None
    else:
        size = nominal_page_size(count)
    if count < size * FULL_PAGE_RATIO:
        return None
    return skip + size, size


def search(client, targets, query, on_progress=None):
    """Search ``(addon, catalog)`` targets at once.

    Returns ``(results, errors, cancelled)`` where results are
    ``(addon, catalog, previews)`` in target order, including empty ones.
    """
    def task(addon, catalog):
        return lambda: fetch_catalog(client, addon, catalog, {"search": query})

    results, errors, cancelled = gather(
        [(f"{a.name} / {c.name}", task(a, c)) for a, c in targets], on_progress
    )
    return [(*targets[i], previews) for i, previews in results], errors, cancelled
