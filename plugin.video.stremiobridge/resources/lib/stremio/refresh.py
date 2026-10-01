"""Keeping installed manifests up to date."""

import time

from .aggregate import gather

DAY = 86400


def refresh_manifests(client, registry, addons, now=None):
    """Re-fetch the manifests of `addons` at once and store the new ones.

    An addon that can't be reached keeps its current manifest (and is tried
    again next time). Returns ``(refreshed, errors)``: the refreshed addons and
    ``(addon_name, exception)`` pairs.
    """
    now = time.time() if now is None else now

    def task(addon):
        return lambda: client.fetch_manifest(addon.transport_url)[1]

    results, errors, _ = gather([(a.name, task(a)) for a in addons])
    refreshed = [registry.update_manifest(addons[i].key, manifest, fetched_at=now) for i, manifest in results]
    return refreshed, errors
