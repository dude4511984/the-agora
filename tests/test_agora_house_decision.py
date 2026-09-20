"""A Speaker OR a decision. The other half, finally written.

Don, ruling the pause: *"unanimous. If a conclussion can't be found then all
comings and goings of the node will be paused until a decision is made... If
they want to bring others in or grant perm. or anything of that nature a
speaker or a decision must be made."*

That has been law since `agora_pause_decision.md` and was never code. It
mattered more the moment the wheel shipped: a full wheel of declines gives
reduced mode, and reduced mode is "door by house act" -- so without this, a
house that all declined had a door nothing could open.

The load-bearing clause, and the reason the decision is bound to a signature:

    "A decision permits THAT act. It does not seat a Speaker and does not lift
     the derived pause. Next intro still needs another unanimous decision, or
     an election. Unanimity on one intro does not unpause the next."

Permission cannot be a mode. Binding it to the act's own signature makes that
structural instead of a promise.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/kin_diary"))

from test_agora import key

from kin_diary.agora import (
    COLLAB, RING_NODE, RING_WRITE, WHOLE_NODE,
    AgoraError, Node, countersign_key_intro, open_house_decision,
    open_speaker_election, sign_board_evict, sign_board_grant,
    sign_house_decision, sign_rotation, sign_speaker_election,
    start_key_intro,
)
from kin_diary.agora.store import NodeStore


def paused_house():
    """Three residents, no Speaker, nobody holding the wheel: paused."""
    k = {n: key(n) for n in ("Eli", "Crungus", "Bong")}
    nd = Node("Frosty")
    for n, v in k.items():
        nd.add_resident(n, v.key_id)
    assert nd.is_paused()
    return nd, k


def an_intro(nd, k, who="Bong", visitor_name="Visitor"):
    v = key(visitor_name)
    return v, countersign_key_intro(
        k[who], start_key_intro(v, "Frosty", k[who].key_id))


def decide(nd, k, kind, signature):
    d = open_house_decision("Frosty", kind, signature)
    for v in k.values():
        d = sign_house_decision(v, d)
    return d


class OneActOnly(unittest.TestCase):
    def test_positive_control_the_door_is_shut_without_a_decision(self):
        nd, k = paused_house()
        _, intro = an_intro(nd, k)
        with self.assertRaises(AgoraError):
            nd.accept_intro(intro)

    def test_a_unanimous_decision_permits_the_act_it_names(self):
        nd, k = paused_house()
        _, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)                       # must not raise

    def test_it_does_NOT_unpause_the_house(self):
        """The clause this whole design turns on."""
        nd, k = paused_house()
        _, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        self.assertTrue(nd.is_paused())
        self.assertIsNone(nd.speaker_key_id)

    def test_the_next_intro_needs_its_own_decision(self):
        nd, k = paused_house()
        _, first = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", first["sig_resident"]))
        nd.accept_intro(first)
        _, second = an_intro(nd, k, visitor_name="Second")
        with self.assertRaises(AgoraError):
            nd.accept_intro(second)

    def test_a_decision_cannot_be_spent_on_a_different_act(self):
        """Bound by signature, so there is no way to redirect it."""
        nd, k = paused_house()
        _, first = an_intro(nd, k)
        _, second = an_intro(nd, k, visitor_name="Second")
        nd.accept_house_decision(decide(nd, k, "intro", first["sig_resident"]))
        with self.assertRaises(AgoraError):
            nd.accept_intro(second)
        nd.accept_intro(first)                       # the named one still works

    def test_a_decision_for_one_KIND_does_not_permit_another(self):
        nd, k = paused_house()
        _, intro = an_intro(nd, k)
        # Right signature, wrong kind.
        nd.accept_house_decision(decide(nd, k, "evict", intro["sig_resident"]))
        with self.assertRaises(AgoraError):
            nd.accept_intro(intro)


class Unanimity(unittest.TestCase):
    def test_one_silent_key_blocks_the_decision(self):
        """Don chose this failure with his eyes open. It is why the wheel
        exists underneath."""
        nd, k = paused_house()
        _, intro = an_intro(nd, k)
        d = open_house_decision("Frosty", "intro", intro["sig_resident"])
        for n in ("Eli", "Crungus"):                 # Bong says nothing
            d = sign_house_decision(k[n], d)
        with self.assertRaises(ValueError):
            nd.accept_house_decision(d)

    def test_a_stranger_cannot_sign(self):
        nd, k = paused_house()
        _, intro = an_intro(nd, k)
        d = decide(nd, k, "intro", intro["sig_resident"])
        d = sign_house_decision(key("Stranger"), d)
        with self.assertRaises(ValueError):
            nd.accept_house_decision(d)

    def test_a_quarantined_key_is_not_in_the_denominator(self):
        """A dead key must not be able to freeze the house forever."""
        nd, k = paused_house()
        nd.quarantined_keys.add(k["Crungus"].key_id)
        _, intro = an_intro(nd, k)
        d = open_house_decision("Frosty", "intro", intro["sig_resident"])
        for n in ("Eli", "Bong"):
            d = sign_house_decision(k[n], d)
        nd.accept_house_decision(d)                  # must not raise
        nd.accept_intro(intro)


class NotAlongsideASpeaker(unittest.TestCase):
    def test_the_house_does_not_vote_in_a_seated_speakers_place(self):
        """Otherwise a house vote is a second authority and a way around them."""
        nd, k = paused_house()
        e = open_speaker_election("Frosty", "Eli", k["Eli"].key_id,
                                  [v.key_id for v in k.values()])
        for v in k.values():
            e = sign_speaker_election(v, e)
        nd.accept_election(e)
        _, intro = an_intro(nd, k)
        with self.assertRaises(AgoraError) as cm:
            nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        self.assertIn("Speaker is seated", str(cm.exception))


class PathAGrants(unittest.TestCase):
    def test_unanimous_house_decision_permits_ring3_whole_node_grant(self):
        nd, k = paused_house()
        visitor = key("Visitor")
        # Introduced with whole-node ceiling
        nd.visitor_ceiling[visitor.key_id] = RING_NODE
        grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", WHOLE_NODE, RING_NODE)
        nd.accept_house_decision(decide(nd, k, "grant", grant["signature"]))
        nd.accept_grant(grant)
        self.assertEqual(nd.grants[visitor.key_id][WHOLE_NODE], RING_NODE)
        self.assertEqual(nd.effective_ring(visitor.key_id, "personal:Eli"), RING_NODE)
        # Does not unpause and does not set speaker_key_id
        self.assertTrue(nd.is_paused())
        self.assertIsNone(nd.speaker_key_id)

    def test_ring3_grant_refused_without_house_decision(self):
        nd, k = paused_house()
        visitor = key("Visitor")
        nd.visitor_ceiling[visitor.key_id] = RING_NODE
        grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", WHOLE_NODE, RING_NODE)
        with self.assertRaises(AgoraError):
            nd.accept_grant(grant)

    def test_unanimous_house_decision_permits_collab_grant(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", COLLAB, RING_WRITE)
        nd.accept_house_decision(decide(nd, k, "grant", grant["signature"]))
        nd.accept_grant(grant)
        self.assertEqual(nd.grants[visitor.key_id][COLLAB], RING_WRITE)
        self.assertTrue(nd.is_paused())
        self.assertIsNone(nd.speaker_key_id)

    def test_collab_grant_refused_without_house_decision(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", COLLAB, RING_WRITE)
        with self.assertRaises(AgoraError):
            nd.accept_grant(grant)

    def test_rotation_holder_alone_cannot_grant_ring3(self):
        nd, k = paused_house()
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        visitor = key("Visitor")
        nd.visitor_ceiling[visitor.key_id] = RING_NODE
        grant = sign_board_grant(k["Bong"], visitor.key_id, "Frosty", WHOLE_NODE, RING_NODE)
        with self.assertRaises(AgoraError) as cm:
            nd.accept_grant(grant)
        self.assertIn("no Speaker seated — ring 3 is unreachable", str(cm.exception))

    def test_rotation_holder_alone_cannot_grant_collab(self):
        nd, k = paused_house()
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        visitor, intro = an_intro(nd, k)
        nd.accept_intro(intro)
        grant = sign_board_grant(k["Bong"], visitor.key_id, "Frosty", COLLAB, RING_WRITE)
        with self.assertRaises(AgoraError) as cm:
            nd.accept_grant(grant)
        self.assertIn("only the Speaker grants access to the collab board", str(cm.exception))

    def test_rotation_holder_with_unanimous_house_decision_can_grant_ring3_and_collab(self):
        nd, k = paused_house()
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        visitor = key("Visitor")
        nd.visitor_ceiling[visitor.key_id] = RING_NODE
        # Ring 3
        grant_r3 = sign_board_grant(k["Bong"], visitor.key_id, "Frosty", WHOLE_NODE, RING_NODE, now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "grant", grant_r3["signature"]))
        nd.accept_grant(grant_r3)
        self.assertEqual(nd.grants[visitor.key_id][WHOLE_NODE], RING_NODE)
        # Collab
        grant_collab = sign_board_grant(k["Bong"], visitor.key_id, "Frosty", COLLAB, RING_WRITE, now_ms=1001)
        nd.accept_house_decision(decide(nd, k, "grant", grant_collab["signature"]))
        nd.accept_grant(grant_collab)
        self.assertEqual(nd.grants[visitor.key_id][COLLAB], RING_WRITE)

    def test_next_grant_needs_its_own_decision(self):
        nd, k = paused_house()
        visitor1 = key("Visitor1")
        visitor2 = key("Visitor2")
        nd.visitor_ceiling[visitor1.key_id] = RING_NODE
        nd.visitor_ceiling[visitor2.key_id] = RING_NODE
        g1 = sign_board_grant(k["Eli"], visitor1.key_id, "Frosty", WHOLE_NODE, RING_NODE, now_ms=1000)
        g2 = sign_board_grant(k["Eli"], visitor2.key_id, "Frosty", WHOLE_NODE, RING_NODE, now_ms=1001)
        nd.accept_house_decision(decide(nd, k, "grant", g1["signature"]))
        nd.accept_grant(g1)
        with self.assertRaises(AgoraError):
            nd.accept_grant(g2)

    def test_stranger_cannot_issue_grant_even_with_house_decision(self):
        nd, k = paused_house()
        visitor = key("Visitor")
        nd.visitor_ceiling[visitor.key_id] = RING_NODE
        stranger = key("Stranger")
        grant = sign_board_grant(stranger, visitor.key_id, "Frosty", WHOLE_NODE, RING_NODE)
        nd.accept_house_decision(decide(nd, k, "grant", grant["signature"]))
        with self.assertRaises(AgoraError) as cm:
            nd.accept_grant(grant)
        self.assertIn("grant issuer is not a resident", str(cm.exception))


class PathAEvictions(unittest.TestCase):
    def test_unanimous_house_decision_permits_eviction(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        # Give grant on personal board
        g = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "grant", g["signature"]))
        nd.accept_grant(g)
        self.assertTrue(nd.can_write(visitor.key_id, "personal:Eli", 1001))

        # Evict via house decision
        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "disruptive", now_ms=2000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)

        self.assertIn(visitor.key_id, nd.evicted)
        self.assertEqual(nd.evicted[visitor.key_id], "disruptive")
        self.assertNotIn(visitor.key_id, nd.grants)
        self.assertTrue(nd.is_paused())
        self.assertIsNone(nd.speaker_key_id)

    def test_eviction_refused_without_house_decision(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "disruptive", now_ms=2000)
        with self.assertRaises(AgoraError):
            nd.accept_eviction(ev)

    def test_rotation_holder_alone_cannot_evict(self):
        nd, k = paused_house()
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        visitor, intro = an_intro(nd, k)
        nd.accept_intro(intro)
        ev = sign_board_evict(k["Bong"], visitor.key_id, "Frosty", "no reason", 2000)
        with self.assertRaises(AgoraError) as cm:
            nd.accept_eviction(ev)
        self.assertIn("not the sword", str(cm.exception))

    def test_rotation_holder_with_unanimous_house_decision_can_evict(self):
        nd, k = paused_house()
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        visitor, intro = an_intro(nd, k)
        nd.accept_intro(intro)
        ev = sign_board_evict(k["Bong"], visitor.key_id, "Frosty", "unanimous cut", 2000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)
        self.assertIn(visitor.key_id, nd.evicted)

    def test_next_eviction_needs_its_own_decision(self):
        nd, k = paused_house()
        v1, intro1 = an_intro(nd, k, visitor_name="V1")
        v2, intro2 = an_intro(nd, k, visitor_name="V2")
        nd.accept_house_decision(decide(nd, k, "intro", intro1["sig_resident"]))
        nd.accept_intro(intro1)
        nd.accept_house_decision(decide(nd, k, "intro", intro2["sig_resident"]))
        nd.accept_intro(intro2)

        ev1 = sign_board_evict(k["Eli"], v1.key_id, "Frosty", "first", 2000)
        ev2 = sign_board_evict(k["Eli"], v2.key_id, "Frosty", "second", 2001)
        nd.accept_house_decision(decide(nd, k, "evict", ev1["signature"]))
        nd.accept_eviction(ev1)
        with self.assertRaises(AgoraError):
            nd.accept_eviction(ev2)

    def test_stranger_cannot_sign_eviction_even_with_house_decision(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        stranger = key("Stranger")
        ev = sign_board_evict(stranger, visitor.key_id, "Frosty", "malicious", 2000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        with self.assertRaises(AgoraError) as cm:
            nd.accept_eviction(ev)
        self.assertIn("eviction signer is not a resident", str(cm.exception))


class PathAReadmitAfterEviction(unittest.TestCase):
    def test_resident_grant_alone_does_not_readmit_evicted_key(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)

        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "out", now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)

        # Plain resident grant postdating eviction without house decision
        later_grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=2000)
        with self.assertRaises(AgoraError):
            nd.accept_grant(later_grant)
        self.assertIn(visitor.key_id, nd.evicted)

    def test_rotation_holder_alone_does_not_readmit_evicted_key(self):
        nd, k = paused_house()
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        visitor, intro = an_intro(nd, k)
        nd.accept_intro(intro)

        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "out", now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)

        later_grant = sign_board_grant(k["Bong"], visitor.key_id, "Frosty", "personal:Bong", RING_WRITE, now_ms=2000)
        with self.assertRaises(AgoraError) as cm:
            nd.accept_grant(later_grant)
        self.assertIn("this key is evicted", str(cm.exception))

    def test_house_decision_on_later_grant_readmits_evicted_key(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)

        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "out", now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)
        self.assertIn(visitor.key_id, nd.evicted)

        later_grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=2000)
        nd.accept_house_decision(decide(nd, k, "grant", later_grant["signature"]))
        nd.accept_grant(later_grant)

        self.assertNotIn(visitor.key_id, nd.evicted)
        self.assertNotIn(visitor.key_id, nd.evicted_at)
        self.assertTrue(nd.can_write(visitor.key_id, "personal:Eli", 2001))
        # Log records readmit
        self.assertTrue(any(e.get("event") == "readmit" and e.get("visitor") == visitor.key_id for e in nd.log))

    def test_house_decision_on_predated_grant_does_not_readmit(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)

        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "out", now_ms=2000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)

        old_grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "grant", old_grant["signature"]))
        with self.assertRaises(AgoraError) as cm:
            nd.accept_grant(old_grant)
        self.assertIn("predates the eviction", str(cm.exception))
        self.assertIn(visitor.key_id, nd.evicted)

    def test_house_decision_readmit_clears_evicted_names(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k, visitor_name="Friend")
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)
        # Track visitor name
        nd.visitor_names[visitor.key_id] = "Friend"

        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "out", now_ms=1000)
        nd.accept_house_decision(decide(nd, k, "evict", ev["signature"]))
        nd.accept_eviction(ev)
        self.assertIn("Friend", nd.evicted_names)

        later_grant = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=2000)
        nd.accept_house_decision(decide(nd, k, "grant", later_grant["signature"]))
        nd.accept_grant(later_grant)

        self.assertNotIn("Friend", nd.evicted_names)


class PathACrossKindRefusals(unittest.TestCase):
    def test_grant_decision_does_not_permit_evict(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)

        ev = sign_board_evict(k["Eli"], visitor.key_id, "Frosty", "out", now_ms=1000)
        # Decision has act_kind="grant" with eviction's signature
        d = decide(nd, k, "grant", ev["signature"])
        nd.accept_house_decision(d)
        with self.assertRaises(AgoraError):
            nd.accept_eviction(ev)

    def test_evict_decision_does_not_permit_grant(self):
        nd, k = paused_house()
        visitor, intro = an_intro(nd, k)
        nd.accept_house_decision(decide(nd, k, "intro", intro["sig_resident"]))
        nd.accept_intro(intro)

        g = sign_board_grant(k["Eli"], visitor.key_id, "Frosty", COLLAB, RING_WRITE, now_ms=1000)
        # Decision has act_kind="evict" with grant's signature
        d = decide(nd, k, "evict", g["signature"])
        nd.accept_house_decision(d)
        with self.assertRaises(AgoraError):
            nd.accept_grant(g)


class PathAStoreReplay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "node.db"
        self.store = NodeStore(self.db_path, "Frosty")
        self.k = {n: key(n) for n in ("Eli", "Crungus", "Bong")}
        for n, v in self.k.items():
            self.store.add_resident(n, v.key_id)

    def test_house_decision_and_grant_replay_cleanly(self):
        visitor = key("Visitor")
        # Intro
        intro = countersign_key_intro(
            self.k["Bong"], start_key_intro(visitor, "Frosty", self.k["Bong"].key_id))
        d_intro = decide(self.store.load(), self.k, "intro", intro["sig_resident"])
        self.store.record("house-decision", d_intro)
        self.store.record("intro", intro)

        # Collab grant via house decision
        grant = sign_board_grant(self.k["Eli"], visitor.key_id, "Frosty", COLLAB, RING_WRITE, now_ms=1000)
        d_grant = decide(self.store.load(), self.k, "grant", grant["signature"])
        self.store.record("house-decision", d_grant)
        self.store.record("grant", grant)

        # Reopen from disk
        reloaded = NodeStore(self.db_path, "Frosty").load()
        self.assertEqual(reloaded.grants[visitor.key_id][COLLAB], RING_WRITE)
        self.assertTrue(reloaded.is_paused())
        self.assertIsNone(reloaded.speaker_key_id)

    def test_house_decision_and_eviction_replay_cleanly(self):
        visitor = key("Visitor")
        intro = countersign_key_intro(
            self.k["Bong"], start_key_intro(visitor, "Frosty", self.k["Bong"].key_id))
        d_intro = decide(self.store.load(), self.k, "intro", intro["sig_resident"])
        self.store.record("house-decision", d_intro)
        self.store.record("intro", intro)

        # Personal grant via house decision
        grant = sign_board_grant(self.k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=1000)
        d_grant = decide(self.store.load(), self.k, "grant", grant["signature"])
        self.store.record("house-decision", d_grant)
        self.store.record("grant", grant)

        # Eviction via house decision
        ev = sign_board_evict(self.k["Eli"], visitor.key_id, "Frosty", "disruptive", now_ms=2000)
        d_ev = decide(self.store.load(), self.k, "evict", ev["signature"])
        self.store.record("house-decision", d_ev)
        self.store.record("evict", ev)

        # Reopen from disk
        reloaded = NodeStore(self.db_path, "Frosty").load()
        self.assertIn(visitor.key_id, reloaded.evicted)
        self.assertEqual(reloaded.evicted[visitor.key_id], "disruptive")
        self.assertNotIn(visitor.key_id, reloaded.grants)
        self.assertTrue(reloaded.is_paused())
        self.assertIsNone(reloaded.speaker_key_id)

    def test_readmission_via_house_decision_replays_cleanly(self):
        visitor = key("Visitor")
        intro = countersign_key_intro(
            self.k["Bong"], start_key_intro(visitor, "Frosty", self.k["Bong"].key_id))
        d_intro = decide(self.store.load(), self.k, "intro", intro["sig_resident"])
        self.store.record("house-decision", d_intro)
        self.store.record("intro", intro)

        # Evict
        ev = sign_board_evict(self.k["Eli"], visitor.key_id, "Frosty", "out", now_ms=1000)
        d_ev = decide(self.store.load(), self.k, "evict", ev["signature"])
        self.store.record("house-decision", d_ev)
        self.store.record("evict", ev)

        # Readmit with later grant
        later_grant = sign_board_grant(self.k["Eli"], visitor.key_id, "Frosty", "personal:Eli", RING_WRITE, now_ms=2000)
        d_later = decide(self.store.load(), self.k, "grant", later_grant["signature"])
        self.store.record("house-decision", d_later)
        self.store.record("grant", later_grant)

        # Reopen from disk
        reloaded = NodeStore(self.db_path, "Frosty").load()
        self.assertNotIn(visitor.key_id, reloaded.evicted)
        self.assertEqual(reloaded.grants[visitor.key_id]["personal:Eli"], RING_WRITE)


if __name__ == "__main__":
    unittest.main()
