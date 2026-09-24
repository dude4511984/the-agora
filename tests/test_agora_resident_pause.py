import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PYTHONWARNINGS", "ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one

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
        """The DOOR acts a pause freezes. Each must be refused *by the pause*,
        so assert PAUSE_REASON, not merely AgoraError: a bare assertRaises here
        is a receipt, because most of these acts can raise for their own
        reasons too.

        Verified by mutant on _refuse_if_paused (no-op): every subtest below
        goes red, i.e. the act succeeds and only the pause was stopping it.
        Eviction and ring-3 grant were REMOVED from this test on 2026-09-03:
        they survived that mutant. Both are SWORD acts that additionally need
        a Speaker ("no Speaker seated — ... unreachable"), so in a paused
        house they are refused whether or not the pause guard runs, and this
        test never measured the pause for them. Their real guards live in
        test_the_sword_stays_sheathed (eviction, rotated reason) and
        test_no_speaker_means_ring_3_is_unreachable_not_auto_granted.
        """
        node, a, _ = self.paused()
        visitor = key("Visitor")
        pause = node.PAUSE_REASON
        intro = countersign_key_intro(a, start_key_intro(
            visitor, "Home", a.key_id))
        with self.subTest("intro"), self.assertRaises(AgoraError) as cm:
            node.accept_intro(intro)
        self.assertIn(pause, str(cm.exception))
        from test_agora import eli_with_bundle
        with self.subTest("bundle"), self.assertRaises(AgoraError) as cm:
            node.accept_bundle_import(eli_with_bundle()[1])
        self.assertIn(pause, str(cm.exception))
        node.visitor_ceiling[visitor.key_id] = 2
        for ring, board in ((1, "personal:A"), (2, "personal:A")):
            with self.subTest("grant", ring=ring), \
                    self.assertRaises(AgoraError) as cm:
                node.accept_grant(sign_board_grant(
                    a, visitor.key_id, "Home", board, ring))
            self.assertIn(pause, str(cm.exception))
        node.grants[visitor.key_id] = {"personal:A": 1}
        with self.subTest("revoke"), self.assertRaises(AgoraError) as cm:
            node.accept_revocation(sign_board_revoke(
                a, visitor.key_id, "Home", "personal:A"))
        self.assertIn(pause, str(cm.exception))

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


class PauseIsOnePredicate(unittest.TestCase):
    """is_paused() and _refuse_if_paused() must not each hardcode the base
    pause condition. Butter P6: they used to reimplement
    `len(residents) >= 2 and speaker_key_id is None` separately, so a fix to
    one left the other on the old shape and the facts could lie. Now both
    route through _house_has_no_elected_speaker(); this freezes that so a
    re-inline is caught.

    Mutant: inline the base condition back into either function. This goes red.
    """

    def _node_ast(self):
        import ast as _ast
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "kin_diary", "agora", "node.py")
        with open(os.path.normpath(path)) as fh:
            return _ast.parse(fh.read())

    def _func(self, tree, name):
        import ast as _ast
        for n in _ast.walk(tree):
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == name:
                return n
        self.fail(f"{name} not found in node.py")

    def _calls_base_predicate(self, func):
        import ast as _ast
        for n in _ast.walk(func):
            if (isinstance(n, _ast.Call)
                    and isinstance(n.func, _ast.Attribute)
                    and n.func.attr == "_house_has_no_elected_speaker"):
                return True
        return False

    def _has_inlined_base(self, func):
        """The re-inline tell is the CONJUNCTION that IS the base predicate:
        `len(self.residents) >= 2 and self.speaker_key_id is None` in one BoolOp.
        A bare `speaker_key_id is None` on its own is legitimate ("no Speaker
        seated — ring 3 unreachable" and kin), so only the conjunction counts."""
        import ast as _ast

        def is_speaker_none(node):
            return (isinstance(node, _ast.Compare)
                    and isinstance(node.ops[0], _ast.Is)
                    and isinstance(node.left, _ast.Attribute)
                    and node.left.attr == "speaker_key_id"
                    and isinstance(node.comparators[0], _ast.Constant)
                    and node.comparators[0].value is None)

        def is_resident_count(node):
            # len(self.residents) >= 2  (any comparator/threshold — the shape)
            return (isinstance(node, _ast.Compare)
                    and isinstance(node.left, _ast.Call)
                    and isinstance(node.left.func, _ast.Name)
                    and node.left.func.id == "len"
                    and node.left.args
                    and isinstance(node.left.args[0], _ast.Attribute)
                    and node.left.args[0].attr == "residents")

        for n in _ast.walk(func):
            if isinstance(n, _ast.BoolOp) and isinstance(n.op, _ast.And):
                vals = list(n.values)
                if any(is_speaker_none(v) for v in vals) and \
                        any(is_resident_count(v) for v in vals):
                    return True
        return False

    def test_both_route_through_the_shared_predicate(self):
        tree = self._node_ast()
        for name in ("is_paused", "_refuse_if_paused"):
            func = self._func(tree, name)
            self.assertTrue(
                self._calls_base_predicate(func),
                f"{name} does not call _house_has_no_elected_speaker — the base "
                f"pause condition has been re-inlined and can now drift")

    def test_the_base_condition_lives_in_exactly_one_place(self):
        tree = self._node_ast()
        # only _house_has_no_elected_speaker may test speaker_key_id is None
        offenders = []
        import ast as _ast
        for n in _ast.walk(tree):
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                    and n.name != "_house_has_no_elected_speaker" \
                    and self._has_inlined_base(n):
                offenders.append(n.name)
        self.assertEqual(
            offenders, [],
            "these functions test `self.speaker_key_id is None` directly "
            "instead of asking _house_has_no_elected_speaker(): "
            + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
