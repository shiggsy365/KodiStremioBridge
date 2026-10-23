"""BingeTV: the service's little web server for trailers' MPEG-DASH manifests (youtube.py).

InputStream Adaptive fetches a manifest only over HTTP, so a trailer's is written to a file
(by the service, or the plugin for the info page's Trailer button) and served from here, on
127.0.0.1 at a port of the system's choosing (Window(Home).Property(stremiobridge.trailer_port)).
It answers at once, always: Kodi looks a video's address up again when it stops, and waited on
YouTube's add-on's sleeping server, frozen, 20 s at a time. Its paths end in .mpd, too.
"""

import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import xbmcgui
import xbmcvfs

from .common import log

PORT = "stremiobridge.trailer_port"
FOLDER = "special://temp/stremiobridge/trailers"
KEEP = 6  # manifests kept (the newest)
NAME = re.compile(r"^/trailer/([\w-]{1,40})\.mpd$")


def folder():
    path = xbmcvfs.translatePath(FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def publish(name, text):
    """Write a trailer's manifest; its URL, or "" when the service's server isn't running."""
    port = xbmcgui.Window(10000).getProperty(PORT)
    if not port:
        return ""
    base = folder()
    with open(os.path.join(base, f"{name}.mpd"), "w", encoding="utf-8") as f:
        f.write(text)
    kept = sorted((os.path.join(base, n) for n in os.listdir(base) if n.endswith(".mpd")),
                  key=os.path.getmtime, reverse=True)
    for old in kept[KEEP:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return f"http://127.0.0.1:{port}/trailer/{name}.mpd"


class Handler(BaseHTTPRequestHandler):
    base = ""

    def _body(self):
        match = NAME.match(self.path.split("?", 1)[0])
        if not match:
            return None
        try:
            with open(os.path.join(self.base, match.group(1) + ".mpd"), "rb") as f:
                return f.read()
        except OSError:
            return None

    def _answer(self, send_body):
        body = self._body()
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/dash+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802 - http.server's names
        self._answer(False)

    def do_GET(self):  # noqa: N802
        self._answer(True)

    def log_message(self, *args):  # not Kodi's log: every request
        pass


class TrailerServer:
    def __init__(self):
        self.server = None

    def start(self):
        try:
            Handler.base = folder()
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except OSError as exc:
            log(f"Trailer server: {exc}")
            return
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.5},
                         name="stremiobridge-trailers", daemon=True).start()
        xbmcgui.Window(10000).setProperty(PORT, str(self.server.server_address[1]))

    def stop(self):
        if self.server:
            xbmcgui.Window(10000).clearProperty(PORT)
            self.server.shutdown()
            self.server.server_close()  # (the listening socket too)
            self.server = None
