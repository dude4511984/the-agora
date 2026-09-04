"""Cross-node discovery tests, including the real Home node smoke test."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Every other module in this directory carries these two lines. This one did
# not, so `python3 tests/test_agora_federation.py` — the way the rest of the
# suite is run — died on ModuleNotFoundError before collecting a single test.
# It only ever passed for someone who happened to set PYTHONPATH.
sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kin_diary.agora.events import sign_node_fact, sign_notice
from kin_diary.agora.federation import (
    MAX_PEER_RESPONSE_BYTES,
    MAX_NOTICES_PER_FETCH,
    PeerResponseTooLarge,
    PeerUnreachable,
    PeerVerificationError,
    discover_peer,
    exchange,
    fetch_peer_notices,
)
from kin_diary.agora.store import NodeStore
from kin_diary.keys import generate_keypair


class _ResponseHandler(BaseHTTPRequestHandler):
    response = {}
    routes = {}

    def log_message(self, *_args):
        pass

    def do_GET(self):
        body = self.routes.get(self.path, self.response)
        raw = (json.dumps(body) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = NodeStore(self.root / "node.db", "Local")
        self.peer_key = generate_keypair("Peer", keys_root=self.root / "keys")
        self.relay_key = generate_keypair("Relay", keys_root=self.root / "relay")
        self.fact = sign_node_fact(
            self.peer_key, "Peer", "Coda", "a" * 64, ["Coda", "Lumen"], now_ms=1
        )
        self.notice = sign_notice(
            self.peer_key, "Peer", "looking for a collaborator",
            "A signed public notice.", "mailto:peer@example.test", now_ms=1
        )
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.store.close()

    def serve(self, root=None, notices=None):
        _ResponseHandler.response = root or {"node": "Peer"}
        _ResponseHandler.routes = {
            "/notices": {"node": "Peer", "notices": notices or []}
        }
        self.server = HTTPServer(("127.0.0.1", 0), _ResponseHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def test_discovery_verifies_and_pins_on_first_use(self):
        url = self.serve({"node": "Peer", "signed": self.fact})
        got = discover_peer(self.store, url)
        self.assertEqual(got["node_key_id"], self.peer_key.key_id)
        self.assertEqual(self.store.known_peers()[0]["peer"], "Peer")

    def test_unreachable_peer_is_explicit(self):
        with self.assertRaises(PeerUnreachable):
            discover_peer(self.store, "http://127.0.0.1:1", timeout=0.2)

    def test_unsigned_facts_are_rejected_and_not_pinned(self):
        url = self.serve({"node": "Peer", "speaker": "Coda"})
        with self.assertRaises(PeerVerificationError):
            discover_peer(self.store, url)
        self.assertEqual(self.store.known_peers(), [])

    def test_pinned_key_swap_is_rejected(self):
        url = self.serve({"node": "Peer", "signed": self.fact})
        discover_peer(self.store, url)
        other = generate_keypair("Other", keys_root=self.root / "other")
        swapped = sign_node_fact(
            other, "Peer", "Coda", "a" * 64, ["Coda"], now_ms=2
        )
        _ResponseHandler.response = {"node": "Peer", "signed": swapped}
        with self.assertRaises(PeerVerificationError):
            discover_peer(self.store, url)

    def test_notice_signature_failure_is_rejected(self):
        url = self.serve({"node": "Peer", "signed": self.fact}, [self.notice])
        discover_peer(self.store, url)
        _ResponseHandler.routes["/notices"]["notices"][0]["body"] = "tampered"
        with self.assertRaises(PeerVerificationError):
            fetch_peer_notices(self.store, "Peer")

    def test_valid_foreign_notice_is_a_relay_not_a_forgery(self):
        relay_notice = sign_notice(
            self.relay_key, "Relay", "relay", "A notice from another node.",
            "mailto:relay@example.test", now_ms=2
        )
        url = self.serve({"node": "Peer", "signed": self.fact}, [relay_notice])
        discover_peer(self.store, url)
        received = fetch_peer_notices(self.store, "Peer")
        self.assertEqual(len(received), 1)
        self.assertTrue(received[0].relayed)

    def test_exchange_returns_verified_peer_and_notices(self):
        url = self.serve({"node": "Peer", "signed": self.fact}, [self.notice])
        facts, notices = exchange(self.store, url)
        self.assertEqual(facts["node"], "Peer")
        self.assertEqual(notices[0].notice["subject"], self.notice["subject"])
        self.assertFalse(notices[0].relayed)


    def test_an_oversize_peer_response_is_refused_before_parse(self):
        """A peer is someone else's computer. json.load on the socket is
        unbounded work on their word (P9a). More than the envelope is refused
        before it is parsed or verified -- not a pin failure, its own type."""
        pad = "x" * (MAX_PEER_RESPONSE_BYTES + 4096)
        url = self.serve({"node": "Peer", "signed": self.fact, "pad": pad})
        with self.assertRaises(PeerResponseTooLarge):
            discover_peer(self.store, url)

    def test_more_than_fifty_notices_is_refused_before_verify(self):
        """The count fuse fires before the verify loop: the 51st notice never
        reaches verify_notice (P9a). Even valid notices past the cap are a
        dump, not a fetch."""
        url = self.serve({"node": "Peer", "signed": self.fact}, [self.notice])
        discover_peer(self.store, url)
        _ResponseHandler.routes["/notices"]["notices"] = [
            dict(self.notice) for _ in range(MAX_NOTICES_PER_FETCH + 1)
        ]
        with self.assertRaises(PeerResponseTooLarge):
            fetch_peer_notices(self.store, "Peer")


@unittest.skipUnless(
    os.environ.get("AGORA_RUN_LIVE", "1") == "1",
    "set AGORA_RUN_LIVE=1 to run the live-node smoke test",
)
class LiveFederationTest(unittest.TestCase):
    def test_live_home_discovery_and_notice_exchange(self):
        root = Path(tempfile.mkdtemp())
        store = NodeStore(root / "live.db", "Probe")
        url = os.environ.get("AGORA_LIVE_URL", "http://192.168.1.120:8770")
        try:
            try:
                facts, notices = exchange(store, url)
            except PeerUnreachable as unreachable:
                # A live smoke test against a real cluster node must DEGRADE to
                # a skip when that node is asleep, not redden the whole suite.
                # Home being down is cluster weather, not a regression in this
                # code. The library's unreachable-raises contract is covered by
                # the 127.0.0.1:1 test above; this one only has something to say
                # when Home is actually up. 2026-09-04, punch-list P10.
                self.skipTest(f"live peer unreachable at {url}: {unreachable}")
            self.assertEqual(facts["node"], "Home")
            self.assertEqual(facts["node_key_id"], store.known_peers()[0]["peer_key_id"])
            for envelope in notices:
                self.assertIsInstance(envelope.notice["signature"], str)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
