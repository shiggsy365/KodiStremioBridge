"""The stream picker: a full-screen page of stream cards (title line, quality,
audio and source lines, and a strip of badges), in two columns, with filter
chips for addon, resolution and cached results. Skin: stremiobridge-streams.xml."""

import os

import xbmcgui
import xbmcvfs

from stremio.badgestrip import BadgeStrips
from stremio.streaminfo import DEBRID, LABELS, audio_codecs, normalise, stream_info

from .common import ADDON, L, log

XML = "stremiobridge-streams.xml"
CARDS, CHIPS = 50, 60
ACTION_PREVIOUS_MENU, ACTION_NAV_BACK = 10, 92
RESOLUTION_ORDER = ("4k", "1440p", "1080p", "720p", "sd")
DEBRID_CODES = {name: code for code, name in DEBRID.items()}
KEY = "FF8A949E"        # colour of "Quality:" etc.
CACHED, UNCACHED = "FF2ECC71", "FFF5A623"


def badge_strips():
    return BadgeStrips(os.path.join(ADDON.getAddonInfo("path"), "resources", "media", "badges"),
                       os.path.join(xbmcvfs.translatePath("special://temp"), "stremiobridge", "badges"))


def card_lines(stream, info):
    """The card's title and up to three text lines."""
    code = DEBRID_CODES.get(info.debrid, info.debrid[:2].upper()) if info.debrid else ""
    if code:
        tag = f"[COLOR {CACHED if info.cached else UNCACHED}][{code}{'+' if info.cached else ''}][/COLOR] "
    else:
        tag = f"[COLOR {UNCACHED}][P2P][/COLOR] " if info.p2p else ""
    resolution = LABELS.get(info.resolution, "")
    title = f"{tag}[B]{stream.addon}[/B]" + (f"  {resolution}" if resolution else "")

    quality = [LABELS.get(info.release, "")] + [stream.quality.codec]
    quality += [LABELS.get(k, k) for k in info.visual + info.extras]
    audio = audio_codecs("\n".join((normalise(stream.name), normalise(stream.title),
                                    (stream.filename or "").replace(".", " ")))) + [info.channels]
    size = " · ".join(p for p in (info.size, info.bitrate) if p)
    source = " · ".join(p for p in (info.provider, info.group) if p)

    def line(key, values, sep=" | "):
        values = [v for v in values if v]
        return f"[COLOR {KEY}]{key}:[/COLOR] {sep.join(values)}" if values else ""

    lines = [line(L(30371), quality), line(L(30372), list(dict.fromkeys(audio))),
             "   ".join(p for p in (line(L(30373), [size]), line(L(30374), [source]),
                                    line(L(30375), [info.languages.upper()])) if p)]
    lines = [l for l in lines if l]
    if len(lines) < 3:  # little to go on (e.g. YouTube): show the addon's own words
        lines += [l for l in normalise(stream.title).splitlines() if l][:3 - len(lines)]
    return title, lines


def card_item(stream, info, strips):
    title, lines = card_lines(stream, info)
    item = xbmcgui.ListItem(title)
    for index in range(3):
        item.setProperty(f"line{index + 1}", lines[index] if index < len(lines) else "")
    item.setProperty("filename", info.filename or normalise(stream.name).replace("\n", " "))
    strip = strips.strip(info.badges()) if strips else ""
    if strip:
        item.setArt({"badges": strip})
    return item


class StreamWindow(xbmcgui.WindowXMLDialog):
    """Set `streams` (candidates in order), `title`, `subtitle`, `logo` and
    `fanart` before doModal(); afterwards `choice` is the chosen index into
    `streams`, or None."""

    streams = ()
    title = ""
    subtitle = ""
    logo = ""
    fanart = ""
    choice = None

    def onInit(self):
        self.setProperty("title", self.title)
        self.setProperty("logo", self.logo)
        self.setProperty("fanart", self.fanart)
        self.setProperty("empty", L(30368))
        try:
            strips = badge_strips()
        except OSError as exc:
            log(f"Badges unavailable: {exc}")
            strips = None
        infos = [stream_info(s) for s in self.streams]
        self._items = [card_item(s, i, strips) for s, i in zip(self.streams, infos)]
        self.setProperty("subtitle", "  ·  ".join(p for p in (self.subtitle, L(30367, count=len(self.streams))) if p))

        self._filters = filters(self.streams, infos)
        chips = []
        for label, members in self._filters:
            chip = xbmcgui.ListItem(f"{label}  {len(members)}")
            chips.append(chip)
        self.getControl(CHIPS).addItems(chips)
        self._show(0)
        self.setFocusId(CARDS)

    def _show(self, index):
        self._active = index
        self._shown = self._filters[index][1]
        cards = self.getControl(CARDS)
        cards.reset()
        cards.addItems([self._items[i] for i in self._shown])
        chips = self.getControl(CHIPS)
        for position in range(len(self._filters)):
            chips.getListItem(position).setProperty("active", "1" if position == index else "")

    def onClick(self, control_id):
        if control_id == CHIPS:
            self._show(self.getControl(CHIPS).getSelectedPosition())
            if self._shown:
                self.setFocusId(CARDS)
        elif control_id == CARDS:
            position = self.getControl(CARDS).getSelectedPosition()
            if 0 <= position < len(self._shown):
                self.choice = self._shown[position]
                self.close()

    def onAction(self, action):
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()


def filters(streams, infos):
    """``[(chip label, [stream indexes])]``: All, then each addon, each
    resolution and Cached, where that actually narrows things down."""
    every = list(range(len(streams)))
    chips = [(L(30369), every)]

    def add(label, members):
        if 0 < len(members) < len(every):
            chips.append((label, members))

    for addon in dict.fromkeys(s.addon for s in streams):
        add(addon, [i for i in every if streams[i].addon == addon])
    for resolution in RESOLUTION_ORDER:
        add(LABELS[resolution], [i for i in every if infos[i].resolution == resolution])
    add(L(30370), [i for i in every if infos[i].cached])
    return chips


def choose_stream(candidates, meta=None, video_id=None):
    """Show the picker for `candidates`; the chosen Stream or None."""
    window = StreamWindow(XML, ADDON.getAddonInfo("path"), "Default", "1080i")
    window.streams = list(candidates)
    if meta is not None:
        window.title = meta.name
        window.logo = meta.logo or ""
        window.fanart = meta.background or ""
        video = next((v for v in meta.videos if v.id == video_id), None) if video_id else None
        if video is not None and video.season is not None:
            window.subtitle = f"S{video.season}E{video.episode}" + (f" · {video.title}" if video.title else "")
    else:
        window.title = L(30174)
    window.doModal()
    choice = window.choice
    del window
    return candidates[choice] if choice is not None else None
