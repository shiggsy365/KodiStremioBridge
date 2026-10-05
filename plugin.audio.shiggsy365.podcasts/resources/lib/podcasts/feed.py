"""Podcast RSS (and Atom) feeds: the podcast's details and its playable episodes."""

import email.utils
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import PodcastError, http
from .models import Episode, Podcast

ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"


def fetch(feed_url, timeout=20):
    """``(Podcast, [Episode])`` for a feed, newest episode first."""
    raw = http.request(feed_url, headers={"Accept": ACCEPT}, timeout=timeout)
    return parse(feed_url, raw)


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _children(element, name):
    return [child for child in element if _local(child.tag) == name]


def _text(element, *names):
    """Text of the first child with one of `names` that has any."""
    for name in names:
        for child in _children(element, name):
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return ""


def _image(element):
    for child in element:
        name = _local(child.tag)
        if name == "image":
            href = child.get("href") or _text(child, "url")
            if href:
                return href.strip()
        elif name == "thumbnail" and child.get("url"):
            return child.get("url").strip()
    return ""


def parse(feed_url, raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise PodcastError(f"The podcast feed isn't valid RSS: {exc}") from exc
    channel = root if _local(root.tag) == "feed" else next(iter(_children(root, "channel")), None)
    if channel is None:
        raise PodcastError("The podcast feed has no channel")
    podcast = Podcast(
        title=clean(_text(channel, "title")) or feed_url,
        feed_url=feed_url,
        author=clean(_text(channel, "author", "owner")),
        image=_image(channel),
        description=clean(_text(channel, "summary", "description", "subtitle")),
    )
    episodes = []
    for item in channel:
        if _local(item.tag) not in ("item", "entry"):
            continue
        url, mime = _audio(item)
        if not url:
            continue
        guid = _text(item, "guid", "id")
        episodes.append(Episode(
            feed_url=feed_url,
            guid=guid,
            title=clean(_text(item, "title")) or "Untitled episode",
            url=url,
            description=clean(_text(item, "encoded", "description", "summary", "subtitle")),
            image=_image(item) or podcast.image,
            published=parse_date(_text(item, "pubdate", "published", "updated", "date")),
            duration=parse_duration(_text(item, "duration")),
            podcast_title=podcast.title,
            mime=mime,
        ))
    episodes.sort(key=lambda e: e.published, reverse=True)
    return podcast, episodes


def _audio(item):
    """``(url, mime type)`` of the episode's media; ("", "") when there's none."""
    for child in item:
        name = _local(child.tag)
        if name == "enclosure" and child.get("url"):
            return child.get("url").strip(), (child.get("type") or "").strip()
        if name == "link" and (child.get("rel") == "enclosure" or (child.get("type") or "").startswith("audio/")) \
                and child.get("href"):
            return child.get("href").strip(), (child.get("type") or "").strip()
    for child in item:  # Media RSS only when there's no enclosure
        if _local(child.tag) == "content" and child.get("url") and \
                (child.get("type") or "audio/").startswith(("audio/", "video/")):
            return child.get("url").strip(), (child.get("type") or "").strip()
    return "", ""


def parse_duration(value):
    """Seconds from "3600", "59:59" or "1:02:03"; 0 when unreadable."""
    value = (value or "").strip()
    try:
        parts = [float(p) for p in value.split(":")]
    except ValueError:
        return 0
    if not 1 <= len(parts) <= 3:
        return 0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return int(seconds)


def parse_date(value):
    """Epoch seconds from an RFC 822 or ISO 8601 date; 0 when unreadable."""
    value = (value or "").strip()
    if not value:
        return 0
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


_TAGS = re.compile(r"<[^>]+>")
_BREAKS = re.compile(r"<\s*(br|/p|/li|/div)\s*/?\s*>", re.IGNORECASE)


def clean(value):
    """Plain text from feed HTML, keeping paragraph breaks."""
    text = _BREAKS.sub("\n", value or "")
    text = html.unescape(_TAGS.sub("", text)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
