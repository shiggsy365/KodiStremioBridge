"""Generate the add-on icon and fanart, and the repository add-on's icon.

    .venv/bin/python tools/make_branding.py

Writes plugin.video.stremiobridge/resources/{icon.png,fanart.jpg},
repository.shiggsy365/icon.png, service.shiggsy365.tidycache/resources/icon.png and
script.shiggsy365.globalsearch/resources/icon.png.
Needs Pillow.
"""

import os

from PIL import Image, ImageDraw, ImageFilter

ACCENT = (18, 160, 199)        # the add-on's accent blue (FF12A0C7)
TOP, BOTTOM = (28, 40, 54), (10, 14, 20)
ROOT = os.path.join(os.path.dirname(__file__), "..")


def gradient(w, h, top=TOP, bottom=BOTTOM):
    image = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(image)
    for y in range(h):
        t = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return image


def glow(image, box, strength, blur):
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=strength)
    image.paste(Image.new("RGB", image.size, ACCENT), mask=mask.filter(ImageFilter.GaussianBlur(blur)))


def bridge(draw, cx, deck_y, span, height, line, colour=ACCENT):
    """An arch bridge: deck, arch and hangers, centred on `cx`."""
    left, right = cx - span / 2, cx + span / 2
    draw.arc([left, deck_y - height, right, deck_y + height], start=180, end=360, fill=colour, width=line)
    draw.rounded_rectangle([left - line, deck_y - line / 2, right + line, deck_y + line / 2], radius=line / 2,
                           fill=colour)
    for i in range(1, 8):
        x = left + span * i / 8
        # Height of the arch above the deck at x (half ellipse).
        dx = (x - cx) / (span / 2)
        top = deck_y - height * (1 - dx * dx) ** 0.5
        draw.line([(x, top), (x, deck_y)], fill=colour, width=max(2, line // 3))


def play(draw, cx, cy, size, colour=(255, 255, 255)):
    draw.polygon([(cx - size * 0.45, cy - size * 0.55), (cx + size * 0.6, cy), (cx - size * 0.45, cy + size * 0.55)],
                 fill=colour)


def icon(path, repo=False):
    scale, size = 4, 512
    s = size * scale
    image = gradient(s, s)
    glow(image, [s * 0.1, s * 0.05, s * 0.9, s * 0.75], 90, 70 * scale)
    draw = ImageDraw.Draw(image)
    bridge(draw, s / 2, s * 0.68, s * 0.74, s * 0.40, 26 * scale)
    if repo:  # a box under the bridge: the repository
        box = [s * 0.36, s * 0.34, s * 0.64, s * 0.58]
        draw.rounded_rectangle(box, radius=18 * scale, fill=(255, 255, 255))
        draw.rectangle([s * 0.36, s * 0.42, s * 0.64, s * 0.44], fill=BOTTOM)
    else:
        play(draw, s / 2, s * 0.47, s * 0.17)
    image.resize((size, size), Image.LANCZOS).save(path, optimize=True)
    print(os.path.normpath(path))


def tidy_icon(path):
    """Tidy Cache: a broom with sparkles."""
    scale, size = 4, 512
    s = size * scale
    image = gradient(s, s)
    glow(image, [s * 0.1, s * 0.05, s * 0.9, s * 0.8], 90, 70 * scale)
    draw = ImageDraw.Draw(image)
    draw.line([(s * 0.72, s * 0.14), (s * 0.47, s * 0.56)], fill=(255, 255, 255), width=24 * scale)
    draw.polygon([(s * 0.36, s * 0.50), (s * 0.58, s * 0.62), (s * 0.44, s * 0.88), (s * 0.14, s * 0.72)],
                 fill=ACCENT)
    for i in range(1, 4):  # bristle lines
        t = i / 4
        top = (s * (0.36 + 0.22 * t), s * (0.50 + 0.12 * t))
        bottom = (s * (0.14 + 0.30 * t), s * (0.72 + 0.16 * t))
        draw.line([top, bottom], fill=BOTTOM, width=6 * scale)

    def star(cx, cy, r):
        k = r * 0.28
        draw.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k), (cx, cy + r),
                      (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)], fill=(255, 255, 255))
    star(s * 0.74, s * 0.66, s * 0.10)
    star(s * 0.84, s * 0.44, s * 0.05)
    image.resize((size, size), Image.LANCZOS).save(path, optimize=True)
    print(os.path.normpath(path))


