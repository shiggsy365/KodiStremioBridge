"""Build the stream picker's badges (and its card background).

    .venv/bin/python tools/make_badges.py [--sheet preview.png]

Badges with artwork come from tools/badges/nuvio (a Nuvio badge config and the
images it links to, see SOURCES.md): each is the image on its rule's coloured
tag. Badges the config doesn't cover are drawn as text in the same style.

Writes plugin.video.stremiobridge/resources/media/badges/<key>.png: 8-bit RGBA
with filter 0 on every row (stremio.badgestrip.png), so the add-on can join a
stream's badges into one strip at run time without an image library. All
badges share HEIGHT.
Also writes resources/skins/Default/media/stremiobridge-card.png. Needs Pillow.
"""

import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin.video.stremiobridge",
                                "resources", "lib"))
from stremio.badgestrip import png  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NUVIO = os.path.join(HERE, "badges", "nuvio")
ROOT = os.path.join(HERE, "..", "plugin.video.stremiobridge", "resources")
OUT = os.path.join(ROOT, "media", "badges")
FONTS = ["/usr/share/fonts/truetype/noto/NotoSans-Black.ttf", "/usr/share/fonts/truetype/noto/NotoSans-ExtraBold.ttf",
         "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"]
HEIGHT = 64            # shown at 32 px in the 1080i skin
RADIUS, BORDER = 12, 3
GLYPH_HEIGHT = 40      # artwork height inside the tag
PAD_X = 16

# Nuvio rule name -> our key (rules that are switched off in the config are skipped)
NUVIO_KEYS = {
    "Remux": "rel_remux", "BluRay": "rel_bluray", "WebDL": "rel_webdl", "SeaDex": "extra_seadex",
    "4K": "res_4k", "1080p": "res_1080p", "720p": "res_720p", "DV": "vis_dv", "HDR10+": "vis_hdr10plus",
    "HDR10": "vis_hdr10", "HDR": "vis_hdr", "IMAX Enhanced": "extra_imax_enhanced", "IMAX": "extra_imax",
    "TrueHD": "aud_truehd", "Atmos": "aud_atmos", "DTS:X": "aud_dtsx", "DTS-HD MA": "aud_dtshdma",
    "DTS-HD": "aud_dtshd", "DTS": "aud_dts", "DD+": "aud_ddp", "DD": "aud_dd",
    "7.1": "ch_71", "6.1": "ch_61", "5.1": "ch_51",
}

BLACK, WHITE = (14, 14, 14), (255, 255, 255)
QUALITY_FILL, QUALITY_TEXT = (0x00, 0xE9, 0x32, 0xE6), (6, 70, 24)
YELLOW = (0xFF, 0xBE, 0x01, 255)
# Text badges in the same style: key -> (text, fill RGBA, border RGB or None, text RGB, icon)
TEXT_BADGES = {
    "dl_cached": ("Cached", (46, 204, 113, 255), None, BLACK, "bolt"),
    "dl_p2p": ("P2P", (245, 166, 35, 255), None, BLACK, None),
    "res_1440p": ("1440p", (0xFF, 0x95, 0x00, 255), None, BLACK, None),
    "res_sd": ("SD", (138, 138, 138, 255), None, BLACK, None),
    "rel_webrip": ("WEBRip", QUALITY_FILL, None, QUALITY_TEXT, None),
    "rel_hdtv": ("HDTV", QUALITY_FILL, None, QUALITY_TEXT, None),
    "rel_dvd": ("DVD", QUALITY_FILL, None, QUALITY_TEXT, None),
    "rel_cam": ("CAM", (231, 76, 60, 255), None, WHITE, None),
    "vis_sdr": ("SDR", (138, 138, 138, 255), None, BLACK, None),
    "extra_3d": ("3D", YELLOW, None, BLACK, None),
    "aud_aac": ("AAC", (255, 255, 255, 255), None, BLACK, None),
    "aud_flac": ("FLAC", (255, 255, 255, 255), None, BLACK, None),
    "aud_opus": ("OPUS", (255, 255, 255, 255), None, BLACK, None),
    "aud_mp3": ("MP3", (255, 255, 255, 255), None, BLACK, None),
    "aud_pcm": ("PCM", (255, 255, 255, 255), None, BLACK, None),
    "ch_20": ("2.0", (255, 255, 255, 255), None, BLACK, None),
    "ch_10": ("1.0", (255, 255, 255, 255), None, BLACK, None),
    # Streaming services: their colours with a plain wordmark, not their logos.
    "net_netflix": ("NETFLIX", (229, 9, 20, 255), None, WHITE, None),
    "net_amazon": ("prime video", (0, 168, 225, 255), None, WHITE, None),
    "net_appletv": ("Apple TV+", (30, 30, 30, 255), (200, 200, 200), WHITE, None),
    "net_disney": ("Disney+", (17, 60, 207, 255), None, WHITE, None),
    "net_max": ("max", (0, 43, 231, 255), None, WHITE, None),
    "net_hulu": ("hulu", (28, 231, 131, 255), None, BLACK, None),
    "net_peacock": ("peacock", (20, 20, 20, 255), (245, 197, 24), WHITE, None),
    "net_paramount": ("Paramount+", (0, 100, 255, 255), None, WHITE, None),
    "net_crunchyroll": ("Crunchyroll", (244, 117, 33, 255), None, WHITE, None),
}


