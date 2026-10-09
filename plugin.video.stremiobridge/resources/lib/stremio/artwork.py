"""Artwork at about the size it's shown at.

Addons often link TMDB's original images (a background can be 3840x2160 and
1.4 MB, a logo up to 5 MB) and TVDB's full-size ones, and Kodi downloads and
decodes each at full size: slow on a Fire TV Stick, and its thumbnail cache
fills with them. TMDB serves every image at set widths; TVDB serves a 640 px
copy of each v4 artwork (its name ending ``_t``), plenty for an episode card.
"""

import re

_TMDB = re.compile(r"^(https?://image\.tmdb\.org/t/p/)(original|w\d+)(/.+)$")
_TVDB_V4 = re.compile(r"^(https?://artworks\.thetvdb\.com/banners/v4/.+?)(\.(?:jpe?g|png))$", re.IGNORECASE)

# The widest each kind is shown at (1920x1080 screen): backgrounds fill it, behind a shade
TMDB_WIDTH = {"background": 1280, "poster": 500, "logo": 500, "thumbnail": 780}


def sized(url, kind):
    """`url` at the size a `kind` (background, poster, logo or thumbnail) needs."""
    if not url:
        return url
    match = _TMDB.match(url)
    if match:
        width = TMDB_WIDTH.get(kind)
        if width and (match.group(2) == "original" or int(match.group(2)[1:]) > width):
            return f"{match.group(1)}w{width}{match.group(3)}"
        return url
    if kind == "thumbnail":
        match = _TVDB_V4.match(url)
        if match and not match.group(1).endswith("_t"):
            return f"{match.group(1)}_t{match.group(2)}"
    return url