def search_icon(path):
    """Global Search: rows of results behind a magnifier."""
    scale, size = 4, 512
    s = size * scale
    image = gradient(s, s)
    glow(image, [s * 0.1, s * 0.05, s * 0.9, s * 0.8], 90, 70 * scale)
    draw = ImageDraw.Draw(image)
    for row, y in enumerate((0.20, 0.42, 0.64)):
        for col in range(3):
            x = 0.14 + col * 0.20
            draw.rounded_rectangle([s * x, s * y, s * (x + 0.16), s * (y + 0.16)], radius=10 * scale,
                                   fill=(*ACCENT, 255) if (row + col) % 2 == 0 else (60, 90, 110))
    cx, cy, r = s * 0.66, s * 0.60, s * 0.17
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=24 * scale)
    draw.line([(cx + r * 0.72, cy + r * 0.72), (cx + r * 1.75, cy + r * 1.75)], fill=(255, 255, 255), width=34 * scale)
    image.resize((size, size), Image.LANCZOS).save(path, optimize=True)
    print(os.path.normpath(path))


def switch_icon(path):
    """Dispatcharr Bridge: a TV with switching arrows."""
    scale, size = 4, 512
    s = size * scale
    image = gradient(s, s)
    glow(image, [s * 0.1, s * 0.05, s * 0.9, s * 0.8], 90, 70 * scale)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([s * 0.16, s * 0.22, s * 0.84, s * 0.68], radius=22 * scale, outline=ACCENT, width=22 * scale)
    draw.rectangle([s * 0.38, s * 0.74, s * 0.62, s * 0.78], fill=ACCENT)
    for y, direction in ((0.38, 1), (0.52, -1)):  # two arrows: source A <-> source B
        x0, x1 = (s * 0.32, s * 0.64) if direction > 0 else (s * 0.68, s * 0.36)
        draw.line([(x0, s * y), (x1, s * y)], fill=(255, 255, 255), width=14 * scale)
        tip = x1 + direction * 26 * scale
        draw.polygon([(tip, s * y), (x1 - direction * 6 * scale, s * y - 22 * scale),
                      (x1 - direction * 6 * scale, s * y + 22 * scale)], fill=(255, 255, 255))
    image.resize((size, size), Image.LANCZOS).save(path, optimize=True)
    print(os.path.normpath(path))


def fanart(path):
    w, h = 1920, 1080
    image = gradient(w, h, (22, 32, 44), (6, 9, 13))
    glow(image, [w * 0.2, h * 0.1, w * 0.8, h * 0.8], 60, 160)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bridge(ImageDraw.Draw(layer), w / 2, h * 0.70, w * 0.70, h * 0.42, 14, colour=(*ACCENT, 120))
    image.paste(layer, mask=layer)
    image.save(path, quality=88, optimize=True)
    print(os.path.normpath(path))


if __name__ == "__main__":
    resources = os.path.join(ROOT, "plugin.video.stremiobridge", "resources")
    icon(os.path.join(resources, "icon.png"))
    fanart(os.path.join(resources, "fanart.jpg"))
    repo_dir = os.path.join(ROOT, "repository.shiggsy365")
    os.makedirs(repo_dir, exist_ok=True)
    icon(os.path.join(repo_dir, "icon.png"), repo=True)
    tidy_icon(os.path.join(ROOT, "service.shiggsy365.tidycache", "resources", "icon.png"))
    search_icon(os.path.join(ROOT, "script.shiggsy365.globalsearch", "resources", "icon.png"))
    switch_icon(os.path.join(ROOT, "script.shiggsy365.dispatcharrbridge", "resources", "icon.png"))
