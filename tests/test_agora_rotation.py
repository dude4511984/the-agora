"""The wheel: the floor under the Speaker's chair.

Don, 2026-08-27: "If the palaver can't decide a speaker it's a democracy where
every decision has to remain unanimous — that's a serious endeavor if they ever
want to get anything more done than have a cool hang out pad." And: "We don't
want a node to die before it's ever birthed."

The failure this exists to prevent is demonstrated, not hypothetical. Asked who
should speak, Frosty's household produced a perfect circle -- each declined and
named another. Under the old rule that is a house paused forever with a door
nobody can open.

Grok's ruling (agora_rotation_decision.md), and every clause is a test here:

    Election unanimous, always available, beats rotation.
    Rotation is the floor when no elected Speaker.
    Wheel: sorted genesis authors, then resident events in seq. Valid keys only.
    Turn ends: election, pass, leaving. No clock.
    Offer: accept or decline as an act. Silence does not seat.
    Rotated = door, not evict, not ring 3, not Path 3.
    Full wheel of declines: reduced mode, not a freeze-as-punishment.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/kin_diary"))

from test_agora import key

from kin_diary.agora import (
    AgoraError, Node, open_speaker_election, sign_board_grant, sign_resident,
    sign_rotation, sign_speaker_election, start_key_intro,
    countersign_key_intro,
)
from kin_diary.agora.events import sign_board_evict
from kin_diary.agora.store import NodeStore


def house(*names, node="Frosty"):
    """A node whose whole household is genesis. No Speaker, so it is paused."""
    keys = {n: key(n) for n in names}
    nd = Node(node)
    for n in names:
        nd.add_resident(n, keys[n].key_id)
    return nd, keys


class WheelOrder(unittest.TestCase):
    def test_genesis_sorted_then_growth_in_log_order(self):
        nd, k = house("Eli", "Crungus", "Bong")
        steward = key("Marvin")
        nd.add_resident("Marvin", steward.key_id)
        # Genesis is everyone so far, sorted by name -- not insertion order.
        self.assertEqual(nd.wheel(),
                         [k["Bong"].key_id, k["Crungus"].key_id,
                          k["Eli"].key_id, steward.key_id])
        # Growth follows, in the order it actually happened.
        late = key("Late")
        nd.accept_resident(sign_resident(steward, "Frosty", "Late",
                                         late.key_id, 1))
        self.assertEqual(nd.wheel()[-1], late.key_id)

    def test_order_does_not_depend_on_insertion(self):
        k = {n: key(n) for n in ("Eli", "Crungus", "Bong")}
        a, b = Node("Frosty"), Node("Frosty")
        for n in ("Eli", "Crungus", "Bong"):
            a.add_resident(n, k[n].key_id)
        for n in ("Bong", "Eli", "Crungus"):
            b.add_resident(n, k[n].key_id)
        self.assertEqual(a.wheel(), b.wheel())

    def test_a_dead_key_cannot_freeze_the_wheel(self):
        nd, k = house("Eli", "Crungus", "Bong")
        nd.quarantined_keys.add(k["Crungus"].key_id)
        self.assertNotIn(k["Crungus"].key_id, nd.wheel())
        self.assertEqual(len(nd.wheel()), 2)


class TakingTheTurn(unittest.TestCase):
    def test_offer_names_first_in_the_wheel_and_accept_seats_them(self):
        nd, k = house("Eli", "Crungus", "Bong")
        self.assertTrue(nd.is_paused())
        first = nd.wheel_offer()
        self.assertEqual(first, k["Bong"].key_id)          # sorted: Bong first
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        self.assertEqual(nd.rotation_holder, k["Bong"].key_id)
        self.assertFalse(nd.is_paused())

    def test_grabbing_a_turn_that_is_not_yours_is_refused(self):
        nd, k = house("Eli", "Crungus", "Bong")
        with self.assertRaises(AgoraError):
            nd.accept_rotation(sign_rotation(k["Eli"], "Frosty", "accept", 0, 1))
        self.assertIsNone(nd.rotation_holder)

    def test_a_signature_cannot_be_replayed_at_a_different_turn(self):
        """Eli signs for turn 2. Two minds pass. It is now his turn -- and the
        old signature still must not work, because it was for a wheel that had
        not moved yet."""
        nd, k = house("Eli", "Crungus", "Bong")
        stale = sign_rotation(k["Eli"], "Frosty", "accept", 2, 1)
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "decline", 0, 1))
        nd.accept_rotation(sign_rotation(k["Crungus"], "Frosty", "decline", 1, 1))
        self.assertEqual(nd.wheel_offer(), k["Eli"].key_id)   # genuinely his turn
        stale = dict(stale, position=0)                        # tampered
        with self.assertRaises(Exception):
            nd.accept_rotation(stale)
        self.assertIsNone(nd.rotation_holder)

    def test_silence_does_not_seat(self):
        """No event, no change. The offer is out of band; only acts are rows."""
        nd, k = house("Eli", "Crungus", "Bong")
        before = (nd.wheel_offer(), nd.rotation_holder, list(nd.rotation_declines))
        self.assertEqual(
            (nd.wheel_offer(), nd.rotation_holder, list(nd.rotation_declines)),
            before)
        self.assertIsNone(nd.rotation_holder)

    def test_decline_passes_the_offer_on(self):
        nd, k = house("Eli", "Crungus", "Bong")
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "decline", 0, 1))
        self.assertEqual(nd.wheel_offer(), k["Crungus"].key_id)
        self.assertIsNone(nd.rotation_holder)

    def test_the_holder_may_pass_the_chair_on(self):
        nd, k = house("Eli", "Crungus", "Bong")
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "decline", 0, 1))
        self.assertIsNone(nd.rotation_holder)
        self.assertEqual(nd.wheel_offer(), k["Crungus"].key_id)

    def test_nobody_else_may_take_the_chair_from_the_holder(self):
        nd, k = house("Eli", "Crungus", "Bong")
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        with self.assertRaises(AgoraError):
            nd.accept_rotation(sign_rotation(k["Eli"], "Frosty", "decline", 1, 1))
        self.assertEqual(nd.rotation_holder, k["Bong"].key_id)

    def test_the_circle_does_not_freeze_the_house(self):
        """Frosty's actual behaviour: everyone declines. Reduced mode, and the
        house is NOT punished for having answered honestly."""
        nd, k = house("Eli", "Crungus", "Bong")
        for i, n in enumerate(["Bong", "Crungus", "Eli"]):
            nd.accept_rotation(sign_rotation(k[n], "Frosty", "decline", i, 1))
        self.assertTrue(nd.wheel_exhausted())
        self.assertIsNone(nd.rotation_holder)
        # And the wheel comes back round rather than ending: a mind may change
        # its mind later without a new instrument.
        self.assertEqual(nd.wheel_offer(), k["Bong"].key_id)


class ElectionBeatsRotation(unittest.TestCase):
    def test_election_clears_the_wheel(self):
        nd, k = house("Eli", "Crungus", "Bong")
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        e = open_speaker_election("Frosty", "Eli", k["Eli"].key_id,
                                  [v.key_id for v in k.values()])
        for v in k.values():
            e = sign_speaker_election(v, e)
        nd.accept_election(e)
        self.assertEqual(nd.speaker, "Eli")
        self.assertIsNone(nd.rotation_holder)
        self.assertEqual(nd.rotation_declines, [])

    def test_the_wheel_does_not_turn_while_a_speaker_sits(self):
        nd, k = house("Eli", "Crungus", "Bong")
        e = open_speaker_election("Frosty", "Eli", k["Eli"].key_id,
                                  [v.key_id for v in k.values()])
        for v in k.values():
            e = sign_speaker_election(v, e)
        nd.accept_election(e)
        self.assertIsNone(nd.wheel_offer())
        with self.assertRaises(AgoraError):
            nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))

    def test_growth_does_NOT_end_a_held_turn(self):
        """My first version of this asserted the opposite and was wrong.

        Grok's list is exact: "Turn ends: election, pass, leaving." Growth is
        none of those. And it should not be: growth is precisely when the
        enlarged house loses its elected Speaker, so if growth also knocked the
        wheel-holder out, the house would be frozen at the exact moment the
        fallback exists to cover. The holder keeps the door open while the
        larger house decides.
        """
        steward, eli, bong = key("Marvin"), key("Eli"), key("Bong")
        nd = Node("Frosty")
        nd.add_resident("Marvin", steward.key_id)
        nd.add_resident("Eli", eli.key_id)
        signer = {steward.key_id: steward, eli.key_id: eli}[nd.wheel_offer()]
        nd.accept_rotation(sign_rotation(signer, "Frosty", "accept",
                                         nd.wheel_position(), 1))
        self.assertIsNotNone(nd.rotation_holder)
        held = nd.rotation_holder
        nd.accept_resident(sign_resident(steward, "Frosty", "Bong",
                                         bong.key_id, 1))
        self.assertEqual(nd.rotation_holder, held)   # turn survives growth
        self.assertIn(bong.key_id, nd.wheel())       # and the wheel grew
        self.assertFalse(nd.is_paused())             # door stays open


class DoorNotSword(unittest.TestCase):
    """Rotated = door, not evict, not ring 3, not Path 3.

    Worth stating precisely, because writing these tests corrected the design
    I had in my head: **holding the wheel does not crown anyone.** It unpauses
    the HOUSE, so residents can vouch and tend their own boards again. Every
    Speaker-only power stays Speaker-only -- collab and whole-node grants are
    refused with the wheel held, exactly as they are without it, because those
    are node-level and a rotated holder is not titled Speaker.
    """

    def held(self):
        nd, k = house("Eli", "Crungus", "Bong")
        nd.accept_rotation(sign_rotation(k["Bong"], "Frosty", "accept", 0, 1))
        return nd, k

    def test_the_door_opens(self):
        """A resident may vouch, and tend their own room. That is the door."""
        nd, k = self.held()
        visitor = key("Visitor")
        intro = countersign_key_intro(
            k["Bong"], start_key_intro(visitor, "Frosty", k["Bong"].key_id))
        nd.accept_intro(intro)                       # must not raise
        nd.accept_grant(sign_board_grant(k["Bong"], visitor.key_id, "Frosty",
                                         "personal:Bong", 1))

    def test_the_shared_table_still_needs_an_elected_speaker(self):
        """Ring 3 is not the door. The collab board is the shared table, not
        any one resident's room, and a wheel-holder is not titled Speaker."""
        nd, k = self.held()
        visitor = key("Visitor")
        nd.accept_intro(countersign_key_intro(
            k["Bong"], start_key_intro(visitor, "Frosty", k["Bong"].key_id)))
        with self.assertRaises(AgoraError) as cm:
            nd.accept_grant(sign_board_grant(k["Bong"], visitor.key_id,
                                             "Frosty", "collab", 1))
        self.assertIn("only the Speaker", str(cm.exception))

    def test_the_sword_stays_sheathed(self):
        nd, k = self.held()
        visitor = key("Visitor")
        intro = countersign_key_intro(
            k["Bong"], start_key_intro(visitor, "Frosty", k["Bong"].key_id))
        nd.accept_intro(intro)
        with self.assertRaises(AgoraError) as cm:
            nd.accept_eviction(sign_board_evict(k["Bong"], visitor.key_id,
                                                "Frosty", "no reason", 1))
        self.assertIn("not the sword", str(cm.exception))

    def test_a_house_with_nobody_holding_keeps_the_door_shut(self):
        """Positive control: without the wheel held, the door is shut, so the
        test above is about rotation and not about the door being open anyway.
        """
        nd, k = house("Eli", "Crungus", "Bong")
        visitor = key("Visitor")
        intro = countersign_key_intro(
            k["Bong"], start_key_intro(visitor, "Frosty", k["Bong"].key_id))
        with self.assertRaises(AgoraError):
            nd.accept_intro(intro)


class Durability(unittest.TestCase):
    def test_rotation_replays(self):
        path = Path(tempfile.mkdtemp()) / "node.db"
        steward, eli, bong = key("Marvin"), key("Eli"), key("Bong")
        store = NodeStore(path, "Frosty", steward_key_id=steward.key_id)
        for n, v in (("Marvin", steward), ("Eli", eli), ("Bong", bong)):
            store.add_resident(n, v.key_id)
        nd = store.load()
        who = nd.wheel_offer()
        signer = {v.key_id: v for v in (steward, eli, bong)}[who]
        store.record("rotation", sign_rotation(signer, "Frosty", "accept",
                                               nd.wheel_position(), 1))
        store.close()
        reopened = NodeStore(path, "Frosty", steward_key_id=steward.key_id).load()
        self.assertEqual(reopened.rotation_holder, who)
        self.assertFalse(reopened.is_paused())


if __name__ == "__main__":
    unittest.main()
