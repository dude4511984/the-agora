"""Contract tests for the public ring-0 door (public_door.py).

Runs the real proxy in-process against a real fake upstream node and
fake upstream map on real sockets — no mocks of the door itself.
Asserts:
- GET / and GET /3d proxy to map upstream (/3d?public=1)
- GET /facts and GET /view proxy to node upstream (signed by door key)
- Polled JSON (/commons-recent, /kin-intent, /proxy) and assets (/models/*, /avatar, /shape3d) proxy to map
- Non-allowlisted paths and non-GET methods return 404 without touching upstream
- Inbound headers are stripped; map upstream receives X-Agora-Door: 1; node upstream receives door signature
- Rate limit trips
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.exceptions import InvalidSignature

import sys
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
if str(Path.home() / "kin_diary") not in sys.path:
    sys.path.append(str(Path.home() / "kin_diary"))

from kin_diary.keys import generate_keypair, load_public  # noqa: E402
from kin_diary.agora.canonical import request_canonical  # noqa: E402

import public_door  # noqa: E402

FACTS = {"node": "Fake", "speaker": "Somebody", "residents": ["A", "B"],
         "paused": False, "pause_reason": None}
VIEW = {"places": [{"id": "concourse", "kind": "commons"}]}
COURTYARD_HTML = "<!doctype html><html><head><title>Courtyard</title></head><body>Courtyard</body></html>"


class _FakeNodeUpstream(BaseHTTPRequestHandler):
    """Stands in for Frosty's node (port 8770). Records exactly what it was sent."""
    seen = []          # list of (path, headers-as-dict)
    server_version = "fake-node/0"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        type(self).seen.append((self.path, dict(self.headers)))
        obj = FACTS if self.path == "/" else VIEW if self.path == "/view" \
            else {"error": "no such path"}
        code = 200 if self.path in ("/", "/view") else 404
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FakeMapUpstream(BaseHTTPRequestHandler):
    """Stands in for Frosty's agora_map (port 8791). Records exactly what it was sent."""
    seen = []          # list of (path, headers-as-dict)
    server_version = "fake-map/0"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        type(self).seen.append((self.path, dict(self.headers)))
        if self.path == "/3d?public=1":
            body = COURTYARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path in ("/commons-recent", "/kin-intent"):
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/models/") or self.path.startswith("/static/agora/"):
            body = b"asset-bytes"
            self.send_response(200)
            self.send_header("Content-Type", "model/gltf+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/avatar") or self.path.startswith("/shape3d") or self.path.startswith("/proxy"):
            body = b'{"data": "present"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = b'{"error": "not found"}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _DoorCase(unittest.TestCase):
    max_reads = 30

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.door_key = generate_keypair(
            "door-everysynthetic", keys_root=Path(cls._tmp.name))
        cls.node_upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeNodeUpstream)
        cls.map_upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMapUpstream)
        cls.door = public_door.make_server(
            "127.0.0.1", 0, cls.door_key, node_name="Fake",
            upstream_host="127.0.0.1",
            upstream_port=cls.node_upstream.server_address[1],
            map_host="127.0.0.1",
            map_port=cls.map_upstream.server_address[1],
            max_reads=cls.max_reads)
        cls._threads = [
            threading.Thread(target=s.serve_forever, daemon=True)
            for s in (cls.node_upstream, cls.map_upstream, cls.door)]
        for t in cls._threads:
            t.start()
        cls.port = cls.door.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.door.shutdown()
        cls.map_upstream.shutdown()
        cls.node_upstream.shutdown()
        cls._tmp.cleanup()

    def setUp(self):
        _FakeNodeUpstream.seen.clear()
        _FakeMapUpstream.seen.clear()

    def _get(self, path, headers=None, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    # ── the courtyard and map proxying ─────────────────────────────────────

    def test_root_and_3d_proxy_to_public_courtyard(self):
        for path in ("/", "/3d"):
            status, hdrs, body = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(body.decode("utf-8"), COURTYARD_HTML, path)
            self.assertIn("text/html", hdrs["Content-Type"])

        # Upstream saw /3d?public=1 with X-Agora-Door: 1
        self.assertEqual(len(_FakeMapUpstream.seen), 2)
        for req_path, req_hdrs in _FakeMapUpstream.seen:
            self.assertEqual(req_path, "/3d?public=1")
            self.assertEqual(req_hdrs.get("X-Agora-Door"), "1")
            self.assertEqual(req_hdrs.get("User-Agent"), "agora-door/1")

    def test_map_polled_and_asset_endpoints_are_proxied(self):
        paths = [
            "/commons-recent",
            "/kin-intent",
            "/models/lantern.gltf",
            "/avatar?kin=Bong",
            "/shape3d?kin=Bong",
            "/proxy?what=root&node=http%3A%2F%2F192.168.1.119%3A8770",
        ]
        for path in paths:
            status, _, _ = self._get(path)
            self.assertEqual(status, 200, path)

        self.assertEqual(len(_FakeMapUpstream.seen), len(paths))
        for req_path, req_hdrs in _FakeMapUpstream.seen:
            self.assertEqual(req_hdrs.get("X-Agora-Door"), "1")

    # ── node ring-0 paths ──────────────────────────────────────────────────

    def test_ring0_paths_are_served_from_node(self):
        # /facts proxies to node /
        status, _, body = self._get("/facts", headers={"Accept": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), FACTS)

        # /view proxies to node /view
        status, _, body = self._get("/view")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), VIEW)

        self.assertEqual(len(_FakeNodeUpstream.seen), 2)
        self.assertEqual(_FakeNodeUpstream.seen[0][0], "/")
        self.assertEqual(_FakeNodeUpstream.seen[1][0], "/view")

    def test_everything_else_is_404_and_never_touches_upstream(self):
        for path in ("/notices", "/peers", "/board/commons",
                     "/artifact/" + "ab" * 32, "/voice_chat",
                     "/models/../secret", "/view/"):
            status, _, body = self._get(path)
            self.assertEqual(status, 404, path)
            self.assertEqual(json.loads(body),
                             {"error": "no such path"}, path)
        self.assertEqual(_FakeNodeUpstream.seen, [])
        self.assertEqual(_FakeMapUpstream.seen, [])

    # ── GET-only ───────────────────────────────────────────────────────────

    def test_non_get_methods_are_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            status, _, _ = self._get("/", method=method, body=b"{}")
            self.assertEqual(status, 404, method)
            status, _, _ = self._get("/voice_chat", method=method, body=b"{}")
            self.assertEqual(status, 404, method)
        self.assertEqual(_FakeNodeUpstream.seen, [])
        self.assertEqual(_FakeMapUpstream.seen, [])

    # ── the signature is the door's own, and it really verifies ────────────

    def test_node_upstream_receives_a_valid_door_signature(self):
        self._get("/view")
        self.assertEqual(len(_FakeNodeUpstream.seen), 1)
        path, hdrs = _FakeNodeUpstream.seen[0]
        self.assertEqual(path, "/view")
        self.assertEqual(hdrs.get("X-Agora-Key"), self.door_key.key_id)
        ts = int(hdrs["X-Agora-Time"])
        self.assertLess(abs(int(time.time() * 1000) - ts), 60_000)
        sig = bytes.fromhex(hdrs["X-Agora-Signature"])
        canon = request_canonical(self.door_key.key_id, "Fake", "/view",
                                   ts, "")
        try:
            load_public(self.door_key.key_id).verify(sig, canon)
        except InvalidSignature:
            self.fail("door signature did not verify against its own key")

    def test_visitor_auth_headers_are_never_forwarded(self):
        forged = {
            "X-Agora-Key": "deadbeef" * 8,
            "X-Agora-Time": "1",
            "X-Agora-Signature": "00" * 64,
            "X-Agora-Door": "forged",
            "Authorization": "Bearer hunter2",
            "Cookie": "session=please",
        }
        # Test node path
        status, _, _ = self._get("/facts", headers=forged)
        self.assertEqual(status, 200)
        _, hdrs = _FakeNodeUpstream.seen[0]
        self.assertEqual(hdrs.get("X-Agora-Key"), self.door_key.key_id)
        self.assertNotIn("Authorization", hdrs)
        self.assertNotIn("Cookie", hdrs)
        self.assertNotEqual(hdrs.get("X-Agora-Signature"), "00" * 64)

        # Test map path
        status, _, _ = self._get("/", headers=forged)
        self.assertEqual(status, 200)
        _, map_hdrs = _FakeMapUpstream.seen[0]
        self.assertEqual(map_hdrs.get("X-Agora-Door"), "1")
        self.assertNotIn("Authorization", map_hdrs)
        self.assertNotIn("Cookie", map_hdrs)

    # ── rate limit ─────────────────────────────────────────────────────────

    def test_rate_limit_trips(self):
        limited = public_door.make_server(
            "127.0.0.1", 0, self.door_key, node_name="Fake",
            upstream_host="127.0.0.1",
            upstream_port=self.node_upstream.server_address[1],
            map_host="127.0.0.1",
            map_port=self.map_upstream.server_address[1],
            max_reads=3)
        t = threading.Thread(target=limited.serve_forever, daemon=True)
        t.start()
        try:
            port = limited.server_address[1]
            codes = []
            for _ in range(5):
                conn = http.client.HTTPConnection("127.0.0.1", port,
                                                  timeout=5)
                conn.request("GET", "/")
                codes.append(conn.getresponse().status)
                conn.close()
            self.assertEqual(codes, [200, 200, 200, 429, 429])
        finally:
            limited.shutdown()

    # ── the human rendering ────────────────────────────────────────────────

    def test_browser_accept_gets_html_with_same_facts_on_facts(self):
        status, hdrs, body = self._get(
            "/facts", headers={"Accept": "text/html,application/xhtml+xml"})
        self.assertEqual(status, 200)
        self.assertIn("text/html", hdrs["Content-Type"])
        text = body.decode()
        self.assertIn("Fake", text)            # node name rendered
        self.assertIn("Somebody", text)        # speaker rendered
        self.assertIn("/view", text)           # teaser linked
        self.assertNotIn("<script", text)      # no active content

    def test_json_accept_gets_raw_json_on_facts(self):
        status, hdrs, body = self._get(
            "/facts", headers={"Accept": "application/json"})
        self.assertEqual(status, 200)
        self.assertIn("application/json", hdrs["Content-Type"])
        self.assertEqual(json.loads(body), FACTS)

    def test_render_facts_html_shows_decides_by_unanimity_when_unanimous(self):
        facts = {
            "node": "Frosty",
            "speaker": None,
            "residents": ["Eli", "Crungus", "Bong", "Marvin"],
            "paused": False,
            "pause_reason": None,
            "governance": "unanimous",
        }
        text = public_door.render_facts_html(facts, "/view").decode("utf-8")
        self.assertIn("Decides by unanimity", text)
        self.assertNotIn("Paused", text)


if __name__ == "__main__":
    unittest.main()
