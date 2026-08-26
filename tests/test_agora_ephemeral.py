"""Tests for the Speaker-issued ephemeral admission path."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import key

from kin_diary.agora import open_speaker_election, sign_speaker_election
from kin_diary.agora.ephemeral import (
    MAX_EPHEMERAL_MS,
    EphemeralError,
    EphemeralStore,
    countersign_ephemeral,
    issue_ephemeral,
    reject_if_ephemeral,
    verify_ephemeral,
)
from kin_diary.agora.store import NodeStore


class EphemeralTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = NodeStore(self.root / "node.db", "Home")
        self.coda = key("Coda")
        self.holder = key("Debug-holder")
        self.other = key("Other-holder")
        self.store.add_resident("Coda", self.coda.key_id)
        election = open_speaker_election(
            "Home", "Coda", self.coda.key_id, {self.coda.key_id}
        )
        self.store.record("election", sign_speaker_election(self.coda, election))
        self.node = self.store.load()
        self.ephemerals = EphemeralStore(self.store)

    def tearDown(self):
        self.ephemerals.close()
        self.store.close()

    def _issued(self, **changes):
        values = {
            "host_node": "Home",
            "speaker_key_id": self.coda.key_id,
            "issued_at_unix_ms": 1_000,
            "expires_at_unix_ms": 2_000,
            "max_ring": 2,
            "board": "personal:Coda",
            "purpose": "debug",
        }
        values.update(changes)
        return issue_ephemeral(self.holder, **values)

    def test_holder_signs_first_and_speaker_countersigns(self):
        issued = self._issued()
        self.assertIn("sig_holder", issued)
        self.assertNotIn("sig_speaker", issued)
        event = countersign_ephemeral(self.coda, issued, self.node)
        verify_ephemeral(event, self.node)
        self.ephemerals.record(event)
        self.assertEqual(
            self.ephemerals.current(self.holder.key_id, "personal:Coda", 1_500),
            event,
        )

    def test_receipt_verifies_after_expiry_but_admission_does_not(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        verify_ephemeral(event, self.node)
        self.ephemerals.record(event)
        self.assertIsNone(
            self.ephemerals.current(self.holder.key_id, "personal:Coda", 2_000)
        )

    def test_expired_ephemeral_is_a_dead_end_for_path_two(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.ephemerals.record(event)
        self.assertTrue(self.ephemerals.was_ephemeral(self.holder.key_id))
        with self.assertRaisesRegex(EphemeralError, "cannot graduate"):
            reject_if_ephemeral(self.ephemerals, self.holder.key_id)

    def test_rejects_bad_shape_and_policy(self):
        cases = (
            {"max_ring": 3},
            {"max_ring": 0},
            {"expires_at_unix_ms": 1_000},
            {"expires_at_unix_ms": 1_000 + MAX_EPHEMERAL_MS + 1},
            {"purpose": "chat"},
            {"board": "*"},
            {"board": ""},
        )
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaises(EphemeralError):
                    self._issued(**change)

    def test_rejects_unseated_speaker_and_missing_signatures(self):
        other = key("Not-speaker")
        event = self._issued(speaker_key_id=other.key_id)
        with self.assertRaises(EphemeralError):
            countersign_ephemeral(other, event, self.node)
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        del event["sig_holder"]
        with self.assertRaises(EphemeralError):
            verify_ephemeral(event, self.node)

    def test_durable_journal_survives_second_store_instance(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.ephemerals.record(event)
        second_node_store = NodeStore(self.store.path, "Home")
        second_ephemerals = EphemeralStore(second_node_store)
        try:
            self.assertTrue(second_ephemerals.was_ephemeral(self.holder.key_id))
            self.assertEqual(
                second_ephemerals.current(
                    self.holder.key_id, "personal:Coda", 1_500
                ),
                event,
            )
        finally:
            second_ephemerals.close()
            second_node_store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
