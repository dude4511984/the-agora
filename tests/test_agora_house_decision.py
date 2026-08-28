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
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/kin_diary"))

from test_agora import key

from kin_diary.agora import (
    AgoraError, Node, countersign_key_intro, open_house_decision,
    open_speaker_election, sign_house_decision, sign_speaker_election,
    start_key_intro,
)


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


if __name__ == "__main__":
    unittest.main()
