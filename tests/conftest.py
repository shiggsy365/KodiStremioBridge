import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

LIB = os.path.join(os.path.dirname(__file__), "..", "plugin.video.stremiobridge", "resources", "lib")
sys.path.insert(0, os.path.abspath(LIB))

CINEMETA_LIKE = {
    "id": "com.example.meta",
    "version": "1.2.0",
    "name": "Example Meta",
    "description": "Test catalog addon",
    "resources": ["catalog", "meta"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt"],
    "catalogs": [
        {"type": "movie", "id": "top", "name": "Popular",
         "extra": [{"name": "genre", "options": ["Action", "Sci-Fi"]}, {"name": "skip"}]},
        {"type": "series", "id": "top", "name": "Popular"},
        {"type": "movie", "id": "search", "name": "Search",
         "extra": [{"name": "search", "isRequired": True}]},
    ],
}

STREAM_ADDON = {
    "id": "com.example.streams",
    "version": "0.3.1",
    "name": "Example Streams",
    "resources": [{"name": "stream", "types": ["movie", "series"], "idPrefixes": ["tt", "kitsu:"]}],
    "types": ["movie", "series", "anime"],
    "catalogs": [],
    "behaviorHints": {"p2p": True, "configurable": True},
}


class FakeAddonServer:
    """Serves canned JSON by path; unknown paths return 404."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                body = owner.routes.get(self.path)
                if callable(body):
                    body = body()
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                if isinstance(body, tuple):  # (status, headers, raw bytes)
                    status, headers, raw = body
                else:
                    status, headers = 200, {"Content-Type": "application/json"}
                    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = FakeAddonServer()
    yield srv
    srv.close()
