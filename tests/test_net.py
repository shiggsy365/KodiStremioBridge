"""stremio.net: the standard-library stand-in for requests."""

import gzip
import json
import sys
import time

import pytest

from stremio import net


def test_get_json_and_headers(server):
    server.routes["/a.json"] = {"x": 1}
    session = net.Session()
    session.headers["User-Agent"] = "Test/1"
    response = session.get(server.url + "/a.json", timeout=5)
    assert response.status_code == 200 and response.ok
    assert response.json() == {"x": 1}
    assert response.headers.get("content-type") == "application/json"  # case-insensitive


def test_gzip_bodies_are_decoded(server):
    body = json.dumps({"metas": [{"id": f"tt{i}"} for i in range(50)]}).encode()
    server.routes["/z.json"] = (200, {"Content-Type": "application/json", "Content-Encoding": "gzip"},
                                gzip.compress(body))
    assert net.Session().get(server.url + "/z.json", timeout=5).json()["metas"][49] == {"id": "tt49"}


def test_errors_come_back_as_responses(server):
    response = net.Session().get(server.url + "/missing", timeout=5)
    assert response.status_code == 404 and not response.ok
    with pytest.raises(net.HTTPError):
        response.raise_for_status()
    with pytest.raises(net.RequestException):  # HTTPError is one, as in requests
        response.raise_for_status()


def test_params_are_added_to_the_query(server):
    server.routes["/q?k=1&apikey=KEY&s=a+b"] = {"ok": True}
    session = net.Session()
    assert session.get(server.url + "/q?k=1", params={"apikey": "KEY", "s": "a b", "none": None},
                       timeout=5).json() == {"ok": True}


def test_post_json(server):
    seen = {}

    def record():
        seen["n"] = len(server.requests)
        return {"done": True}

    server.routes["/sync"] = record
    # FakeAddonServer only answers GET, so the POST lands as a 501 response, not an exception
    response = net.Session().request("POST", server.url + "/sync", json={"a": 1}, timeout=5)
    assert response.status_code == 501  # sent, and the error answer came back as a response


def test_unreachable_raises_connection_error():
    with pytest.raises(net.ConnectionError):
        net.Session().get("http://127.0.0.1:1/x", timeout=2)


def test_timeout_raises_timeout(server):
    def slow():
        time.sleep(1.5)
        return {"late": True}

    server.routes["/slow"] = slow
    with pytest.raises(net.Timeout):
        net.Session().get(server.url + "/slow", timeout=0.3)


def test_stream_reads_no_body(server):
    server.routes["/v.mkv"] = (206, {"Content-Type": "video/x-matroska", "Content-Range": "bytes 0-1/99"}, b"xx")
    response = net.Session().get(server.url + "/v.mkv", headers={"Range": "bytes=0-1"}, stream=True, timeout=5)
    assert response.status_code == 206 and response.headers.get("Content-Range") == "bytes 0-1/99"
    assert response._content is None  # not downloaded
    response.close()


def test_client_does_not_import_requests(server):
    """The point of stremio.net: a catalog fetch never loads requests."""
    from conftest import CINEMETA_LIKE
    from stremio.client import StremioClient

    sys.modules.pop("requests", None)
    server.routes["/manifest.json"] = CINEMETA_LIKE
    StremioClient(timeout=5).fetch_manifest(server.url)
    assert "requests" not in sys.modules


def test_certifi_only_without_a_certificate_store(monkeypatch):
    import ssl
    from types import SimpleNamespace

    made = []
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: made.append(cafile) or object())
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: SimpleNamespace(cafile=__file__, capath=None))
    sys.modules.pop("certifi", None)
    net._ssl_context()
    assert made == [None] and "certifi" not in sys.modules       # Kodi's / the system's store: no certifi

    monkeypatch.setattr(ssl, "get_default_verify_paths",
                        lambda: SimpleNamespace(cafile="/nonexistent/cert.pem", capath=None))
    made.clear()
    net._ssl_context()
    import certifi
    assert made == [certifi.where()]
