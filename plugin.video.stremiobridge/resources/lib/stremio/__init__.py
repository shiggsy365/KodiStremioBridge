"""Stremio addon protocol support.

Nothing in this package may import Kodi modules (xbmc, xbmcgui, ...), so it can
be unit-tested with plain pytest.
"""


class StremioError(Exception):
    """Base error for everything raised by this package."""


class ManifestError(StremioError):
    """A manifest could not be fetched or is not a valid Stremio manifest."""


class AddonRequestError(StremioError):
    """A request to an addon failed (network error, bad status, bad JSON)."""