def colour(value):
    """'#RRGGBB' or '#AARRGGBB' -> RGBA."""
    value = value.lstrip("#")
    if len(value) == 8:
        return tuple(int(value[i:i + 2], 16) for i in (2, 4, 6, 0))
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def font(size):
    for path in FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("No Noto Sans font found")


def tag(width, fill, border):
    image = Image.new("RGBA", (width, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        [1, 1, width - 2, HEIGHT - 2], radius=RADIUS, fill=fill,
        outline=border if border else None, width=BORDER if border else 0)
    return image


def artwork_badge(path, fill, border):
    glyph = Image.open(path).convert("RGBA")
    box = glyph.getchannel("A").getbbox()
    if box:
        glyph = glyph.crop(box)
    glyph = glyph.resize((max(1, round(glyph.width * GLYPH_HEIGHT / glyph.height)), GLYPH_HEIGHT), Image.LANCZOS)
    image = tag(glyph.width + 2 * PAD_X, fill, border)
    image.alpha_composite(glyph, (PAD_X, (HEIGHT - GLYPH_HEIGHT) // 2))
    return image


def text_badge(text, fill, border, text_colour, icon):
    face = font(37)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text_width = round(probe.textlength(text, font=face))
    icon_width = 26 if icon else 0
    image = tag(text_width + icon_width + 2 * PAD_X, fill, border and (*border, 255))
    draw = ImageDraw.Draw(image)
    x, cy = PAD_X, HEIGHT // 2
    if icon == "bolt":
        draw.polygon([(x + 12, cy - 17), (x + 1, cy + 3), (x + 10, cy + 3), (x + 6, cy + 17), (x + 19, cy - 4),
                      (x + 10, cy - 4)], fill=(*text_colour, 255))
    draw.text((x + icon_width, cy), text, font=face, fill=(*text_colour, 255), anchor="lm")
    return image


def save_badge(image, path):
    image = image.convert("RGBA")
    raw = image.tobytes()
    stride = image.width * 4
    rows = b"".join(b"\0" + raw[y * stride:(y + 1) * stride] for y in range(image.height))
    with open(path, "wb") as f:
        f.write(png(image.width, image.height, rows))


def card(path):
    """White rounded rectangle; the skin tints and stretches it (border="24")."""
    s = 4
    image = Image.new("RGBA", (96 * s, 96 * s), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle([0, 0, 96 * s - 1, 96 * s - 1], radius=22 * s, fill=(255, 255, 255, 255))
    image.resize((96, 96), Image.LANCZOS).save(path, optimize=True)


def build():
    config = json.load(open(os.path.join(NUVIO, "Nuvio.json"), encoding="utf-8"))
    badges = {}
    for rule in config["filters"]:
        key = NUVIO_KEYS.get(rule["name"])
        if key and rule.get("isEnabled") and rule.get("imageURL"):
            bordered = "bordered" in rule.get("tagStyle", "")
            badges[key] = artwork_badge(os.path.join(NUVIO, "images", key + ".png"), colour(rule["tagColor"]),
                                        colour(rule["borderColor"]) if bordered else None)
    for key, spec in TEXT_BADGES.items():
        badges.setdefault(key, text_badge(*spec))
    return badges


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        os.remove(os.path.join(OUT, name))
    badges = build()
    for key, image in badges.items():
        save_badge(image, os.path.join(OUT, key + ".png"))
    print(f"{len(badges)} badges in {os.path.normpath(OUT)}")
    card(os.path.join(ROOT, "skins", "Default", "media", "stremiobridge-card.png"))
    if "--sheet" in sys.argv:  # preview of every badge
        sheet = Image.new("RGBA", (1400, 60 + (len(badges) // 6 + 1) * 80), (24, 28, 34, 255))
        x = y = 20
        for key in sorted(badges):
            image = badges[key]
            if x + image.width > 1380:
                x, y = 20, y + 80
            sheet.alpha_composite(image, (x, y))
            x += image.width + 16
        sheet.save(sys.argv[sys.argv.index("--sheet") + 1])
