"""Icons for Global Search's shortcut tiles that the source add-ons don't
provide in a matching style (white, solid, on transparent, 256x256, like
Spotify2's icon_music_*.png).

    .venv/bin/python tools/make_globalsearch_icons.py

Writes script.shiggsy365.globalsearch/resources/media/<name>.png. Needs Pillow.
"""

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "script.shiggsy365.globalsearch", "resources", "media")
SIZE, SCALE = 256, 4
WHITE, CLEAR = (255, 255, 255, 255), (0, 0, 0, 0)


def px(v):
    return v * SCALE


def microphone(d, cx=128, top=56, badge=False):
    # Capsule, holder arc, stem and base.
    d.rounded_rectangle([px(cx - 26), px(top), px(cx + 26), px(top + 82)], radius=px(26), fill=WHITE)
    d.arc([px(cx - 42), px(top + 26), px(cx + 42), px(top + 104)], start=0, end=180, fill=WHITE, width=px(9))
    d.rectangle([px(cx - 5), px(top + 103), px(cx + 5), px(top + 128)], fill=WHITE)
    d.rounded_rectangle([px(cx - 32), px(top + 124), px(cx + 32), px(top + 136)], radius=px(6), fill=WHITE)
    if badge:  # a play button: one episode
        bx, by, r = cx + 50, top + 104, 30
        d.ellipse([px(bx - r - 7), px(by - r - 7), px(bx + r + 7), px(by + r + 7)], fill=CLEAR)
        d.ellipse([px(bx - r), px(by - r), px(bx + r), px(by + r)], fill=WHITE)
        d.polygon([(px(bx - 9), px(by - 14)), (px(bx + 15), px(by)), (px(bx - 9), px(by + 14))], fill=CLEAR)


def podcasts(d):
    microphone(d)


def podcast_episodes(d):
    microphone(d, cx=112, badge=True)


def videos(d):
    d.rounded_rectangle([px(54), px(74), px(202), px(182)], radius=px(26), fill=WHITE)
    d.polygon([(px(110), px(102)), (px(156), px(128)), (px(110), px(154))], fill=CLEAR)


def channels(d):
    d.ellipse([px(96), px(56), px(160), px(120)], fill=WHITE)
    d.chord([px(60), px(130), px(196), px(250)], start=180, end=360, fill=WHITE)
    d.rectangle([px(60), px(188), px(196), px(196)], fill=WHITE)


def playlists(d):
    for y in (70, 104, 138):
        d.rounded_rectangle([px(56), px(y), px(170), px(y + 14)], radius=px(7), fill=WHITE)
    d.rounded_rectangle([px(56), px(172), px(124), px(186)], radius=px(7), fill=WHITE)
    d.polygon([(px(140), px(160)), (px(200), px(185)), (px(140), px(210))], fill=WHITE)


ICONS = {"podcasts": podcasts, "podcast_episodes": podcast_episodes, "youtube_videos": videos,
         "youtube_channels": channels, "youtube_playlists": playlists}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, draw_icon in ICONS.items():
        image = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), CLEAR)
        draw_icon(ImageDraw.Draw(image))
        path = os.path.normpath(os.path.join(OUT, name + ".png"))
        image.resize((SIZE, SIZE), Image.LANCZOS).save(path, optimize=True)
        print(path)
