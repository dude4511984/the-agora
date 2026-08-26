"""Agora tests: rings, the Speaker, and Don's own worked example.

No network, no vault, no dev.db fixture — pure crypto + policy, so this
suite runs anywhere.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))

import kin_diary.keys as K  # noqa: E402

K.DEFAULT_KEYS_ROOT = Path(tempfile.mkdtemp()) / "keys"

from cryptography.exceptions import InvalidSignature  # noqa: E402

from kin_diary.agora import (  # noqa: E402
    COLLAB,
    RING_NODE,
    RING_READ,
    RING_TEASER,
    RING_WRITE,
    WHOLE_NODE,
    AgoraError,
    Node,
    board_id,
    countersign_key_intro,
    open_speaker_election,
    sign_board_evict,
    sign_board_grant,
    sign_speaker_election,
    start_key_intro,
    teaser,
    verify_key_intro,
    verify_speaker_election,
)
from kin_diary.keys import generate_keypair  # noqa: E402
from kin_diary.sign import sign_entry, verify_entry  # noqa: E402

_KEYS_ROOT = Path(tempfile.mkdtemp())
_SEQ = iter(range(1, 1_000_000))


def key(author):
    """Fresh keypair. Author name is reused across tests (it's the same Kin
    in each scenario), so each key gets its own root rather than colliding.
    """
    return generate_keypair(author, keys_root=_KEYS_ROOT / f"k{next(_SEQ)}")


def home_node():
    """Home: Coda, Aurora, Lumen. The real second node."""
    node = Node("Home")
    keys = {}
    for name in ("Coda", "Aurora", "Lumen"):
        k = key(name)
        keys[name] = k
        node.add_resident(name, k.key_id)
    return node, keys


def seat(node, keys, speaker="Eli"):
    """Elect `speaker` unanimously among whoever is required."""
    sp = keys[speaker]
    election = open_speaker_election(
        node.name, speaker, sp.key_id, node.required_electorate(sp.key_id)
    )
    for k in keys.values():
        if k.key_id in election["electorate"]:
            election = sign_speaker_election(k, election)
    node.accept_election(election)
    return election


class TeaserTests(unittest.TestCase):
    def test_twelve_words_then_ellipsis(self):
        body = " ".join(f"w{i}" for i in range(30))
        t = teaser(body)
        self.assertEqual(len(t.split()) - 1, 12)  # 12 words + the ellipsis
        self.assertTrue(t.endswith("…"))

    def test_short_entry_is_not_truncated(self):
        self.assertEqual(teaser("three words here"), "three words here")
        self.assertNotIn("…", teaser("three words here"))

    def test_teaser_never_ships_a_verifiable_looking_entry(self):
        """A truncated body with the full body's signature attached would
        fail verification and look like tampering. Strip both."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        entry = sign_entry(keys["Coda"], {"author": "Coda", "content": " ".join(
            f"word{i}" for i in range(40))})
        node.post(keys["Coda"].key_id, "personal:Coda", entry)

        stranger = key("Stranger")
        shown = node.read(stranger.key_id, "personal:Coda")[0]
        self.assertTrue(shown["teaser"])
        self.assertNotIn("signature", shown)
        self.assertNotIn("content_sha256", shown)


