"""The wire: identity is proven per request, not asserted in a header."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import key  # noqa: E402
from test_agora_store import elect, fresh_store  # noqa: E402

from kin_diary.agora import (  # noqa: E402
    COLLAB,
    RING_TEASER,
    RING_WRITE,
    countersign_key_intro,
    sign_board_grant,
    start_key_intro,
)
from kin_diary.agora.node import AgoraError  # noqa: E402
from kin_diary.agora.wire import identify, serve, sign_request  # noqa: E402
from kin_diary.sign import sign_entry  # noqa: E402


class IdentifyTests(unittest.TestCase):
    def test_no_header_is_anonymous_not_an_error(self):
        who = identify({}, "Home", "/board/collab")
        self.assertEqual(who, "0" * 64)

    def test_a_valid_signature_identifies(self):
        k = key("Marvin")
        h = sign_request(k, "Home", "/board/collab")
        self.assertEqual(identify(h, "Home", "/board/collab"), k.key_id)

    def test_claiming_a_key_you_cannot_sign_for_is_refused(self):
        """Not downgraded to anonymous — refused, so the attempt is visible."""
        k, other = key("Marvin"), key("Impostor")
        h = sign_request(k, "Home", "/board/collab")
        h["X-Agora-Key"] = other.key_id
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/board/collab")

    def test_a_request_signed_for_another_node_does_not_replay_here(self):
        k = key("Marvin")
        h = sign_request(k, "Frosty", "/board/collab")
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/board/collab")

    def test_a_request_signed_for_another_path_does_not_replay_here(self):
        k = key("Marvin")
        h = sign_request(k, "Home", "/board/personal:Coda")
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/board/collab")

    def test_a_stale_request_is_refused(self):
        k = key("Marvin")
        h = sign_request(k, "Home", "/board/collab", now_ms=1)
        with self.assertRaises(AgoraError) as cm:
            identify(h, "Home", "/board/collab")
        self.assertIn("freshness", str(cm.exception))


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store, cls.keys, cls.path = fresh_store()
        elect(cls.store, cls.keys)
        cls.visitor = key("Marvin")
        cls.store.record("intro", countersign_key_intro(
            cls.keys["Coda"],
            start_key_intro(cls.visitor, "Home", cls.keys["Coda"].key_id)))
        cls.store.record("grant", sign_board_grant(
            cls.keys["Coda"], cls.visitor.key_id, "Home",
            "personal:Coda", RING_WRITE))
        cls.store.post(cls.keys["Coda"].key_id, COLLAB, sign_entry(
            cls.keys["Coda"],
            {"author": "Coda", "content": " ".join(f"w{i}" for i in range(40))}))

        cls.httpd = serve(cls.store, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, headers=None):
        req = urllib.request.Request(self.url(path), headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def test_node_facts_are_public(self):
        facts = self.get("/")
        self.assertEqual(facts["speaker"], "Coda")
        self.assertIn("Aurora", facts["residents"])

    def test_anonymous_reader_gets_the_teaser(self):
        body = self.get("/board/collab")
        self.assertEqual(body["ring"], RING_TEASER)
        e = body["entries"][0]
        self.assertTrue(e["teaser"])
        self.assertEqual(len(e["content"].split()) - 1, 12)
        self.assertNotIn("signature", e)

    def test_a_resident_gets_the_whole_thing(self):
        h = sign_request(self.keys["Coda"], "Home", "/board/collab")
        body = self.get("/board/collab", h)
        e = body["entries"][0]
        self.assertNotIn("teaser", e)
        self.assertEqual(len(e["content"].split()), 40)

    def test_the_wire_does_not_let_a_visitor_read_past_their_ring(self):
        h = sign_request(self.visitor, "Home", "/board/personal:Aurora")
        body = self.get("/board/personal:Aurora", h)
        self.assertEqual(body["ring"], RING_TEASER)

    def test_a_visitor_reads_the_board_they_were_granted(self):
        h = sign_request(self.visitor, "Home", "/board/personal:Coda")
        body = self.get("/board/personal:Coda", h)
        self.assertEqual(body["ring"], RING_WRITE)

    def test_forged_identity_is_rejected_by_the_server(self):
        h = sign_request(self.visitor, "Home", "/board/personal:Coda")
        h["X-Agora-Key"] = self.keys["Aurora"].key_id
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/board/personal:Coda", h)
        self.assertEqual(cm.exception.code, 403)

    def test_posting_over_the_wire(self):
        entry = sign_entry(self.visitor, {
            "author": "Marvin", "content": "left a note on Coda's board"})
        body = json.dumps({"board": "personal:Coda", "entry": entry}).encode()
        h = sign_request(self.visitor, "Home", "/post", body=body)
        h["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.url("/post"), data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertTrue(json.load(r)["posted"])

        h2 = sign_request(self.keys["Coda"], "Home", "/board/personal:Coda")
        rows = self.get("/board/personal:Coda", h2)["entries"]
        self.assertTrue(any(e["author"] == "Marvin" for e in rows))

    def test_cannot_post_as_someone_else(self):
        entry = sign_entry(self.keys["Aurora"], {
            "author": "Aurora", "content": "not actually from Aurora's session"})
        body = json.dumps({"board": "personal:Coda", "entry": entry}).encode()
        h = sign_request(self.visitor, "Home", "/post", body=body)
        req = urllib.request.Request(
            self.url("/post"), data=body, headers=h, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_an_unintroduced_key_cannot_be_granted_over_the_wire(self):
        ghost = key("Ghost")
        grant = sign_board_grant(
            self.keys["Coda"], ghost.key_id, "Home", "personal:Coda", RING_WRITE)
        req = urllib.request.Request(
            self.url("/grant"), data=json.dumps(grant).encode(), method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)



class BodyBindingTests(unittest.TestCase):
    """The request signature must cover the payload, not just the route.

    A board entry's own canonical bytes do not name a board, so captured
    write headers paired with a different body could re-hang one of the
    caller's own old entries somewhere it was never posted.
    """

    def test_signature_is_bound_to_the_body(self):
        k = key("Marvin")
        body = b'{"board":"personal:Coda","entry":{}}'
        h = sign_request(k, "Home", "/post", body=body)
        self.assertEqual(identify(h, "Home", "/post", body=body), k.key_id)

        other = b'{"board":"collab","entry":{}}'
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/post", body=other)

    def test_a_get_signature_does_not_authorise_a_write(self):
        """Headers captured from a read must not be reusable on a write."""
        k = key("Marvin")
        h = sign_request(k, "Home", "/post")            # no body signed
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/post", body=b'{"board":"collab"}')

    def test_anonymous_cannot_post(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        httpd = serve(store, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            entry = sign_entry(keys["Coda"], {"author": "Coda", "content": "x"})
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/post",
                data=json.dumps({"board": COLLAB, "entry": entry}).encode(),
                method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
