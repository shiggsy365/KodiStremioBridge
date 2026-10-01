"""Generate the white action icons used in the Extended info page.

    .venv/bin/python tools/make_icons.py

Writes plugin.video.stremiobridge/resources/media/icons/<name>.png (128x128,
white on transparent; the tile behind supplies the colour). Needs Pillow.
"""

import os

from PIL import Image, ImageDraw

SIZE, SCALE = 128, 4
S = SIZE * SCALE
WHITE = (255, 255, 255, 255)
LINE = 9 * SCALE
OUT = os.path.join(os.path.dirname(__file__), "..", "plugin.video.stremiobridge", "resources", "media", "icons")


def px(v):
    return v * SCALE


def canvas():
    image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def badge(draw, plus):
    """A small filled circle bottom-right with + or -."""
    cx, cy, r = px(96), px(96), px(24)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    bar = px(12)
    clear = (0, 0, 0, 0)
    draw.rectangle([cx - bar, cy - px(3), cx + bar, cy + px(3)], fill=clear)
    if plus:
        draw.rectangle([cx - px(3), cy - bar, cx + px(3), cy + bar], fill=clear)


def play(d):
    d.polygon([(px(40), px(26)), (px(104), px(64)), (px(40), px(102))], fill=WHITE)


def seasons(d):
    # Three overlapping cards, back to front.
    for i, (x, y) in enumerate(((44, 14), (29, 29), (14, 44))):
        box = [px(x), px(y), px(x + 70), px(y + 70)]
        if i < 2:
            d.rounded_rectangle(box, radius=px(9), outline=WHITE, width=px(7))
        else:
            d.rounded_rectangle(box, radius=px(9), fill=WHITE)


def streams(d):
    # A list of sources with a play button: choose a stream yourself.
    for y in (26, 56, 86):
        d.rounded_rectangle([px(14), px(y), px(70), px(y + 14)], radius=px(7), fill=WHITE)
    d.polygon([(px(82), px(38)), (px(118), px(63)), (px(82), px(88))], fill=WHITE)


def trailer(d):
    d.rounded_rectangle([px(16), px(28), px(112), px(100)], radius=px(10), outline=WHITE, width=LINE)
    for y in (38, 58, 78):
        d.rectangle([px(24), px(y), px(32), px(y + 10)], fill=WHITE)
        d.rectangle([px(96), px(y), px(104), px(y + 10)], fill=WHITE)
    d.polygon([(px(52), px(46)), (px(80), px(64)), (px(52), px(82))], fill=WHITE)


def director(d):
    # Clapperboard: body plus a slanted, striped arm.
    d.rounded_rectangle([px(18), px(52), px(110), px(106)], radius=px(8), outline=WHITE, width=LINE)
    arm = [(px(16), px(42)), (px(104), px(22)), (px(108), px(38)), (px(20), px(58))]
    d.polygon(arm, fill=WHITE)
    for x in (38, 60, 82):
        d.polygon([(px(x), px(38)), (px(x + 10), px(36)), (px(x + 2), px(50)), (px(x - 8), px(52))],
                  fill=(0, 0, 0, 0))


def watched(d):
    d.ellipse([px(14), px(14), px(114), px(114)], outline=WHITE, width=LINE)
    d.line([(px(40), px(66)), (px(57), px(83)), (px(90), px(47))], fill=WHITE, width=px(11), joint="curve")


def unwatched(d):
    # An eye, crossed out.
    d.chord([px(10), px(30), px(118), px(122)], start=200, end=340, fill=WHITE)
    d.chord([px(10), px(6), px(118), px(98)], start=20, end=160, fill=WHITE)
    d.chord([px(22), px(38), px(106), px(110)], start=200, end=340, fill=(0, 0, 0, 0))
    d.chord([px(22), px(18), px(106), px(90)], start=20, end=160, fill=(0, 0, 0, 0))
    d.ellipse([px(48), px(48), px(80), px(80)], fill=WHITE)
    d.line([(px(18), px(110)), (px(110), px(18))], fill=(0, 0, 0, 0), width=px(18))
    d.line([(px(22), px(106)), (px(106), px(22))], fill=WHITE, width=px(8))


def similar(d):
    d.rounded_rectangle([px(14), px(14), px(78), px(78)], radius=px(10), outline=WHITE, width=LINE)
    d.rounded_rectangle([px(50), px(50), px(114), px(114)], radius=px(10), fill=WHITE)


def books(d):
    for x0, x1, top in ((14, 38, 22), (44, 68, 30), (74, 98, 22)):
        d.rounded_rectangle([px(x0), px(top), px(x1), px(102)], radius=px(4), outline=WHITE, width=px(7))
    d.rectangle([px(10), px(104), px(104), px(112)], fill=WHITE)


def library_add(d):
    books(d)
    badge(d, plus=True)


def library_remove(d):
    books(d)
    badge(d, plus=False)


def bookmark(d):
    d.polygon([(px(26), px(14)), (px(86), px(14)), (px(86), px(112)), (px(56), px(88)), (px(26), px(112))],
              outline=WHITE, width=LINE)


def watchlist_add(d):
    bookmark(d)
    badge(d, plus=True)


def watchlist_remove(d):
    bookmark(d)
    badge(d, plus=False)


ICONS = {
    "play": play, "streams": streams, "seasons": seasons, "trailer": trailer, "director": director,
    "watched": watched, "unwatched": unwatched, "similar": similar,
    "library_add": library_add, "library_remove": library_remove,
    "watchlist_add": watchlist_add, "watchlist_remove": watchlist_remove,
}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, draw_icon in ICONS.items():
        image, draw = canvas()
        draw_icon(draw)
        path = os.path.normpath(os.path.join(OUT, name + ".png"))
        image.resize((SIZE, SIZE), Image.LANCZOS).save(path, optimize=True)
        print(path)