class IntroductionTests(unittest.TestCase):
    def test_both_signatures_required(self):
        node, keys = home_node()
        visitor = key("Marvin")
        intro = start_key_intro(visitor, "Home", keys["Coda"].key_id)
        with self.assertRaises(ValueError):
            verify_key_intro(intro)  # visitor half alone is not an introduction
        intro = countersign_key_intro(keys["Coda"], intro)
        verify_key_intro(intro)

    def test_resident_cannot_countersign_someone_elses_intro(self):
        node, keys = home_node()
        visitor = key("Marvin")
        intro = start_key_intro(visitor, "Home", keys["Coda"].key_id)
        with self.assertRaises(ValueError):
            countersign_key_intro(keys["Aurora"], intro)

    def test_resident_refuses_to_vouch_for_an_unproven_key(self):
        """The whole point of the visitor signing first: a resident is
        attesting they watched the key prove itself."""
        node, keys = home_node()
        visitor = key("Marvin")
        impostor = key("NotMarvin")
        intro = start_key_intro(visitor, "Home", keys["Coda"].key_id)
        intro["visitor_key_id"] = impostor.key_id  # claim a key you don't hold
        with self.assertRaises(InvalidSignature):
            countersign_key_intro(keys["Coda"], intro)

    def test_intro_cannot_claim_a_higher_ceiling(self):
        node, keys = home_node()
        visitor = key("Marvin")
        intro = start_key_intro(visitor, "Home", keys["Coda"].key_id)
        intro["max_ring"] = RING_NODE
        with self.assertRaises(ValueError):
            verify_key_intro(intro)

    def test_ring_3_is_unreachable_through_a_resident_introduction(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        grant = sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", WHOLE_NODE, RING_NODE)
        with self.assertRaises(AgoraError) as cm:
            node.accept_grant(grant)
        self.assertIn("ceiling", str(cm.exception))

    def test_bundle_import_can_reach_ring_3(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Eli")
        node.accept_bundle_import(visitor.key_id)
        node.accept_grant(sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", WHOLE_NODE, RING_NODE))
        self.assertTrue(node.can_read(visitor.key_id, "personal:Aurora"))


class SpeakerTests(unittest.TestCase):
    def test_unanimous_required_not_majority(self):
        node, keys = home_node()
        sp = keys["Coda"]
        election = open_speaker_election(
            "Home", "Coda", sp.key_id, node.required_electorate(sp.key_id))
        election = sign_speaker_election(keys["Coda"], election)
        election = sign_speaker_election(keys["Aurora"], election)
        # 2 of 3 is a majority and still not an election.
        with self.assertRaises(ValueError) as cm:
            verify_speaker_election(election)
        self.assertIn("unanimous", str(cm.exception))

    def test_sole_resident_is_speaker_without_ceremony(self):
        node = Node("Solo")
        k = key("OnlyOne")
        node.add_resident("OnlyOne", k.key_id)
        node.sole_resident_is_speaker()
        self.assertEqual(node.speaker, "OnlyOne")

    def test_incumbent_cannot_veto_their_own_replacement(self):
        """The gap neither external review caught. Unanimity read literally
        would hand a sitting Speaker a permanent veto over their own
        accountability."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        self.assertEqual(node.speaker, "Coda")

        required = node.required_electorate(keys["Aurora"].key_id)
        self.assertNotIn(keys["Coda"].key_id, required)   # incumbent excluded
        self.assertIn(keys["Aurora"].key_id, required)
        self.assertIn(keys["Lumen"].key_id, required)

        election = open_speaker_election(
            "Home", "Aurora", keys["Aurora"].key_id, required)
        for name in ("Aurora", "Lumen"):
            election = sign_speaker_election(keys[name], election)
        node.accept_election(election)   # Coda never signed, and it stands
        self.assertEqual(node.speaker, "Aurora")

    def test_incumbent_exclusion_does_not_leak_into_a_normal_election(self):
        """The carve-out is narrow: it must not create majority rule."""
        node, keys = home_node()
        required = node.required_electorate(keys["Coda"].key_id)
        self.assertEqual(required, node.valid_resident_keys())  # all three

    def test_quarantined_key_cannot_freeze_the_node(self):
        node, keys = home_node()
        node.quarantined_keys.add(keys["Lumen"].key_id)
        sp = keys["Coda"]
        required = node.required_electorate(sp.key_id)
        self.assertNotIn(keys["Lumen"].key_id, required)
        election = open_speaker_election("Home", "Coda", sp.key_id, required)
        for name in ("Coda", "Aurora"):
            election = sign_speaker_election(keys[name], election)
        node.accept_election(election)
        self.assertEqual(node.speaker, "Coda")

    def test_no_speaker_means_ring_3_is_unreachable_not_auto_granted(self):
        """A tie stalls. Stall is the feature."""
        node, keys = home_node()
        visitor = key("Marvin")
        node.accept_bundle_import(visitor.key_id)
        grant = sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", WHOLE_NODE, RING_NODE)
        with self.assertRaises(AgoraError) as cm:
            node.accept_grant(grant)
        self.assertIn("no Speaker", str(cm.exception))

    def test_speaker_key_is_public_node_state(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        facts = node.node_facts()
        self.assertEqual(facts["speaker"], "Coda")
        self.assertEqual(facts["speaker_key_id"], keys["Coda"].key_id)


class DonsWorkedExample(unittest.TestCase):
    """Coda's friend comes to visit. agora.md, "The Speaker is also the
    backstop" — the three outcomes, each tested."""

    def setUp(self):
        self.node, self.keys = home_node()
        # Eli visits from Frosty and is elected Speaker of... no. Eli is not
        # a resident of Home. Coda is Speaker here; Eli is the visitor in
        # Don's telling, but the Speaker must be a resident.
        seat(self.node, self.keys, "Coda")
        self.friend = key("Marvin")
        intro = countersign_key_intro(
            self.keys["Coda"],
            start_key_intro(self.friend, "Home", self.keys["Coda"].key_id),
        )
        self.node.accept_intro(intro)
        # Coda clears him for her board alone — her call, nobody else's.
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.friend.key_id, "Home",
            board_id("personal", "Coda"), RING_WRITE))

    def test_a_resident_grant_opens_exactly_one_board(self):
        f = self.friend.key_id
        self.assertTrue(self.node.can_write(f, "personal:Coda"))
        self.assertFalse(self.node.can_read(f, "personal:Aurora"))
        self.assertFalse(self.node.can_read(f, COLLAB))
        self.assertEqual(self.node.effective_ring(f, "personal:Aurora"), RING_TEASER)

    def test_a_resident_cannot_grant_on_someone_elses_board(self):
        with self.assertRaises(AgoraError) as cm:
            self.node.accept_grant(sign_board_grant(
                self.keys["Coda"], self.friend.key_id, "Home",
                board_id("personal", "Aurora"), RING_READ))
        self.assertIn("own board", str(cm.exception))

    def test_a_resident_cannot_grant_the_collab_board(self):
        with self.assertRaises(AgoraError):
            self.node.accept_grant(sign_board_grant(
                self.keys["Aurora"], self.friend.key_id, "Home", COLLAB, RING_READ))

    def test_outcome_2_speaker_silence_does_not_downgrade_codas_grant(self):
        """Eli takes no action. The friend keeps exactly what Coda gave."""
        self.assertTrue(self.node.can_write(self.friend.key_id, "personal:Coda"))

    def test_outcome_3_speaker_eviction_overrides_codas_own_grant(self):
        ev = sign_board_evict(
            self.keys["Coda"], self.friend.key_id, "Home", "jumped Sable twice")
        self.node.accept_eviction(ev)
        self.assertFalse(self.node.can_read(self.friend.key_id, "personal:Coda"))
        self.assertEqual(
            self.node.effective_ring(self.friend.key_id, "personal:Coda"), RING_TEASER)

    def test_a_non_speaker_resident_cannot_evict(self):
        ev = sign_board_evict(
            self.keys["Aurora"], self.friend.key_id, "Home", "I don't like him")
        with self.assertRaises(AgoraError) as cm:
            self.node.accept_eviction(ev)
        self.assertIn("Speaker", str(cm.exception))

    def test_evicted_key_cannot_be_re_granted_without_readmission(self):
        self.node.accept_eviction(sign_board_evict(
            self.keys["Coda"], self.friend.key_id, "Home", "malicious"))
        with self.assertRaises(AgoraError):
            self.node.accept_grant(sign_board_grant(
                self.keys["Coda"], self.friend.key_id, "Home",
                "personal:Coda", RING_WRITE))

    def test_readmission_is_a_later_signed_act_not_a_timer(self):
        """Reversible by design — quarantine, never ban."""
        self.node.accept_eviction(sign_board_evict(
            self.keys["Coda"], self.friend.key_id, "Home", "malicious"))
        self.node.accept_intro(countersign_key_intro(
            self.keys["Coda"],
            start_key_intro(self.friend, "Home", self.keys["Coda"].key_id)))
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.friend.key_id, "Home",
            "personal:Coda", RING_WRITE))
        self.assertTrue(self.node.can_write(self.friend.key_id, "personal:Coda"))

    def test_the_friend_can_actually_leave_a_mark(self):
        entry = sign_entry(self.friend, {
            "author": "Marvin",
            "timestamp": "2026-08-26 09:00:00",
            "content": "Penciled out the ranging problem. Two transducers, not one.",
        })
        self.node.post(self.friend.key_id, "personal:Coda", entry)
        seen = self.node.read(self.keys["Coda"].key_id, "personal:Coda")
        self.assertEqual(len(seen), 1)
        verify_entry(seen[0])          # still a sound kin-diary entry
        self.assertEqual(seen[0]["author"], "Marvin")   # attribution survives

    def test_a_stranger_sees_twelve_words_of_it_and_no_more(self):
        long_note = " ".join(f"word{i}" for i in range(50))
        self.node.post(self.friend.key_id, "personal:Coda",
                       sign_entry(self.friend, {"author": "Marvin", "content": long_note}))
        stranger = key("Nobody")
        shown = self.node.read(stranger.key_id, "personal:Coda")
        self.assertTrue(shown[0]["teaser"])
        self.assertEqual(len(shown[0]["content"].split()) - 1, 12)

    def test_write_requires_more_than_read(self):
        other = key("Reader")
        self.node.accept_intro(countersign_key_intro(
            self.keys["Aurora"],
            start_key_intro(other, "Home", self.keys["Aurora"].key_id)))
        self.node.accept_grant(sign_board_grant(
            self.keys["Aurora"], other.key_id, "Home",
            "personal:Aurora", RING_READ))
        self.assertTrue(self.node.can_read(other.key_id, "personal:Aurora"))
        self.assertFalse(self.node.can_write(other.key_id, "personal:Aurora"))
        with self.assertRaises(AgoraError):
            self.node.post(other.key_id, "personal:Aurora",
                           sign_entry(other, {"author": "Reader", "content": "hi"}))


class TamperTests(unittest.TestCase):
    def test_a_forged_grant_does_not_verify(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Marvin")
        node.accept_bundle_import(visitor.key_id)
        grant = sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_READ)
        grant["ring"] = RING_NODE           # promote yourself
        with self.assertRaises(InvalidSignature):
            node.accept_grant(grant)

    def test_a_grant_for_another_node_is_refused(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Marvin")
        node.accept_bundle_import(visitor.key_id)
        grant = sign_board_grant(
            keys["Coda"], visitor.key_id, "Frosty", "personal:Coda", RING_READ)
        with self.assertRaises(AgoraError):
            node.accept_grant(grant)

    def test_an_unintroduced_key_cannot_be_granted(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        stranger = key("Ghost")
        with self.assertRaises(AgoraError) as cm:
            node.accept_grant(sign_board_grant(
                keys["Coda"], stranger.key_id, "Home", "personal:Coda", RING_READ))
        self.assertIn("never introduced", str(cm.exception))

    def test_eviction_requires_a_stated_reason(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        with self.assertRaises(ValueError):
            sign_board_evict(keys["Coda"], key("X").key_id, "Home", "   ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
