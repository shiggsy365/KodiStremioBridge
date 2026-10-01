"""Join a stream's badges into one image for the picker: badges differ in
width, and a Kodi list item can't place a varying number of differently sized
images side by side. Plain Python (zlib only), since Kodi on Android has no
image library.

Badges are stored by tools/make_badges.py as ``<key>.png``: 8-bit RGBA PNGs
written with filter type 0 on every row (``png`` below), all the same height,
so reading one is just zlib. Strips are cached as PNGs named after their
badges, so each combination is built once.
"""

import hashlib
import os
import struct
import zlib

GAP = 12           # transparent pixels between badges
VERSION = "2"      # bump when the badge images change, so old strips aren't reused
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class BadgeStrips:
    def __init__(self, badge_dir, cache_dir):
        self.badge_dir = badge_dir
        self.cache_dir = cache_dir
        self._badges = {}

    def _badge(self, key):
        """``(width, height, rgba bytes)`` or None if there's no such badge."""
        if key not in self._badges:
            try:
                with open(os.path.join(self.badge_dir, key + ".png"), "rb") as f:
                    self._badges[key] = read_png(f.read())
            except (OSError, ValueError, zlib.error, struct.error):
                self._badges[key] = None
        return self._badges[key]

    def strip(self, keys):
        """Path of a PNG showing the badges for `keys` in order (unknown keys
        are skipped), or "" if none of them exist."""
        badges = [b for b in (self._badge(k) for k in keys) if b]
        if not badges:
            return ""
        known = [k for k in keys if self._badge(k)]
        name = hashlib.sha1(("|".join(known) + VERSION).encode()).hexdigest()[:16] + ".png"
        path = os.path.join(self.cache_dir, name)
        if not os.path.exists(path):
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(compose(badges))
            os.replace(tmp, path)
        return path


def compose(badges):
    """PNG bytes for badges ``[(width, height, rgba)]`` side by side, GAP apart."""
    height = max(h for _, h, _ in badges)
    width = sum(w for w, _, _ in badges) + GAP * (len(badges) - 1)
    gap = b"\0\0\0\0" * GAP
    rows = []
    for y in range(height):
        parts = []
        for index, (w, h, pixels) in enumerate(badges):
            if index:
                parts.append(gap)
            parts.append(pixels[y * w * 4:(y + 1) * w * 4] if y < h else b"\0\0\0\0" * w)
        rows.append(b"\0" + b"".join(parts))  # filter type 0 per row
    return png(width, height, b"".join(rows))


def read_png(data):
    """``(width, height, rgba bytes)`` from a PNG as ``png`` writes it (8-bit
    RGBA, filter 0 on every row); ValueError for anything else."""
    if data[:8] != _SIGNATURE:
        raise ValueError("not a PNG")
    position, idat, header = 8, [], None
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        position += 12 + length
    if not header or header[2:] != (8, 6, 0, 0, 0):
        raise ValueError("not an 8-bit RGBA, non-interlaced PNG")
    width, height = header[:2]
    raw = zlib.decompress(b"".join(idat))
    stride = width * 4 + 1
    if len(raw) != stride * height or any(raw[y * stride] for y in range(height)):
        raise ValueError("unexpected PNG layout (rows must use filter 0)")
    return width, height, b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(height))


def png(width, height, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
