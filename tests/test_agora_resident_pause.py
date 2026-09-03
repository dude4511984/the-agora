import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PYTHONWARNINGS", "ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/kin_diary"))

from test_agora import key

from kin_diary.agora import (
    AgoraError,
    Node,
    open_speaker_election,
    sign_board_evict,
    sign_board_grant,
    sign_board_revoke,
    sign_resident,
    sign_speaker_election,
    start_key_intro,
    countersign_key_intro,
)
from kin_diary.agora.events import sign_appeal, sign_finding, sign_ruling
from kin_diary.agora.events import verify_speaker_election
from kin_diary.agora.store import NodeStore


class ResidentEventTests(unittest.TestCase):
    def test_resident_replays_after_reopen(self):
        path = Path(tempfile.mkdtemp()) / "node.db"
        steward, first, second = key("Steward"), key("First"), key("Second")
        store = NodeStore(path, "Home", steward_key_id=steward.key_id)
        store.add_resident("First", first.key_id)
        event = sign_resident(steward, "Home", "Second", second.key_id, 1)
        store.record("resident", event)
        store.close()
        reopened = NodeStore(path, "Home", steward_key_id=steward.key_id)
        self.assertEqual(reopened.load().residents["Second"], second.key_id)

    def test_resident_requires_configured_steward(self):
        path = Path(tempfile.mkdtemp()) / "node.db"
        steward, second = key("Steward"), key("Second")
        store = NodeStore(path, "Home", steward_key_id=steward.key_id)
        # The impostor signs their own steward_key_id, so verify_resident
        # accepts the signature — the gate is the ONLY thing refusing this.
        # Assert the reason, not just the raise: otherwise the day a verify
        # check starts refusing the impostor for some other reason, deleting
        # the gate keeps this green and the gate rots untested (Grok: reason
        # or nothing).
        with self.assertRaises(AgoraError) as caught:
            store.record("resident", sign_resident(
                key("Impostor"), "Home", "Second", second.key_id, 1))
        self.assertIn("only this node's steward can add residents",
                      str(caught.exception))

    def test_frosty_growth_sequence_reopens_with_old_election_and_pause(self):
        path = Path(tempfile.mkdtemp()) / "frosty.db"
        steward, marvin = key("Steward"), key("Marvin")
        additions = [key(name) for name in ("Eli", "Crungus", "Bong")]
        store = NodeStore(path, "Frosty", steward_key_id=steward.key_id)
        store.add_resident("Marvin", marvin.key_id)
        election = open_speaker_election(
            "Frosty", "Marvin", marvin.key_id, {marvin.key_id})
        store.record("election", sign_speaker_election(marvin, election))
        for name, resident in zip(("Eli", "Crungus", "Bong"), additions):
            store.record("resident", sign_resident(
                steward, "Frosty", name, resident.key_id, 1))
        store.close()

        reopened = NodeStore(path, "Frosty", steward_key_id=steward.key_id)
        node = reopened.load()
        self.assertIsNone(node.speaker_key_id)
        self.assertTrue(node.is_paused())
        self.assertEqual(set(node.residents), {"Marvin", "Eli", "Crungus", "Bong"})
        verify_speaker_election(node.election)

    def _elected(self):
        node = Node("Home")
        steward, first, second = key("Steward"), key("First"), key("Second")
        node.add_resident("First", first.key_id)
        election = open_speaker_election("Home", "First", first.key_id, {first.key_id})
        node.accept_election(sign_speaker_election(first, election))
        return node, steward, first, second

    def test_growth_outside_electorate_vacates_but_election_remains(self):
        node, steward, first, second = self._elected()
        event = sign_resident(steward, "Home", "Second", second.key_id, 1)
        node.accept_resident(event)
        self.assertIsNone(node.speaker_key_id)
        self.assertEqual(node.election["electorate"], [first.key_id])
        verify_speaker_election(node.election)
        self.assertTrue(node.is_paused())

    def test_growth_inside_electorate_does_not_vacate(self):
        node, steward, first, second = self._elected()
        node.election["electorate"].append(second.key_id)
        node.accept_resident(sign_resident(
            steward, "Home", "Second", second.key_id, 1))
        self.assertEqual(node.speaker_key_id, first.key_id)

    def test_quarantine_does_not_vacate(self):
        node, _, first, _ = self._elected()
        node.quarantined_keys.add(first.key_id)
        self.assertEqual(node.speaker_key_id, first.key_id)

    def test_removing_resident_does_not_vacate(self):
        node, _, first, _ = self._elected()
        del node.residents["First"]
        self.assertEqual(node.speaker_key_id, first.key_id)


