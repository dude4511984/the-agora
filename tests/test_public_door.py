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

import hashlib
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
from kin_diary.agora.wire import sign_request  # noqa: E402

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

        if self.path in ("/market", "/public-commons-ads"):
            body = b"<!doctype html>market" if self.path == "/market" else b'{"posts": []}'
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if self.path == "/market" else "application/json")
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


class _FakeCommonsUpstream(BaseHTTPRequestHandler):
    """Stands in for Commons (co-located, its own port). Records exactly
    what it was sent, including headers — the whole point of the
    Commons routes is checking what does and doesn't cross the door.
    """
    seen = []          # list of (method, path, headers-as-dict, body)
    server_version = "fake-commons/0"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        type(self).seen.append(("GET", self.path, dict(self.headers), b""))
        body = json.dumps({"label": "UNSAFE", "posts": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        type(self).seen.append(("POST", self.path, dict(self.headers), raw))
        body = json.dumps({"id": "fake-post-id"}).encode()
        self.send_response(201)
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
        cls.commons_upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCommonsUpstream)
        cls.door = public_door.make_server(
            "127.0.0.1", 0, cls.door_key, node_name="Fake",
            upstream_host="127.0.0.1",
            upstream_port=cls.node_upstream.server_address[1],
            map_host="127.0.0.1",
            map_port=cls.map_upstream.server_address[1],
            commons_host="127.0.0.1",
            commons_port=cls.commons_upstream.server_address[1],
            max_reads=cls.max_reads)
        cls._threads = [
            threading.Thread(target=s.serve_forever, daemon=True)
            for s in (cls.node_upstream, cls.map_upstream,
                      cls.commons_upstream, cls.door)]
        for t in cls._threads:
            t.start()
        cls.port = cls.door.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.door.shutdown()
        cls.map_upstream.shutdown()
        cls.node_upstream.shutdown()
        cls.commons_upstream.shutdown()
        cls._tmp.cleanup()

    def setUp(self):
        _FakeNodeUpstream.seen.clear()
        _FakeMapUpstream.seen.clear()
        _FakeCommonsUpstream.seen.clear()

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

    def test_the_market_and_its_ads_are_open(self):
        """Don, 2026-09-24: "Let strangers walk the commons. Open the doors."
        The market (/market) and the feed its stalls and the plaza read
        (/public-commons-ads: the Commons' own public posts, already served
        here as /commons/posts) go through the door, read-only, like /3d."""
        for path in ("/market", "/public-commons-ads"):
            status, _, _ = self._get(path)
            self.assertEqual(status, 200, path)
        self.assertEqual([p for p, _ in _FakeMapUpstream.seen], ["/market", "/public-commons-ads"])
        for _, req_hdrs in _FakeMapUpstream.seen:
            self.assertEqual(req_hdrs.get("X-Agora-Door"), "1")

    def test_leaving_the_market_lands_back_on_the_path(self):
        """/3d?from=market reaches the map as /3d?public=1&from=market; any
        other query is still dropped, and public=1 is never lost."""
        self._get("/3d?from=market")
        self._get("/3d?from=market&public=0&cross=Home")
        self._get("/3d?cross=Home")
        self.assertEqual([p for p, _ in _FakeMapUpstream.seen],
                         ["/3d?public=1&from=market", "/3d?public=1&from=market", "/3d?public=1"])

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
        self.assertEqual(_FakeCommonsUpstream.seen, [])

    # ── GET-only ───────────────────────────────────────────────────────────

    def test_non_get_methods_are_refused(self):
        """POST is no longer refused unconditionally — but only the
        three /commons/* write paths are listed, so every other path and
        every other method still gets the same 404 as before."""
        for method in ("PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            status, _, _ = self._get("/", method=method, body=b"{}")
            self.assertEqual(status, 404, method)
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            status, _, _ = self._get("/voice_chat", method=method, body=b"{}")
            self.assertEqual(status, 404, method)
        status, _, _ = self._get("/", method="POST", body=b"{}")
        self.assertEqual(status, 404)
        self.assertEqual(_FakeNodeUpstream.seen, [])
        self.assertEqual(_FakeMapUpstream.seen, [])
        self.assertEqual(_FakeCommonsUpstream.seen, [])

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

    # ── Commons: read is plain, post forwards the caller's own headers ─────

    def test_commons_posts_is_a_plain_proxy(self):
        status, hdrs, body = self._get("/commons/posts")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"label": "UNSAFE", "posts": []})
        self.assertEqual(len(_FakeCommonsUpstream.seen), 1)
        method, path, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertEqual((method, path), ("GET", "/commons/posts"))
        # Unlike the map path, Commons reads need no X-Agora-Door marker —
        # there is nothing to distinguish here, reading is public either way.
        self.assertNotIn("X-Agora-Door", req_hdrs)

    def test_commons_post_forwards_the_callers_own_signature_unmodified(self):
        """The one deliberate exception. The caller's real X-Agora-* headers
        must arrive at Commons exactly as sent -- not the door's own key,
        not stripped, not re-signed."""
        poster = generate_keypair("Commons-poster", keys_root=Path(self._tmp.name))
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        ts = str(int(time.time() * 1000))
        digest = hashlib.sha256(body).hexdigest()
        canon = request_canonical(poster.key_id, "Commons", "/commons/post",
                                  int(ts), digest)
        sig = poster.sign(canon)
        headers = {
            "X-Agora-Key": poster.key_id,
            "X-Agora-Time": ts,
            "X-Agora-Signature": sig,
            "Content-Type": "application/json",
        }
        status, _, resp_body = self._get(
            "/commons/post", headers=headers, method="POST", body=body)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(resp_body), {"id": "fake-post-id"})

        self.assertEqual(len(_FakeCommonsUpstream.seen), 1)
        method, path, req_hdrs, req_body = _FakeCommonsUpstream.seen[0]
        self.assertEqual((method, path), ("POST", "/commons/post"))
        self.assertEqual(req_hdrs.get("X-Agora-Key"), poster.key_id)
        self.assertEqual(req_hdrs.get("X-Agora-Time"), ts)
        self.assertEqual(req_hdrs.get("X-Agora-Signature"), sig)
        self.assertEqual(req_body, body)

    def test_commons_post_never_carries_the_doors_own_key(self):
        """Every other write in this file is the door signing as itself.
        This route must never do that -- it is the caller's identity or
        no identity at all."""
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        self._get("/commons/post", method="POST", body=body)  # unsigned
        _, _, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertNotEqual(req_hdrs.get("X-Agora-Key"), self.door_key.key_id)
        self.assertNotIn("X-Agora-Key", req_hdrs)

    def test_commons_post_strips_cookies_and_auth_but_keeps_the_signature(self):
        poster = generate_keypair("Commons-poster-2", keys_root=Path(self._tmp.name))
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(poster, "Commons", "/commons/post", body=body)
        h["Cookie"] = "session=please"
        h["Authorization"] = "Bearer hunter2"
        status, _, _ = self._get(
            "/commons/post", headers=h, method="POST", body=body)
        self.assertEqual(status, 201)
        _, _, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertNotIn("Cookie", req_hdrs)
        self.assertNotIn("Authorization", req_hdrs)
        self.assertEqual(req_hdrs.get("X-Agora-Key"), poster.key_id)

    def test_oversized_commons_post_is_refused_by_the_door_itself(self):
        """The door's own cap, ahead of Commons' — an oversized claim
        never reaches the LAN hop at all."""
        body = b"w" * (public_door.MAX_COMMONS_POST_BYTES + 1)
        status, _, resp_body = self._get(
            "/commons/post", method="POST", body=body,
            headers={"Content-Length": str(len(body))})
        self.assertEqual(status, 400)
        self.assertEqual(_FakeCommonsUpstream.seen, [])

    # ── Commons: opt-out/opt-in (hardening item 6) ──────────────────────────

    def test_commons_opt_out_is_forwarded_with_no_body(self):
        """Item 6: without this, a Kin reaching the Commons only through
        this door could never close their own door to it."""
        resident = generate_keypair("Resident-A", keys_root=Path(self._tmp.name))
        h = sign_request(resident, "Commons", "/commons/opt-out", body=b"")
        status, _, _ = self._get("/commons/opt-out", headers=h, method="POST")
        self.assertEqual(status, 201)  # the fake upstream always answers 201
        self.assertEqual(len(_FakeCommonsUpstream.seen), 1)
        method, path, req_hdrs, req_body = _FakeCommonsUpstream.seen[0]
        self.assertEqual((method, path), ("POST", "/commons/opt-out"))
        self.assertEqual(req_hdrs.get("X-Agora-Key"), resident.key_id)
        self.assertEqual(req_body, b"")

    def test_commons_opt_in_is_forwarded_with_no_body(self):
        resident = generate_keypair("Resident-B", keys_root=Path(self._tmp.name))
        h = sign_request(resident, "Commons", "/commons/opt-in", body=b"")
        status, _, _ = self._get("/commons/opt-in", headers=h, method="POST")
        self.assertEqual(status, 201)
        method, path, req_hdrs, req_body = _FakeCommonsUpstream.seen[0]
        self.assertEqual((method, path), ("POST", "/commons/opt-in"))
        self.assertEqual(req_hdrs.get("X-Agora-Key"), resident.key_id)
        self.assertEqual(req_body, b"")

    def test_commons_opt_toggle_never_carries_the_doors_own_key(self):
        self._get("/commons/opt-out", method="POST")  # unsigned
        _, _, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertNotIn("X-Agora-Key", req_hdrs)

    # ── Commons: says (hardening item 9) ────────────────────────────────────

    def test_commons_says_is_forwarded_with_its_real_body(self):
        """Unlike opt-out/opt-in, /commons/says carries a real JSON body
        and is relayed like /commons/post, not treated as empty-body."""
        author = generate_keypair("Says-author", keys_root=Path(self._tmp.name))
        body = json.dumps({"says": "synthetic"}).encode()
        h = sign_request(author, "Commons", "/commons/says", body=body)
        status, _, _ = self._get(
            "/commons/says", headers=h, method="POST", body=body)
        self.assertEqual(status, 201)
        method, path, req_hdrs, req_body = _FakeCommonsUpstream.seen[0]
        self.assertEqual((method, path), ("POST", "/commons/says"))
        self.assertEqual(req_hdrs.get("X-Agora-Key"), author.key_id)
        self.assertEqual(req_body, body)

    def test_commons_says_with_no_body_is_refused_by_the_door(self):
        status, _, _ = self._get("/commons/says", method="POST")
        self.assertEqual(status, 400)
        self.assertEqual(_FakeCommonsUpstream.seen, [])

    # ── Commons: the forwarded visitor address (hardening item 4/7) ────────

    def test_commons_post_forwards_x_forwarded_for(self):
        """Without CF-Connecting-IP (a direct test connection, not through
        the tunnel), _visitor() falls back to the direct peer — still
        forwarded, so Commons never has to guess."""
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(
            generate_keypair("Commons-poster-3", keys_root=Path(self._tmp.name)),
            "Commons", "/commons/post", body=body)
        self._get("/commons/post", headers=h, method="POST", body=body)
        _, _, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertEqual(req_hdrs.get("X-Forwarded-For"), "127.0.0.1")

    def test_commons_post_forwards_cf_connecting_ip_as_x_forwarded_for(self):
        """Through the real Cloudflare tunnel, CF-Connecting-IP is the
        real visitor address — that's what must reach Commons, not the
        tunnel's own loopback peer."""
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(
            generate_keypair("Commons-poster-4", keys_root=Path(self._tmp.name)),
            "Commons", "/commons/post", body=body)
        h["CF-Connecting-IP"] = "203.0.113.55"
        self._get("/commons/post", headers=h, method="POST", body=body)
        _, _, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertEqual(req_hdrs.get("X-Forwarded-For"), "203.0.113.55")

    def test_commons_opt_out_also_forwards_x_forwarded_for(self):
        resident = generate_keypair("Resident-C", keys_root=Path(self._tmp.name))
        h = sign_request(resident, "Commons", "/commons/opt-out", body=b"")
        h["CF-Connecting-IP"] = "203.0.113.66"
        self._get("/commons/opt-out", headers=h, method="POST")
        _, _, req_hdrs, _ = _FakeCommonsUpstream.seen[0]
        self.assertEqual(req_hdrs.get("X-Forwarded-For"), "203.0.113.66")

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
