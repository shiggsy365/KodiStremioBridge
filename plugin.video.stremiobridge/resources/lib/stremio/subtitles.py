"""Subtitles from subtitle addons (and from streams themselves)."""

import os
import re
from urllib.parse import urlsplit

from .aggregate import gather
from .models import Subtitle

MAX_SUBTITLES = 10
_EXTENSIONS = (".srt", ".vtt", ".ass", ".ssa", ".sub")


def fetch_subtitles(client, addons, type_, id_, extra=None):
    def task(addon):
        return lambda: client.get_resource(addon.transport_url, "subtitles", type_, id_, extra)

    results, errors, _ = gather([(a.name, task(a)) for a in addons])
    subtitles = []
    for i, data in results:
        entries = data.get("subtitles") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            sub = Subtitle.from_dict(entry, addons[i].name)
            if sub is not None:
                subtitles.append(sub)
    return subtitles, errors


def select_subtitles(subtitles, languages, per_language=2, limit=MAX_SUBTITLES):
    """Pick up to `per_language` subtitles for each preferred language, in
    preference order. With no preferred languages, take the first `limit`."""
    seen_urls, unique = set(), []
    for sub in subtitles:
        if sub.url not in seen_urls:
            seen_urls.add(sub.url)
            unique.append(sub)
    if not languages:
        return unique[:limit]
    chosen = []
    for lang in languages:
        chosen += [s for s in unique if s.lang.lower() == lang][:per_language]
    return chosen[:limit]


def parse_languages(text):
    return tuple(code.strip().lower() for code in (text or "").split(",") if code.strip())


def _safe(text):
    return re.sub(r"[^a-z0-9-]", "", text.lower()) or "und"


def download_subtitles(client, subtitles, dest_dir):
    """Save subtitles as ``NN.<lang>.<ext>`` so Kodi can tell their language
    from the file name. Returns the paths that downloaded successfully."""
    os.makedirs(dest_dir, exist_ok=True)

    def task(i, sub):
        def run():
            ext = os.path.splitext(urlsplit(sub.url).path)[1].lower()
            path = os.path.join(dest_dir, f"{i:02d}.{_safe(sub.lang)}{ext if ext in _EXTENSIONS else '.srt'}")
            response = client.session.get(sub.url, timeout=client.timeout)
            response.raise_for_status()
            with open(path, "wb") as f:
                f.write(response.content)
            return path
        return run

    results, _, _ = gather([(sub.url, task(i, sub)) for i, sub in enumerate(subtitles)])
    return [path for _, path in results]