class PauseTests(unittest.TestCase):
    def paused(self):
        node = Node("Home")
        a, b = key("A"), key("B")
        node.add_resident("A", a.key_id)
        node.add_resident("B", b.key_id)
        return node, a, b

    def test_pause_is_derived(self):
        node, a, _ = self.paused()
        self.assertTrue(node.is_paused())
        node = Node("Solo")
        node.add_resident("A", a.key_id)
        node.sole_resident_is_speaker()
        self.assertFalse(node.is_paused())

    def test_seated_speaker_is_not_paused(self):
        node = Node("Home")
        a, b = key("A"), key("B")
        node.add_resident("A", a.key_id)
        node.add_resident("B", b.key_id)
        node.speaker_key_id = a.key_id
        self.assertFalse(node.is_paused())

    def test_pause_facts_are_public(self):
        node, _, _ = self.paused()
        facts = node.node_facts()
        self.assertTrue(facts["paused"])
        self.assertIn("Comings and goings paused", facts["pause_reason"])
        node.speaker_key_id = key("A").key_id
        self.assertFalse(node.node_facts()["paused"])
        self.assertIsNone(node.node_facts()["pause_reason"])

    def test_each_frozen_mutation_is_refused(self):
        node, a, _ = self.paused()
        visitor = key("Visitor")
        intro = countersign_key_intro(a, start_key_intro(
            visitor, "Home", a.key_id))
        with self.subTest("intro"), self.assertRaises(AgoraError):
            node.accept_intro(intro)
        from test_agora import eli_with_bundle
        with self.subTest("bundle"), self.assertRaises(AgoraError):
            node.accept_bundle_import(eli_with_bundle()[1])
        node.visitor_ceiling[visitor.key_id] = 2
        for ring, board in ((1, "personal:A"), (2, "personal:A"), (3, "*")):
            with self.subTest("grant", ring=ring), self.assertRaises(AgoraError):
                node.accept_grant(sign_board_grant(
                    a, visitor.key_id, "Home", board, ring))
        node.grants[visitor.key_id] = {"personal:A": 1}
        with self.subTest("revoke"), self.assertRaises(AgoraError):
            node.accept_revocation(sign_board_revoke(
                a, visitor.key_id, "Home", "personal:A"))
        with self.subTest("eviction"), self.assertRaises(AgoraError):
            node.accept_eviction(sign_board_evict(
                a, visitor.key_id, "Home", "reason"))

    def test_election_is_not_frozen(self):
        node, a, b = self.paused()
        election = open_speaker_election(
            "Home", "A", a.key_id, {a.key_id, b.key_id})
        election = sign_speaker_election(a, election)
        election = sign_speaker_election(b, election)
        node.accept_election(election)
        self.assertFalse(node.is_paused())

    def test_hearing_is_not_frozen(self):
        node, a, _ = self.paused()
        visitor = key("Visitor")
        node.evictions["e" * 128] = {
            "visitor_key_id": visitor.key_id,
        }
        appeal = sign_appeal(visitor, "Home", "e" * 128, "reason")
        node.accept_appeal(appeal)
        finding = sign_finding(a, appeal["signature"], "Home", "finding")
        node.accept_finding(finding)
        ruling = sign_ruling(
            key("Steward"), appeal["signature"], "Home",
            "upheld", "reason")
        node.accept_ruling(ruling)
        self.assertIn(appeal["signature"], node.rulings)


if __name__ == "__main__":
    unittest.main()
