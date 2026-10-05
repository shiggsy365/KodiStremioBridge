"""gPodder sync clients: subscriptions and episode play positions.

Two flavours of the same idea:

* the gPodder API v2 (gpodder.net, oPodSync and other compatible servers),
  where everything is per user and subscriptions per device;
* the Nextcloud "gPodder Sync" app, which drops the user/device path parts.

Both return a ``timestamp`` to pass back as ``since`` next time, so each sync
only fetches what changed. Every Kodi install uses the same device id, so they
share one subscription list.
"""

import http.cookiejar
import json
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from . import PodcastError, http as _http

GPODDER, NEXTCLOUD = "gpodder", "nextcloud"


def iso(epoch):
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def from_iso(value):
    """Epoch seconds from gPodder's ``2009-12-12T09:00:00`` (UTC); 0 if unreadable."""
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class Client:
    def __init__(self, server, username, password, device="kodi", flavour=GPODDER, timeout=20):
        server = (server or "").strip().rstrip("/")
        if not server or not username:
            raise PodcastError("Sync needs a server address and a username")
        if "://" not in server:
            server = "https://" + server
        self.server, self.username, self.password = server, username, password
        self.device = device or "kodi"
        self.flavour = flavour
        self.timeout = timeout
        # gpodder.net and oPodSync also hand out a session cookie at login.
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._logged_in = False

    # ------------------------------------------------------------ plumbing

    def _url(self, path, **query):
        query = {k: v for k, v in query.items() if v is not None}
        return f"{self.server}{path}" + (f"?{urlencode(query)}" if query else "")

    def _call(self, method, path, body=None, **query):
        if self.flavour == GPODDER and not self._logged_in:
            self.login()
        raw = _http.request(self._url(path, **query), method=method, body=body,
                            headers={"Accept": "application/json"}, auth=(self.username, self.password),
                            timeout=self.timeout, opener=self.opener)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError as exc:
            raise PodcastError("The sync server didn't return JSON. Check the address and server type.") from exc

    @property
    def _user(self):
        return quote(self.username, safe="")

    @property
    def _device(self):
        return quote(self.device, safe="")

    def login(self):
        """Checks the credentials (gPodder API only; Nextcloud checks every request)."""
        if self.flavour == GPODDER:
            _http.request(self._url(f"/api/2/auth/{self._user}/login.json"), method="POST", body={},
                          auth=(self.username, self.password), timeout=self.timeout, opener=self.opener)
            self._logged_in = True
            try:
                self._call("POST", f"/api/2/devices/{self._user}/{self._device}.json",
                           body={"caption": "Kodi", "type": "other"})
            except PodcastError:
                pass  # servers that create devices on first use may not have this
        else:
            self._call("GET", "/index.php/apps/gpoddersync/subscriptions", since=0)

    # ------------------------------------------------------------ subscriptions

    def get_subscriptions(self, since=0):
        """``(added, removed, timestamp)`` since the timestamp from last time."""
        if self.flavour == GPODDER:
            data = self._call("GET", f"/api/2/subscriptions/{self._user}/{self._device}.json", since=int(since))
        else:
            data = self._call("GET", "/index.php/apps/gpoddersync/subscriptions", since=int(since))
        return list(data.get("add") or []), list(data.get("remove") or []), int(data.get("timestamp") or 0)

    def put_subscriptions(self, added, removed):
        """``(timestamp, [(sent url, url the server keeps)])``."""
        body = {"add": list(added), "remove": list(removed)}
        if self.flavour == GPODDER:
            data = self._call("POST", f"/api/2/subscriptions/{self._user}/{self._device}.json", body=body)
        else:
            data = self._call("POST", "/index.php/apps/gpoddersync/subscription_change/create", body=body)
        renames = [tuple(pair) for pair in data.get("update_urls") or [] if len(pair) == 2 and pair[1]]
        return int(data.get("timestamp") or 0), renames

    # ------------------------------------------------------------ episode actions

    def get_actions(self, since=0):
        """``(actions, timestamp)``; each action a dict as the server sent it."""
        if self.flavour == GPODDER:
            data = self._call("GET", f"/api/2/episodes/{self._user}.json", since=int(since), aggregated="true")
        else:
            data = self._call("GET", "/index.php/apps/gpoddersync/episode_action", since=int(since))
        return list(data.get("actions") or []), int(data.get("timestamp") or 0)

    def put_actions(self, actions):
        """`actions`: dicts with podcast, episode, guid, position, total, timestamp (epoch)."""
        payload = []
        for action in actions:
            item = {
                "podcast": action["podcast"], "episode": action["episode"],
                "action": "play" if self.flavour == GPODDER else "PLAY",
                "timestamp": iso(action["timestamp"]),
                "started": 0, "position": int(action["position"]), "total": int(action["total"]),
            }
            if action.get("guid"):
                item["guid"] = action["guid"]
            if self.flavour == GPODDER:
                item["device"] = self.device
            payload.append(item)
        if not payload:
            return 0
        if self.flavour == GPODDER:
            data = self._call("POST", f"/api/2/episodes/{self._user}.json", body=payload)
        else:
            data = self._call("POST", "/index.php/apps/gpoddersync/episode_action/create", body=payload)
        return int(data.get("timestamp") or 0)
