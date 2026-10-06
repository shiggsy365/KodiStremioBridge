"""Generate the textures of Arctic Zephyr Stremio's info page and rounded artwork.

    .venv/bin/python tools/make_info_textures.py

Writes skin.arctic.zephyr.stremio/media/stremio/*.png: white shapes the skin
tints with colordiffuse (pills, tabs, rings), masks it applies with
``diffuse`` (round cast photos, rounded episode cards and posters) and the page's
shading. Drawn at 4x and scaled down for smooth edges. It also rounds the
corners of the home rows' artwork masks (media/diffuse/widget*.png, in place:
running it again changes nothing) and draws the rounded box, border and
focus frame that go with them. Needs Pillow.
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.dirname(__file__), "..")
MEDIA = os.path.join(ROOT, "skin.arctic.zephyr.stremio", "media")
OUT = os.path.join(MEDIA, "stremio")
RADIUS = 14  # corners of posters, thumbnails and their frames, at 1080p
SCALE = 4


def save(image, name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    image.save(path, optimize=True)
    print(os.path.normpath(path))


def rounded(size, radius, outline=0, fill=(255, 255, 255, 255)):
    """A white rounded rectangle (or its outline, `outline` px wide) on transparent."""
    w, h = size
    big = Image.new("RGBA", (w * SCALE, h * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    box = [0, 0, w * SCALE - 1, h * SCALE - 1]
    if outline:
        draw.rounded_rectangle(box, radius * SCALE, outline=fill, width=outline * SCALE)
    else:
        draw.rounded_rectangle(box, radius * SCALE, fill=fill)
    return big.resize(size, Image.LANCZOS)


def circle(diameter, ring=0):
    big = Image.new("RGBA", (diameter * SCALE,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    box = [0, 0, diameter * SCALE - 1, diameter * SCALE - 1]
    if ring:
        draw.ellipse(box, outline=(255, 255, 255, 255), width=ring * SCALE)
    else:
        draw.ellipse(box, fill=(255, 255, 255, 255))
    return big.resize((diameter, diameter), Image.LANCZOS)


def gradient(size, stops, vertical):
    """Black with alpha running through `stops` [(position 0-1, alpha 0-255), ...]."""
    w, h = size
    length = h if vertical else w
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for i in range(length):
        t = i / (length - 1)
        for (p0, a0), (p1, a1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                alpha = round(a0 + (a1 - a0) * (t - p0) / ((p1 - p0) or 1))
                break
        line = Image.new("RGBA", (w, 1) if vertical else (1, h), (0, 0, 0, alpha))
        image.paste(line, (0, i) if vertical else (i, 0))
    return image


def frame(size, radius, thickness):
    """A rounded frame: outer corners `radius`, inner `radius - thickness`."""
    outer = rounded(size, radius)
    w, h = size
    inner = rounded((w - 2 * thickness, h - 2 * thickness), max(radius - thickness, 1))
    hole = Image.new("L", size, 0)
    hole.paste(inner.getchannel("A"), (thickness, thickness))
    alpha = Image.composite(Image.new("L", size, 0), outer.getchannel("A"), hole)
    outer.putalpha(alpha)
    return outer


def fade_right(image):
    """`image` fading in from transparent at the left edge to opaque at the right."""
    w, h = image.size
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))  # 0 at left, 255 at right
    ramp = ramp.transpose(Image.FLIP_LEFT_RIGHT) if ramp.getpixel((0, 0)) > 128 else ramp
    alpha = Image.composite(image.getchannel("A"), Image.new("L", (w, h), 0), ramp)
    image.putalpha(alpha)
    return image


def round_mask(name, top_only=False):
    """Round the corners of an existing artwork mask, keeping its alpha (reflections, fades)."""
    path = os.path.join(MEDIA, "diffuse", name)
    image = Image.open(path).convert("LA")
    w, h = image.size
    shape = rounded((w, h + (RADIUS * 2 if top_only else 0)), RADIUS).getchannel("A").crop((0, 0, w, h))
    image.putalpha(Image.composite(image.getchannel("A"), Image.new("L", (w, h), 0), shape))
    image.save(path, optimize=True)
    print(os.path.normpath(path))


def osd_subtitles():
    """The OSD's Subtitles button in the style of its other buttons (osd/*.png):
    the ring of osd/play.png around the subtitles glyph of buttonsdialogs/subtitles.png."""
    ring = Image.open(os.path.join(MEDIA, "osd", "play.png")).convert("RGBA")
    w, h = ring.size
    hole = Image.new("L", (w * SCALE, h * SCALE), 0)
    ImageDraw.Draw(hole).ellipse([10 * SCALE, 10 * SCALE, (w - 10) * SCALE, (h - 10) * SCALE], fill=255)
    hole = hole.resize((w, h), Image.LANCZOS)
    ring.putalpha(Image.composite(Image.new("L", (w, h), 0), ring.getchannel("A"), hole))
    glyph = Image.open(os.path.join(MEDIA, "buttonsdialogs", "subtitles.png")).convert("RGBA")
    glyph = glyph.resize((24, round(24 * glyph.height / glyph.width)), Image.LANCZOS)
    ring.alpha_composite(glyph, ((w - glyph.width) // 2, (h - glyph.height) // 2))
    path = os.path.join(MEDIA, "osd", "subtitles.png")
    ring.save(path, optimize=True)
    print(os.path.normpath(path))


def main():
    save(rounded((132, 66), 33), "pill.png")                    # Play button, round buttons' hover
    save(rounded((60, 44), 10, outline=2), "badge-outline.png")  # age rating and IMDb rating pills
    save(rounded((60, 52), 14), "tab.png")                       # selected season
    save(rounded((494, 300), 14), "card-mask.png")               # episode cards
    save(rounded((494, 300), 14, outline=4), "card-focus.png")
    save(rounded((200, 300), 12), "poster-mask.png")             # similar titles
    save(rounded((200, 300), 12, outline=4), "poster-focus.png")
    save(circle(256), "circle.png")                              # cast photos, round buttons
    save(circle(256, ring=10), "ring.png")
    save(gradient((1, 300), [(0, 0), (0.25, 40), (0.6, 200), (1, 235)], vertical=True), "card-shade.png")
    save(gradient((1920, 1), [(0, 235), (0.45, 170), (0.75, 40), (1, 0)], vertical=False), "shade-left.png")
    # rounded artwork: the box behind a poster, its 1px border and the focus frame
    save(rounded((48, 48), RADIUS), "box-rounded.png")
    save(frame((48, 48), RADIUS, 1), "frame-1px.png")
    save(frame((48, 48), RADIUS, 4), "frame-4px.png")                # a focused card (stream picker, search)
    save(frame((64, 64), RADIUS + 4, 8), "focus-frame.png")
    save(fade_right(frame((64, 64), RADIUS + 4, 8)), "focus-frame-gradient.png")  # the frame's colour sweep
    for name in ("widgetposter_new.png", "widgetfanart_new.png", "widgetsquare_new.png"):
        round_mask(name)
    round_mask("widgetfanart_top.png", top_only=True)
    save(gradient((1, 1080), [(0, 60), (0.3, 0), (0.6, 90), (1, 245)], vertical=True), "shade-bottom.png")
    osd_subtitles()


if __name__ == "__main__":
    main()
