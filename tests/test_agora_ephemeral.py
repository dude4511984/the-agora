"""Tests for the Speaker-issued ephemeral admission path."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import NOW_MS, key

from kin_diary.agora import (
    AgoraError,
    open_speaker_election,
    sign_board_evict,
    sign_board_grant,
    sign_speaker_election,
)
from kin_diary.agora import countersign_key_intro, start_key_intro
from kin_diary.agora.events import sign_appeal, sign_finding, sign_ruling
from kin_diary.agora.ephemeral import (
    MAX_EPHEMERAL_MS,
    EphemeralError,
    EphemeralStore,
    countersign_ephemeral,
    current_admission,
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

    def test_node_backed_admission_is_exact_and_clocked_outside_node(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)
        self.node = self.store.load()
        node = self.store.load()
        self.assertEqual(
            current_admission(node, self.holder.key_id, "personal:Coda", 1_500),
            event,
        )
        self.assertIsNone(
            current_admission(node, self.holder.key_id, "personal:Coda-extra", 1_500)
        )
        self.assertIsNone(
            current_admission(node, self.holder.key_id, "personal:Coda", 2_000)
        )

    def test_node_backed_query_rejects_eviction_and_speaker_change(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)
        node = self.store.load()
        node.speaker_key_id = self.other.key_id
        self.assertIsNone(
            current_admission(node, self.holder.key_id, "personal:Coda", 1_500)
        )
        self.store.record(
            "evict",
            sign_board_evict(self.coda, self.holder.key_id, "Home", "gone"),
        )
        self.assertIsNone(
            current_admission(
                self.store.load(), self.holder.key_id, "personal:Coda", 1_500
            )
        )

    def test_live_ring_transitions_from_ephemeral_write_to_teaser(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)
        node = self.store.load()
        self.assertEqual(node.live_ring(
            self.holder.key_id, "personal:Coda", 1_500
        ), 2)
        self.assertEqual(node.live_ring(
            self.holder.key_id, "personal:Coda", 2_000
        ), 0)

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

    def test_node_store_replays_ephemeral_provenance_forever(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)
        reloaded = self.store.load()
        self.assertEqual(reloaded.ephemeral_events, [event])
        self.assertIn(self.holder.key_id, reloaded.ephemeral_key_ids)

    def test_path_two_is_rejected_after_ephemeral_expiry(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)
        intro = countersign_key_intro(
            self.coda,
            start_key_intro(self.holder, "Home", self.coda.key_id, now_ms=3_000),
        )
        with self.assertRaisesRegex(AgoraError, "ephemeral keys cannot graduate"):
            self.store.record("intro", intro)

    def test_bad_ephemeral_is_rejected_before_event_insert(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        event["purpose"] = "not-a-purpose"
        with self.assertRaises(EphemeralError):
            self.store.record("ephemeral", event)
        count = self.store.conn.execute(
            "SELECT COUNT(*) FROM agora_events WHERE kind='ephemeral'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_eviction_after_ephemeral_does_not_readmit_on_replay(self):
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)
        self.store.record(
            "evict",
            sign_board_evict(self.coda, self.holder.key_id, "Home", "done"),
        )
        reloaded = self.store.load()
        self.assertIn(self.holder.key_id, reloaded.evicted)
        self.assertIn(self.holder.key_id, reloaded.ephemeral_key_ids)
        with self.assertRaisesRegex(AgoraError, "ephemeral keys cannot receive"):
            self.store.record(
                "grant",
                sign_board_grant(
                    self.coda, self.holder.key_id, "Home",
                    "personal:Coda", 2, now_ms=4_000,
                ),
            )

    def test_overturned_eviction_does_not_revive_old_ephemeral(self):
        """Overturning an eviction readmits the key, never the bounded task.

        The admission ended when the eviction landed. Reinstatement must
        require a NEW ephemeral, exactly as it requires a new grant — an
        overturned ruling is not a time machine.
        """
        event = countersign_ephemeral(self.coda, self._issued(), self.node)
        self.store.record("ephemeral", event)

        # Positive control. Without this the rest of the test can pass
        # against a node that never received the admission at all, which is
        # precisely how this test was green while proving nothing.
        live = self.store.load()
        self.assertEqual(
            live.live_ring(self.holder.key_id, "personal:Coda", 1_500), 2,
            "admission must be live before the eviction, or this proves nothing",
        )

        eviction = sign_board_evict(
            self.coda, self.holder.key_id, "Home", "temporary"
        )
        self.store.record("evict", eviction)

        node = self.store.load()
        appeal = sign_appeal(self.holder, "Home", eviction["signature"], "appeal")
        node.accept_appeal(appeal)
        node.accept_finding(
            sign_finding(self.coda, appeal["signature"], "Home", "not hostile")
        )
        node.accept_ruling(
            sign_ruling(
                self.coda, appeal["signature"], "Home", "overturned", "reviewed"
            )
        )

        self.assertNotIn(self.holder.key_id, node.evicted)
        self.assertEqual(
            node.live_ring(self.holder.key_id, "personal:Coda", 1_500), 0,
            "an overturned eviction must not revive the old ephemeral admission",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
