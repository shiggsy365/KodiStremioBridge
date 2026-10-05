"""Generate the add-ons' icons and fanart.

    .venv/bin/python tools/make_branding.py

From branding/logo.png (the Kodi Stremio Bridge logo; replace it with a larger
original for sharper results and run this again): Stremio Bridge's
resources/{icon.png,fanart.jpg}, repository.shiggsy365/icon.png, Arctic Zephyr
Stremio's icon.png, fanart.jpg, media/misc/stremio-bridge-logo.png (its startup
screen) and media/misc/matrix.png (the "Stremio" wordmark beside "Arctic Zephyr"
in its settings). Drawn here: service.shiggsy365.tidycache/resources/icon.png
script.shiggsy365.dispatcharrbridge/resources/icon.png and Podcasts'
resources/{icon.png,fanart.jpg}.
Needs Pillow.
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ACCENT = (18, 160, 199)        # the add-on's accent blue (FF12A0C7)
TOP, BOTTOM = (28, 40, 54), (10, 14, 20)
ROOT = os.path.join(os.path.dirname(__file__), "..")
LOGO = os.path.join(ROOT, "branding", "logo.png")
# Bold fonts for the wordmark, first one found
WORDMARK_FONTS = ("/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


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


def podcast_mark(draw, cx, cy, u):
    """A microphone between broadcast arcs, centred on (cx, cy); `u` is the unit size."""
    draw.rounded_rectangle([cx - 0.11 * u, cy - 0.30 * u, cx + 0.11 * u, cy + 0.06 * u], radius=0.11 * u,
                           fill=(255, 255, 255))
    draw.arc([cx - 0.19 * u, cy - 0.20 * u, cx + 0.19 * u, cy + 0.17 * u], 0, 180, fill=ACCENT, width=int(0.04 * u))
    draw.line([(cx, cy + 0.17 * u), (cx, cy + 0.30 * u)], fill=ACCENT, width=int(0.04 * u))
    draw.line([(cx - 0.12 * u, cy + 0.30 * u), (cx + 0.12 * u, cy + 0.30 * u)], fill=ACCENT, width=int(0.04 * u))
    for r in (0.30, 0.42):  # arcs either side
        box = [cx - r * u, cy - 0.12 * u - r * u, cx + r * u, cy - 0.12 * u + r * u]
        draw.arc(box, 140, 220, fill=(255, 255, 255), width=int(0.035 * u))
        draw.arc(box, -40, 40, fill=(255, 255, 255), width=int(0.035 * u))


def podcast_icon(path):
    """Podcasts: a microphone with broadcast arcs."""
    scale, size = 4, 512
    s = size * scale
    image = gradient(s, s)
    glow(image, [s * 0.1, s * 0.05, s * 0.9, s * 0.8], 90, 70 * scale)
    podcast_mark(ImageDraw.Draw(image), s * 0.5, s * 0.5, s)
    image.resize((size, size), Image.LANCZOS).save(path, optimize=True)
    print(os.path.normpath(path))


def podcast_fanart(path):
    scale, w, h = 2, 1920, 1080
    image = gradient(w * scale, h * scale)
    glow(image, [w * scale * 0.55, -h * scale * 0.2, w * scale * 1.1, h * scale * 0.9], 70, 90 * scale)
    podcast_mark(ImageDraw.Draw(image), w * scale * 0.76, h * scale * 0.46, h * scale * 0.62)
    image.resize((w, h), Image.LANCZOS).save(path, quality=88, optimize=True)
    print(os.path.normpath(path))


def on_logo(size, logo_scale, dim=1.0):
    """The logo, `logo_scale` times its size, centred on a canvas of `size`
    in the colour of the logo's own edges (it fades into it)."""
    logo = Image.open(LOGO).convert("RGB")
    w, h = size
    border = [logo.getpixel((x, y)) for x in range(logo.width) for y in (0, logo.height - 1)]
    border += [logo.getpixel((x, y)) for y in range(logo.height) for x in (0, logo.width - 1)]
    edge_colour = tuple(sum(c[i] for c in border) // len(border) for i in range(3))
    back = Image.new("RGB", (w, h), edge_colour)
    front = logo.resize((round(logo.width * logo_scale), round(logo.height * logo_scale)), Image.LANCZOS)
    mask = Image.new("L", front.size, 0)
    feather = max(4, round(min(front.size) * 0.18))
    ImageDraw.Draw(mask).rectangle([feather, feather, front.width - feather, front.height - feather], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
    back.paste(front, ((w - front.width) // 2, (h - front.height) // 2), mask)
    if dim < 1:
        back = Image.blend(Image.new("RGB", back.size, (0, 0, 0)), back, dim)
    return back


def logo_icon(path):
    """512 x 512: the logo's mark and name fill the square."""
    on_logo((512, 512), 1.45).save(path, optimize=True)
    print(os.path.normpath(path))


def logo_fanart(path):
    on_logo((1920, 1080), 2.0, dim=0.85).save(path, quality=88, optimize=True)
    print(os.path.normpath(path))


def startup_logo(path):
    """The skin's startup screen: the logo as it is."""
    Image.open(LOGO).convert("RGB").save(path, optimize=True)
    print(os.path.normpath(path))


def wordmark(path, text="Stremio", size=(331, 60)):
    """White bold text on transparent, in place of Arctic: Zephyr - Reloaded's
    "Reloaded" badge (the skin shows it scaled to fit, beside "Arctic Zephyr")."""
    font_path = next(p for p in WORDMARK_FONTS if os.path.exists(p))
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    points = size[1]
    while True:
        font = ImageFont.truetype(font_path, points)
        try:  # a variable font (Ubuntu-B.ttf links to one): its Bold instance
            names = [n.decode() if isinstance(n, bytes) else n for n in font.get_variation_names()]
            font.set_variation_by_name(next(n for n in names if n.lower() == "bold"))
        except (OSError, StopIteration, AttributeError):
            pass
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        if right - left <= size[0] - 4 and bottom - top <= size[1] - 4:
            break
        points -= 1
    draw.text(((size[0] - (right - left)) / 2 - left, (size[1] - (bottom - top)) / 2 - top), text,
              font=font, fill=(255, 255, 255, 255))
    image.save(path, optimize=True)
    print(os.path.normpath(path))


if __name__ == "__main__":
    resources = os.path.join(ROOT, "plugin.video.stremiobridge", "resources")
    logo_icon(os.path.join(resources, "icon.png"))
    logo_fanart(os.path.join(resources, "fanart.jpg"))
    logo_icon(os.path.join(ROOT, "repository.shiggsy365", "icon.png"))
    skin = os.path.join(ROOT, "skin.arctic.zephyr.stremio")
    logo_icon(os.path.join(skin, "icon.png"))
    logo_fanart(os.path.join(skin, "fanart.jpg"))
    startup_logo(os.path.join(skin, "media", "misc", "stremio-bridge-logo.png"))
    wordmark(os.path.join(skin, "media", "misc", "matrix.png"))
    tidy_icon(os.path.join(ROOT, "service.shiggsy365.tidycache", "resources", "icon.png"))
    switch_icon(os.path.join(ROOT, "script.shiggsy365.dispatcharrbridge", "resources", "icon.png"))
    podcasts = os.path.join(ROOT, "plugin.audio.shiggsy365.podcasts", "resources")
    podcast_icon(os.path.join(podcasts, "icon.png"))
    podcast_fanart(os.path.join(podcasts, "fanart.jpg"))
