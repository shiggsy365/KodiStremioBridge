"""One-time copy of your Nuvio (Nuvio Sync) watched history to MDBList.

    .venv/bin/python tools/nuvio_to_mdblist.py            # preview: counts only, uploads nothing
    .venv/bin/python tools/nuvio_to_mdblist.py --apply    # upload to MDBList

Signs in to your Nuvio account (email + password, asked for here and never
stored), reads every watched movie and episode of one Nuvio profile, and adds
them to your MDBList watched history with their original watch dates, using
the same mapping Stremio Bridge uses. Nothing is removed from either side, and
items MDBList already has are simply kept, so running it twice is harmless.

Server details come from NuvioTV's local.properties (NUVIO_SUPABASE_URL and
NUVIO_SUPABASE_ANON_KEY); the MDBList key from Stremio Bridge's Kodi settings,
or MDBLIST_API_KEY. Watch history kept in Trakt or Simkl instead of Nuvio Sync
isn't read: MDBList can import that itself (mdblist.com > Preferences).
"""

import argparse
import getpass
import os
import re
import sys
from collections import Counter
from types import SimpleNamespace

import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.stremiobridge", "resources", "lib"))

from mdblist import BATCH_SIZE, MDBListClient, MDBListError, supported_ids, watched_payload  # noqa: E402
from stremio.models import external_ids  # noqa: E402

NUVIO_PROPERTIES = os.path.join(ROOT, "..", "NuvioTV", "local.properties")
KODI_SETTINGS = os.path.expanduser("~/.kodi/userdata/addon_data/plugin.video.stremiobridge/settings.xml")
PAGE_SIZE = 900  # as the Nuvio app pulls them


def read_properties(path):
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def mdblist_key():
    if os.environ.get("MDBLIST_API_KEY"):
        return os.environ["MDBLIST_API_KEY"]
    try:
        with open(KODI_SETTINGS, encoding="utf-8") as f:
            match = re.search(r'<setting id="mdblist_api_key"[^>]*>([^<]+)<', f.read())
    except OSError:
        match = None
    return match.group(1).strip() if match else ""


class Nuvio:
    def __init__(self, url, anon_key):
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.token = None

    def _headers(self):
        headers = {"apikey": self.anon_key, "Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def sign_in(self, email, password):
        response = requests.post(f"{self.url}/auth/v1/token?grant_type=password", headers=self._headers(),
                                 json={"email": email, "password": password}, timeout=30)
        if response.status_code >= 400:
            raise SystemExit(f"Nuvio sign-in failed (HTTP {response.status_code}). Check your email and password.")
        self.token = response.json()["access_token"]

    def watched_items(self, profile_id):
        items, page = [], 1
        while True:
            response = requests.post(f"{self.url}/rest/v1/rpc/sync_pull_watched_items", headers=self._headers(),
                                     json={"p_profile_id": profile_id, "p_page": page, "p_page_size": PAGE_SIZE},
                                     timeout=60)
            if response.status_code >= 400:
                raise SystemExit(f"Reading Nuvio watched items failed (HTTP {response.status_code}).")
            batch = response.json()
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                return items
            page += 1


def to_entries(items):
    """Nuvio watched items -> entries for mdblist.watched_payload, plus counts
    of what was skipped."""
    entries, skipped = [], Counter()
    for item in items:
        content_id = str(item.get("content_id") or "")
        season, episode = item.get("season"), item.get("episode")
        parts = content_id.split(":")
        if season is None and len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
            season, episode = int(parts[-2]), int(parts[-1])  # "tt…:S:E" style ids
        stamp = item.get("watched_at") or 0
        stamp = stamp / 1000 if stamp > 1e11 else stamp     # Nuvio stores milliseconds
        stamp = stamp if stamp > 1e9 else 0                 # no real date: MDBList uses today
        is_episode = season is not None and episode is not None
        ids = supported_ids(external_ids(content_id), "show" if is_episode else "movie")
        if not ids:
            skipped["no id MDBList accepts (e.g. Kitsu-only anime episodes)"] += 1
        elif not is_episode and item.get("content_type") not in ("movie",):
            skipped["whole-show marks (episodes are copied instead)"] += 1
        else:
            entries.append(SimpleNamespace(is_episode=is_episode, ids=ids, season=season, episode=episode,
                                           watched_at=stamp, content_id=content_id))
    return entries, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="upload to MDBList (default: preview only)")
    parser.add_argument("--profile", type=int, default=1, help="Nuvio profile number (default 1)")
    parser.add_argument("--email", default=os.environ.get("NUVIO_EMAIL", ""))
    parser.add_argument("--properties", default=NUVIO_PROPERTIES, help="NuvioTV local.properties")
    args = parser.parse_args()

    props = read_properties(args.properties)
    url, anon_key = props.get("NUVIO_SUPABASE_URL"), props.get("NUVIO_SUPABASE_ANON_KEY")
    if not url or not anon_key:
        raise SystemExit(f"NUVIO_SUPABASE_URL / NUVIO_SUPABASE_ANON_KEY missing from {args.properties}")
    api_key = mdblist_key()
    if not api_key:
        raise SystemExit("No MDBList API key: set it in Stremio Bridge's settings or MDBLIST_API_KEY.")

    nuvio = Nuvio(url, anon_key)
    email = args.email or input("Nuvio email: ").strip()
    nuvio.sign_in(email, getpass.getpass("Nuvio password: "))
    items = nuvio.watched_items(args.profile)
    entries, skipped = to_entries(items)
    movies = [e for e in entries if not e.is_episode]
    episodes = [e for e in entries if e.is_episode]
    shows = {tuple(sorted(e.ids.items())) for e in episodes}
    print(f"Nuvio profile {args.profile}: {len(items)} watched items")
    print(f"  to copy: {len(movies)} movies, {len(episodes)} episodes of {len(shows)} shows")
    for reason, count in skipped.items():
        print(f"  skipped: {count} ({reason})")
    if not args.apply:
        print("Preview only. Run again with --apply to upload to MDBList.")
        return

    client = MDBListClient(api_key)
    totals = Counter()
    try:
        for i in range(0, len(entries), BATCH_SIZE):
            body = watched_payload(entries[i:i + BATCH_SIZE])
            if not body:
                continue
            result = client._request("POST", "/sync/watched", body=body) or {}
            for outcome in ("added", "existing", "not_found"):
                for kind, count in (result.get(outcome) or {}).items():
                    if isinstance(count, int):
                        totals[f"{outcome} {kind}"] += count
                    elif isinstance(count, list):
                        totals[f"{outcome} {kind}"] += len(count)
            print(f"  uploaded {min(i + BATCH_SIZE, len(entries))} of {len(entries)}")
    except MDBListError as exc:
        raise SystemExit(f"MDBList upload stopped: {exc}")
    print("Done." + ("" if not totals else " MDBList reports: " + ", ".join(f"{k} {v}" for k, v in sorted(totals.items()))))


if __name__ == "__main__":
    main()
