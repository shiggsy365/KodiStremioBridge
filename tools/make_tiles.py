"""Generate the poster-shaped (2:3) tiles used for "Filter by Genre" and
"Next Page" / "Previous Page" in catalog lists.

    .venv/bin/python tools/make_tiles.py

Writes the tiles listed in TILES to plugin.video.stremiobridge/resources/media/.
Needs Pillow (dev only; the add-on just ships the PNGs).
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT = 500, 750
ACCENT = (18, 160, 199)        # the add-on's accent blue (FF12A0C7)
TOP, BOTTOM = (28, 40, 54), (10, 14, 20)
FONT = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
OUT = os.path.join(os.path.dirname(__file__), "..", "plugin.video.stremiobridge", "resources", "media")
SCALE = 3  # draw large, then downsample for smooth edges


def background():
    w, h = WIDTH * SCALE, HEIGHT * SCALE
    image = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(image)
    for y in range(h):
        t = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    # Soft accent glow behind the icon.
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([w * 0.15, h * 0.18, w * 0.85, h * 0.62], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(60 * SCALE))
    image.paste(Image.new("RGB", (w, h), ACCENT), mask=glow)
    # Thin frame.
    inset = 14 * SCALE
    draw.rounded_rectangle([inset, inset, w - inset, h - inset], radius=18 * SCALE,
                           outline=(*ACCENT, 255), width=3 * SCALE)
    return image, draw


def funnel(draw):
    s, cx, top = SCALE, WIDTH * SCALE / 2, 190 * SCALE
    half, neck, cone, stem = 125 * s, 30 * s, 140 * s, 95 * s
    draw.polygon([(cx - half, top), (cx + half, top), (cx + neck, top + cone), (cx - neck, top + cone)],
                 fill=ACCENT)
    draw.rounded_rectangle([cx - neck, top + cone - 2 * s, cx + neck, top + cone + stem], radius=10 * s,
                           fill=ACCENT)


def arrow(draw):
    s, cx, cy = SCALE, WIDTH * SCALE / 2, 300 * SCALE
    radius = 120 * s
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=ACCENT, width=14 * s)
    shaft = 70 * s
    draw.rectangle([cx - shaft, cy - 14 * s, cx + 18 * s, cy + 14 * s], fill=ACCENT)
    draw.polygon([(cx + 10 * s, cy - 55 * s), (cx + 75 * s, cy), (cx + 10 * s, cy + 55 * s)], fill=ACCENT)


def arrow_back(draw):
    s, cx, cy = SCALE, WIDTH * SCALE / 2, 300 * SCALE
    radius = 120 * s
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=ACCENT, width=14 * s)
    shaft = 70 * s
    draw.rectangle([cx - 18 * s, cy - 14 * s, cx + shaft, cy + 14 * s], fill=ACCENT)
    draw.polygon([(cx - 10 * s, cy - 55 * s), (cx - 75 * s, cy), (cx - 10 * s, cy + 55 * s)], fill=ACCENT)


def _p(v):
    return v * SCALE


def film(draw):  # Movies: a film frame
    cx, cy = WIDTH * SCALE / 2, _p(300)
    draw.rounded_rectangle([cx - _p(120), cy - _p(90), cx + _p(120), cy + _p(90)], radius=_p(16),
                           outline=ACCENT, width=_p(14))
    for y in (-60, -20, 20, 60):
        for x in (-100, 88):
            draw.rectangle([cx + _p(x), cy + _p(y) - _p(10), cx + _p(x + 12), cy + _p(y) + _p(10)], fill=ACCENT)
    draw.polygon([(cx - _p(30), cy - _p(45)), (cx + _p(48), cy), (cx - _p(30), cy + _p(45))], fill=ACCENT)


def television(draw):  # TV Shows
    cx, cy = WIDTH * SCALE / 2, _p(290)
    draw.rounded_rectangle([cx - _p(135), cy - _p(95), cx + _p(135), cy + _p(80)], radius=_p(18),
                           outline=ACCENT, width=_p(14))
    draw.rectangle([cx - _p(60), cy + _p(100), cx + _p(60), cy + _p(114)], fill=ACCENT)
    draw.line([(cx - _p(50), cy - _p(150)), (cx, cy - _p(100))], fill=ACCENT, width=_p(12))
    draw.line([(cx + _p(50), cy - _p(150)), (cx, cy - _p(100))], fill=ACCENT, width=_p(12))


def sparkle(draw):  # Anime: a four-point star with a small one
    def star(cx, cy, r):
        k = r * 0.28
        draw.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k), (cx, cy + r),
                      (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)], fill=ACCENT)
    star(WIDTH * SCALE / 2 - _p(15), _p(310), _p(125))
    star(WIDTH * SCALE / 2 + _p(105), _p(195), _p(50))


def grid_dots(draw):  # More
    cx, cy = WIDTH * SCALE / 2, _p(300)
    for dx in (-80, 0, 80):
        for dy in (-80, 0, 80):
            r = _p(24)
            draw.ellipse([cx + _p(dx) - r, cy + _p(dy) - r, cx + _p(dx) + r, cy + _p(dy) + r], fill=ACCENT)


def magnifier(draw):  # Search
    cx, cy, r = WIDTH * SCALE / 2 - _p(25), _p(280), _p(85)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=_p(18))
    draw.line([(cx + _p(62), cy + _p(62)), (cx + _p(140), cy + _p(140))], fill=ACCENT, width=_p(26))


def tiles4(draw):  # Widgets
    cx, cy = WIDTH * SCALE / 2, _p(300)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x0, y0 = cx + _p(10) * dx + (_p(-110) if dx < 0 else 0), cy + _p(10) * dy + (_p(-110) if dy < 0 else 0)
        draw.rounded_rectangle([x0, y0, x0 + _p(100), y0 + _p(100)], radius=_p(16),
                               fill=ACCENT if (dx, dy) != (1, 1) else None, outline=ACCENT, width=_p(12))


def tag(draw):  # Genres
    cx, cy = WIDTH * SCALE / 2, _p(300)
    points = [(cx - _p(120), cy - _p(70)), (cx + _p(50), cy - _p(70)), (cx + _p(125), cy),
              (cx + _p(50), cy + _p(70)), (cx - _p(120), cy + _p(70))]
    draw.polygon(points, fill=ACCENT)
    r = _p(18)
    draw.ellipse([cx + _p(45) - r, cy - r, cx + _p(45) + r, cy + r], fill=TOP)


def resume(draw):  # Continue Watching: play + progress bar
    cx, cy = WIDTH * SCALE / 2, _p(270)
    draw.polygon([(cx - _p(60), cy - _p(90)), (cx + _p(90), cy), (cx - _p(60), cy + _p(90))], fill=ACCENT)
    draw.rounded_rectangle([cx - _p(140), cy + _p(130), cx + _p(140), cy + _p(150)], radius=_p(10),
                           outline=ACCENT, width=_p(5))
    draw.rounded_rectangle([cx - _p(140), cy + _p(130), cx + _p(30), cy + _p(150)], radius=_p(10), fill=ACCENT)


def skip_next(draw):  # Next Up
    cx, cy = WIDTH * SCALE / 2, _p(300)
    draw.polygon([(cx - _p(120), cy - _p(90)), (cx + _p(10), cy), (cx - _p(120), cy + _p(90))], fill=ACCENT)
    draw.polygon([(cx - _p(10), cy - _p(90)), (cx + _p(120), cy), (cx - _p(10), cy + _p(90))], fill=ACCENT)
    draw.rectangle([cx + _p(110), cy - _p(90), cx + _p(132), cy + _p(90)], fill=ACCENT)


def bookmark(draw):  # Watchlist
    cx, top = WIDTH * SCALE / 2, _p(180)
    draw.polygon([(cx - _p(80), top), (cx + _p(80), top), (cx + _p(80), top + _p(250)), (cx, top + _p(185)),
                  (cx - _p(80), top + _p(250))], fill=ACCENT)


def folder(draw):  # a hub of your own
    cx, cy = WIDTH * SCALE / 2, _p(300)
    draw.polygon([(cx - _p(140), cy - _p(100)), (cx - _p(40), cy - _p(100)), (cx - _p(15), cy - _p(75)),
                  (cx + _p(140), cy - _p(75)), (cx + _p(140), cy + _p(100)), (cx - _p(140), cy + _p(100))],
                 fill=ACCENT)
    draw.rectangle([cx - _p(140), cy - _p(50), cx + _p(140), cy - _p(42)], fill=TOP)


def plus(draw):  # New hub
    cx, cy, r = WIDTH * SCALE / 2, _p(300), _p(120)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=_p(14))
    draw.rectangle([cx - _p(65), cy - _p(12), cx + _p(65), cy + _p(12)], fill=ACCENT)
    draw.rectangle([cx - _p(12), cy - _p(65), cx + _p(12), cy + _p(65)], fill=ACCENT)


def caption(draw, lines):
    s = SCALE
    font = ImageFont.truetype(FONT, 62 * s)
    y = 500 * s
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text(((WIDTH * s - width) / 2, y), line, font=font, fill=(255, 255, 255))
        y += 78 * s


def make(name, icon, lines):
    image, draw = background()
    icon(draw)
    caption(draw, lines)
    image = image.resize((WIDTH, HEIGHT), Image.LANCZOS)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.normpath(os.path.join(OUT, name))
    image.save(path, optimize=True)
    print(path)


TILES = [
    ("filter_genre.png", funnel, ["Filter by", "Genre"]),
    ("next_page.png", arrow, ["Next", "Page"]),
    ("previous_page.png", arrow_back, ["Previous", "Page"]),
    ("hub_movies.png", film, ["Movies"]),
    ("hub_tvshows.png", television, ["TV Shows"]),
    ("hub_anime.png", sparkle, ["Anime"]),
    ("hub_more.png", grid_dots, ["More"]),
    # Renamed and user-made hubs: no caption (the skin shows the hub's name).
    ("hub_movies_plain.png", film, []),
    ("hub_tvshows_plain.png", television, []),
    ("hub_anime_plain.png", sparkle, []),
    ("hub_more_plain.png", grid_dots, []),
    ("hub_custom.png", folder, []),
    ("hub_new.png", plus, ["New Hub"]),
    ("search.png", magnifier, ["Search"]),
    ("widgets.png", tiles4, ["Widgets"]),
    ("genres.png", tag, ["Genres"]),
    ("genre.png", tag, []),              # one genre: the skin shows its name
    ("continue.png", resume, ["Continue", "Watching"]),
    ("next_up.png", skip_next, ["Next Up"]),
    ("watchlist.png", bookmark, ["Watchlist"]),
]

if __name__ == "__main__":
    for name, icon, lines in TILES:
        make(name, icon, lines)
