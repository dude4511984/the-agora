"""Tests for local signed presence submission and persistence."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import key

from kin_diary.agora import (
    countersign_key_intro,
    open_speaker_election,
    sign_board_evict,
    sign_speaker_election,
    start_key_intro,
)
from kin_diary.agora.places import Atlas, sign_place, sign_presence
from kin_diary.agora.presence_wire import (
    PRESENCE_ERROR,
    PresenceError,
    PresenceStore,
    accept_presence,
)
from kin_diary.agora.store import NodeStore


class PresenceWireTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.node_store = NodeStore(self.root / "node.db", "Home")
        self.coda = key("Coda")
        self.marvin = key("Marvin")
        self.stranger = key("Stranger")
        self.node_store.add_resident("Coda", self.coda.key_id)
        election = open_speaker_election(
            "Home", "Coda", self.coda.key_id, {self.coda.key_id}
        )
        self.node_store.record("election", sign_speaker_election(self.coda, election))
        self.node_store.record(
            "intro",
            countersign_key_intro(
                self.coda,
                start_key_intro(self.marvin, "Home", self.coda.key_id),
            ),
        )
        self.atlas = Atlas(self.node_store.load(), store=self.node_store)
        node_key = key("Home-node")
        self.atlas.add_place(sign_place(node_key, "room", "Home", "table"))
        self.presences = PresenceStore(self.node_store)

    def tearDown(self):
        self.presences.close()
        self.node_store.close()

    def _presence(self, who=None, *, node="Home", expires=10_000):
        return sign_presence(
            who or self.marvin, node, "room", "here",
            ttl_ms=expires - 1_000, now_ms=1_000,
        )

    def test_accepts_self_signed_presence_and_restores_after_reload(self):
        presence = self._presence()
        accept_presence(
            self.node_store, self.atlas, self.presences, self.marvin.key_id,
            presence, now_ms=1_500,
        )
        reloaded = Atlas(self.node_store.load(), store=self.node_store)
        node_key = key("Home-node-2")
        reloaded.add_place(sign_place(node_key, "room", "Home", "table"))
        restored = self.presences.restore(reloaded, now_ms=1_500)
        self.assertEqual(restored, [presence])
        self.assertEqual(reloaded.presence[self.marvin.key_id], presence)

    def test_presence_for_another_node_is_refused_and_not_saved(self):
        with self.assertRaisesRegex(PresenceError, PRESENCE_ERROR):
            accept_presence(
                self.node_store, self.atlas, self.presences,
                self.marvin.key_id, self._presence(node="Frosty"), now_ms=1_500,
            )
        self.assertEqual(self.presences.restore(self.atlas, now_ms=1_500), [])

    def test_signature_failure_is_refused(self):
        presence = self._presence()
        presence["label"] = "forged"
        with self.assertRaisesRegex(PresenceError, PRESENCE_ERROR):
            accept_presence(
                self.node_store, self.atlas, self.presences,
                self.marvin.key_id, presence, now_ms=1_500,
            )

    def test_host_cannot_submit_presence_for_another_key(self):
        with self.assertRaisesRegex(PresenceError, PRESENCE_ERROR):
            accept_presence(
                self.node_store, self.atlas, self.presences,
                self.coda.key_id, self._presence(), now_ms=1_500,
            )

    def test_never_introduced_key_cannot_stand(self):
        with self.assertRaisesRegex(PresenceError, PRESENCE_ERROR):
            accept_presence(
                self.node_store, self.atlas, self.presences,
                self.stranger.key_id, self._presence(self.stranger), now_ms=1_500,
            )

    def test_ttl_is_required_and_must_be_live(self):
        missing = self._presence()
        del missing["expires_at_unix_ms"]
        with self.assertRaisesRegex(PresenceError, PRESENCE_ERROR):
            accept_presence(
                self.node_store, self.atlas, self.presences,
                self.marvin.key_id, missing, now_ms=1_500,
            )
        expired = self._presence(expires=1_500)
        with self.assertRaisesRegex(PresenceError, PRESENCE_ERROR):
            accept_presence(
                self.node_store, self.atlas, self.presences,
                self.marvin.key_id, expired, now_ms=1_500,
            )

    def test_eviction_removes_existing_presence_on_restore(self):
        presence = self._presence()
        accept_presence(
            self.node_store, self.atlas, self.presences, self.marvin.key_id,
            presence, now_ms=1_500,
        )
        other = NodeStore(self.node_store.path, "Home")
        try:
            other.record(
                "evict",
                sign_board_evict(
                    self.coda, self.marvin.key_id, "Home", "gone",
                ),
            )
        finally:
            other.close()
        self.assertEqual(self.atlas.view(self.marvin.key_id, now_ms=1_500)["presence"], [])
        reloaded = Atlas(self.node_store.load(), store=self.node_store)
        node_key = key("Home-node-3")
        reloaded.add_place(sign_place(node_key, "room", "Home", "table"))
        self.assertEqual(self.presences.restore(reloaded, now_ms=1_500), [])
        row = self.presences.conn.execute(
            "SELECT COUNT(*) FROM agora_presence"
        ).fetchone()
        self.assertEqual(row[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
