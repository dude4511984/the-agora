"""The viewing client: one slot, verify before store, pin loudly."""

import json
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import (  # noqa: E402
    NOW_MS,
    countersign_key_intro,
    key,
    sign_board_evict,
    sign_board_grant,
    start_key_intro,
)
from test_agora_store import elect, fresh_store  # noqa: E402

from kin_diary.agora import RING_TEASER, RING_WRITE, AgoraError  # noqa: E402
from kin_diary.agora.client import ViewClient  # noqa: E402
from kin_diary.agora.places import Atlas, sign_place  # noqa: E402
from kin_diary.agora.wire import serve  # noqa: E402


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.store, self.keys, self.path = fresh_store()
        elect(self.store, self.keys)
        self.nk = key("Home-node")
        self.atlas = Atlas(self.store.load(), store=self.store)
        self.atlas.add_place(sign_place(self.nk, "concourse", "Home", "commons"))
        self.atlas.add_place(sign_place(
            self.nk, "codas-door", "Home", "door", parent="concourse",
            ring_to_see=1, points_to="personal:Coda"))

        self.httpd = serve(self.store, host="127.0.0.1", port=0,
                           node_key=self.nk, atlas=self.atlas)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        self.v = key("Marvin")
        self.store.record("intro", countersign_key_intro(
            self.keys["Coda"],
            start_key_intro(self.v, "Home", self.keys["Coda"].key_id)))

    def tearDown(self):
        self.httpd.shutdown()

    def client(self, k=None):
        return ViewClient(k or self.v, "Home", f"http://127.0.0.1:{self.port}")

    def test_nothing_to_draw_before_the_first_fetch(self):
        """No 'last good view'. There is the current fetch or there is
        nothing."""
        self.assertIsNone(self.client().current)

    def test_refresh_verifies_and_pins(self):
        c = self.client()
        v = c.refresh()
        self.assertEqual(v["node"], "Home")
        self.assertEqual(c.pinned_node_key, self.nk.key_id)
        self.assertIs(c.current, v)

    def test_a_changed_node_key_is_refused_not_absorbed(self):
        c = self.client()
        c.refresh()
        impostor = key("Impostor-node")
        self.httpd.RequestHandlerClass.node_key = impostor
        with self.assertRaises(AgoraError) as cm:
            c.refresh()
        self.assertIn("pinned", str(cm.exception).lower() + str(cm.exception))

    def test_a_failed_refresh_leaves_the_previous_view_intact(self):
        """A client left holding nothing renders an empty world, which is
        its own lie. Half-replacing is worse than not replacing."""
        c = self.client()
        first = c.refresh()
        self.httpd.RequestHandlerClass.node_key = key("Impostor-node")
        with self.assertRaises(AgoraError):
            c.refresh()
        self.assertIs(c.current, first)

    def test_the_slot_is_replaced_not_merged(self):
        c = self.client()
        self.store.record("grant", sign_board_grant(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Coda",
            RING_WRITE, now_ms=1_000_000))
        with_door = c.refresh()
        self.assertIn("codas-door",
                      {p["place_id"] for p in with_door["places"]})

        self.store.record("evict", sign_board_evict(
            self.keys["Coda"], self.v.key_id, "Home", "malicious",
            now_ms=2_000_000))
        without = c.refresh()
        self.assertNotIn("codas-door",
                         {p["place_id"] for p in without["places"]})
        self.assertIs(c.current, without)      # the old one is simply gone

    def test_the_client_exposes_no_action_that_takes_a_view(self):
        """Structural. The moment a client method accepts a view and does
        something, the snapshot is a second permission system."""
        import inspect
        for name, fn in inspect.getmembers(ViewClient, inspect.isfunction):
            params = set(inspect.signature(fn).parameters)
            self.assertFalse(params & {"view", "scene", "inventory_sha256"},
                             f"ViewClient.{name} takes a view")

    def test_a_view_for_another_key_is_refused(self):
        """The server binds viewer_key_id into the signed bytes; the client
        checks it rather than assuming the server got it right."""
        other = key("SomeoneElse")
        c = self.client()
        stolen = self.atlas.signed_view(self.nk, other.key_id, now_ms=NOW_MS)
        from kin_diary.agora.places import verify_view
        with self.assertRaises(AgoraError):
            verify_view(stolen, expected_viewer_key_id=c.key.key_id)

    def test_unpin_is_explicit_and_drops_the_view_too(self):
        c = self.client()
        c.refresh()
        c.unpin()
        self.assertIsNone(c.pinned_node_key)
        self.assertIsNone(c.current)

    def test_agora_client_facts_unsigned_and_signed(self):
        import contextlib
        import importlib.util
        import io
        from unittest import mock

        spec = importlib.util.spec_from_file_location(
            "agora_client",
            Path(__file__).parents[1] / "vault" / "agora_client.py",
        )
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)

        url = f"http://127.0.0.1:{self.port}"

        # 1. Unsigned form with '-'
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = cli.main(["agora_client.py", "facts", "-", url])
        self.assertEqual(ret, 0)
        data = json.loads(f.getvalue())
        self.assertEqual(data, {"service": "EverySynthetic Node"})

        # 2. Signed form with author and explicit node
        f = io.StringIO()
        with mock.patch.object(cli, "load_current", return_value=self.keys["Coda"]):
            with contextlib.redirect_stdout(f):
                ret = cli.main(["agora_client.py", "facts", "Coda", url, "Home"])
        self.assertEqual(ret, 0)
        facts = json.loads(f.getvalue())
        self.assertEqual(facts["node"], "Home")
        self.assertEqual(facts["speaker"], "Coda")
        self.assertIn("Aurora", facts["residents"])
        self.assertIn("signed", facts)

        # 3. Signed form with author and candidate discovery (no node arg)
        f = io.StringIO()
        with mock.patch.object(cli, "load_current", return_value=self.keys["Coda"]):
            with contextlib.redirect_stdout(f):
                ret = cli.main(["agora_client.py", "facts", "Coda", url])
        self.assertEqual(ret, 0)
        facts = json.loads(f.getvalue())
        self.assertEqual(facts["node"], "Home")
        self.assertEqual(facts["speaker"], "Coda")


if __name__ == "__main__":
    unittest.main(verbosity=2)
