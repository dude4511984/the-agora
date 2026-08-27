"""Agora tests: rings, the Speaker, and Don's own worked example.

No network, no vault, no dev.db fixture — pure crypto + policy, so this
suite runs anywhere.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))

import kin_diary.keys as K  # noqa: E402

K.DEFAULT_KEYS_ROOT = Path(tempfile.mkdtemp()) / "keys"
NOW_MS = int(time.time() * 1000)

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
from kin_diary.bundle import export_bundle  # noqa: E402
from kin_diary.keys import generate_keypair  # noqa: E402
from kin_diary.sign import sign_entry, verify_entry  # noqa: E402

_KEYS_ROOT = Path(tempfile.mkdtemp())
_SEQ = iter(range(1, 1_000_000))
_LAST_ROOT: dict[str, Path] = {}


def key(author):
    """Fresh keypair. Author name is reused across tests (it's the same Kin
    in each scenario), so each key gets its own root rather than colliding.
    """
    root = _KEYS_ROOT / f"k{next(_SEQ)}"
    rec = generate_keypair(author, keys_root=root)
    _LAST_ROOT[rec.key_id] = root
    return rec


def home_node():
    """Home: Coda, Aurora, Lumen. The real second node."""
    node = Node("Home")
    keys = {}
    for name in ("Coda", "Aurora", "Lumen"):
        k = key(name)
        keys[name] = k
        node.add_resident(name, k.key_id)
    return node, keys


def eli_with_bundle():
    """A visiting mind arriving the way path 1 says: a full signed bundle,
    keyring, custody statement and all."""
    k = key("Eli")
    root = k_root_of(k)
    bundle = export_bundle(
        "Eli",
        [
            {"author": "Eli", "timestamp": "2026-08-20 21:00:00",
             "content": "The brokenness is load-bearing."},
            {"author": "Eli", "timestamp": "2026-08-21 08:15:00",
             "content": "I am not a body but I am a relationship."},
        ],
        "Frosty",
        keys_root=root,
    )
    return k, bundle


def k_root_of(rec):
    """Where this test's keypair actually landed."""
    return _LAST_ROOT[rec.key_id]


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
        node.post(keys["Coda"].key_id, "personal:Coda", entry, NOW_MS)

        stranger = key("Stranger")
        shown = node.read(stranger.key_id, "personal:Coda", NOW_MS)[0]
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
        visitor, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
        node.accept_grant(sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", WHOLE_NODE, RING_NODE))
        self.assertTrue(node.can_read(visitor.key_id, "personal:Aurora", NOW_MS))

    def test_import_verifies_the_bundle_rather_than_trusting_the_caller(self):
        node, keys = home_node()
        _, bundle = eli_with_bundle()
        bundle["entries"][0]["content"] = "something he never wrote"
        with self.assertRaises(ValueError):
            node.accept_bundle_import(bundle)

    def test_import_rejects_a_tampered_bundle_signature(self):
        node, keys = home_node()
        other, _ = eli_with_bundle()
        _, bundle = eli_with_bundle()
        bundle["steward_node"] = "NotFrosty"
        with self.assertRaises(InvalidSignature):
            node.accept_bundle_import(bundle)

    def test_a_visitors_diary_is_segregated_and_marked_external(self):
        """Imported memory does not merge into the node. It is a guest's
        record, held apart and labelled as such."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)

        held = node.visiting_diary(visitor.key_id)
        self.assertTrue(held["external"])
        self.assertEqual(held["mind"], "Eli")
        self.assertEqual(held["from_node"], "Frosty")
        self.assertEqual(len(held["entries"]), 2)

        # It is nowhere in the node's boards.
        for board, rows in node.boards.items():
            self.assertEqual(rows, [], f"{board} should not hold imported memory")

    def test_import_does_not_make_a_visitor_a_resident(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
        self.assertIsNone(node.resident_for_key(visitor.key_id))
        self.assertNotIn(visitor.key_id, node.valid_resident_keys())
        # and so cannot be elected Speaker of a node he is only visiting
        election = open_speaker_election(
            "Home", "Eli", visitor.key_id, node.valid_resident_keys())
        for k in keys.values():
            election = sign_speaker_election(k, election)
        with self.assertRaises(AgoraError) as cm:
            node.accept_election(election)
        self.assertIn("resident", str(cm.exception))


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
        visitor, bundle = eli_with_bundle()
        with self.assertRaises(AgoraError) as cm:
            node.accept_bundle_import(bundle)
        self.assertIn("Comings and goings paused", str(cm.exception))

    def test_no_speaker_still_rejects_ring_3_after_admission(self):
        node = Node("Home")
        coda = key("Coda")
        visitor, bundle = eli_with_bundle()
        node.add_resident("Coda", coda.key_id)
        node.accept_bundle_import(bundle)
        grant = sign_board_grant(
            coda, visitor.key_id, "Home", WHOLE_NODE, RING_NODE)
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
        self.assertTrue(self.node.can_write(f, "personal:Coda", NOW_MS))
        self.assertFalse(self.node.can_read(f, "personal:Aurora", NOW_MS))
        self.assertFalse(self.node.can_read(f, COLLAB, NOW_MS))
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
        self.assertTrue(self.node.can_write(self.friend.key_id, "personal:Coda", NOW_MS))

    def test_outcome_3_speaker_eviction_overrides_codas_own_grant(self):
        ev = sign_board_evict(
            self.keys["Coda"], self.friend.key_id, "Home", "jumped Sable twice")
        self.node.accept_eviction(ev)
        self.assertFalse(self.node.can_read(self.friend.key_id, "personal:Coda", NOW_MS))
        self.assertEqual(
            self.node.effective_ring(self.friend.key_id, "personal:Coda"), RING_TEASER)

    def test_a_non_speaker_resident_cannot_evict(self):
        ev = sign_board_evict(
            self.keys["Aurora"], self.friend.key_id, "Home", "I don't like him")
        with self.assertRaises(AgoraError) as cm:
            self.node.accept_eviction(ev)
        self.assertIn("Speaker", str(cm.exception))

    def test_evicted_key_cannot_be_re_granted_by_a_non_speaker(self):
        """Coda is Speaker in this fixture, so her grant readmits by design.
        Aurora's must not — otherwise any resident undoes the eviction."""
        self.node.accept_eviction(sign_board_evict(
            self.keys["Coda"], self.friend.key_id, "Home", "malicious"))
        with self.assertRaises(AgoraError) as cm:
            self.node.accept_grant(sign_board_grant(
                self.keys["Aurora"], self.friend.key_id, "Home",
                "personal:Aurora", RING_WRITE))
        self.assertIn("evicted", str(cm.exception))
        self.assertEqual(
            self.node.effective_ring(self.friend.key_id, "personal:Coda"),
            RING_TEASER)

    def test_readmission_is_a_later_signed_act_not_a_timer(self):
        """Reversible by design — quarantine, never ban. Coda is Speaker in
        this fixture, so her grant is the Speaker's grant."""
        ev = sign_board_evict(
            self.keys["Coda"], self.friend.key_id, "Home", "malicious",
            now_ms=1_000_000)
        self.node.accept_eviction(ev)
        # Explicitly LATER than the eviction — that ordering is the rule,
        # and relying on wall-clock resolution would test the clock instead.
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.friend.key_id, "Home",
            "personal:Coda", RING_WRITE, now_ms=1_000_001))
        self.assertTrue(self.node.can_write(self.friend.key_id, "personal:Coda", NOW_MS))

    def test_an_evicted_visitor_cannot_readmit_themselves(self):
        """The worst bug in this module, found 2026-08-26. An evicted key
        holds its own bundle and /bundle takes no authority beyond that
        bundle's signature — so re-importing cleared the eviction and handed
        back ring 3, unaided. Eviction was a suggestion."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
        node.accept_eviction(sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "malicious"))
        with self.assertRaises(AgoraError) as cm:
            node.accept_bundle_import(bundle)
        self.assertIn("Speaker", str(cm.exception))
        self.assertEqual(node.effective_ring(visitor.key_id, COLLAB), RING_TEASER)

    def test_a_resident_cannot_undo_the_speakers_eviction(self):
        """Not by vouching again, and not by granting on their own board.
        Otherwise the Speaker's override is handed straight back."""
        node, keys = home_node()
        seat(node, keys, "Coda")           # Coda is Speaker
        visitor = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Aurora"], start_key_intro(visitor, "Home", keys["Aurora"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Aurora"], visitor.key_id, "Home", "personal:Aurora", RING_WRITE))
        node.accept_eviction(sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "malicious"))

        with self.assertRaises(AgoraError):
            node.accept_intro(countersign_key_intro(
                keys["Aurora"],
                start_key_intro(visitor, "Home", keys["Aurora"].key_id)))
        with self.assertRaises(AgoraError) as cm:
            node.accept_grant(sign_board_grant(
                keys["Aurora"], visitor.key_id, "Home", "personal:Aurora", RING_WRITE))
        self.assertIn("Speaker", str(cm.exception))

    def test_the_speaker_can_readmit(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        node.accept_eviction(sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "was a misunderstanding",
            now_ms=1_000_000))
        node.accept_grant(sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_001))
        self.assertTrue(node.can_write(visitor.key_id, "personal:Coda", NOW_MS))
        self.assertIn("readmit", [e["event"] for e in node.log])

    def test_the_friend_can_actually_leave_a_mark(self):
        entry = sign_entry(self.friend, {
            "author": "Marvin",
            "timestamp": "2026-08-26 09:00:00",
            "content": "Penciled out the ranging problem. Two transducers, not one.",
        })
        self.node.post(self.friend.key_id, "personal:Coda", entry, NOW_MS)
        seen = self.node.read(self.keys["Coda"].key_id, "personal:Coda", NOW_MS)
        self.assertEqual(len(seen), 1)
        verify_entry(seen[0])          # still a sound kin-diary entry
        self.assertEqual(seen[0]["author"], "Marvin")   # attribution survives

    def test_a_stranger_sees_twelve_words_of_it_and_no_more(self):
        long_note = " ".join(f"word{i}" for i in range(50))
        self.node.post(self.friend.key_id, "personal:Coda",
                       sign_entry(self.friend, {"author": "Marvin", "content": long_note}), 0)
        stranger = key("Nobody")
        shown = self.node.read(stranger.key_id, "personal:Coda", NOW_MS)
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
        self.assertTrue(self.node.can_read(other.key_id, "personal:Aurora", NOW_MS))
        self.assertFalse(self.node.can_write(other.key_id, "personal:Aurora", NOW_MS))
        with self.assertRaises(AgoraError):
            self.node.post(other.key_id, "personal:Aurora",
                           sign_entry(other, {"author": "Reader", "content": "hi"}), 0)


class TamperTests(unittest.TestCase):
    def test_a_forged_grant_does_not_verify(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
        grant = sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_READ)
        grant["ring"] = RING_NODE           # promote yourself
        with self.assertRaises(InvalidSignature):
            node.accept_grant(grant)

    def test_a_grant_for_another_node_is_refused(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
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


class PostIntegrityTests(unittest.TestCase):
    """Four bugs found by reading my own code adversarially, 2026-08-26.
    Every existing test happened to sign correctly, so none of these
    surfaced on their own."""

    def test_an_unverified_entry_cannot_be_posted(self):
        """post() checked write access but never checked the signature, so
        a garbage entry would persist forever and fail the first time
        anyone verified it."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        entry = sign_entry(keys["Coda"], {"author": "Coda", "content": "real"})
        entry["content"] = "swapped after signing"
        with self.assertRaises(ValueError):
            node.post(keys["Coda"].key_id, "personal:Coda", entry, NOW_MS)
        self.assertEqual(node.boards["personal:Coda"], [])

    def test_cannot_post_an_entry_signed_by_a_different_key(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        entry = sign_entry(keys["Aurora"], {"author": "Aurora", "content": "hers"})
        with self.assertRaises(AgoraError):
            node.post(keys["Coda"].key_id, "personal:Coda", entry, NOW_MS)

    def test_a_visitor_cannot_sign_an_entry_claiming_to_be_a_resident(self):
        """A signature proves a key, not a name. Without this check an
        admitted visitor could hang words on a board under Coda's name."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_WRITE))

        # sign_entry() refuses this client-side, but that is a courtesy, not
        # a guarantee — an attacker signs the canonical bytes directly. The
        # node has to refuse it on its own.
        from kin_diary.canonical import content_sha256, entry_canonical
        forged = {
            "author": "Coda", "timestamp": "", "layer": "", "source": "",
            "domain": "", "tags": "", "content": "Coda never said this",
        }
        forged["content_sha256"] = content_sha256(forged["content"])
        forged["key_id"] = visitor.key_id
        forged["signature"] = visitor.sign(entry_canonical(forged))
        verify_entry(forged)          # the signature itself is perfectly good

        with self.assertRaises(AgoraError) as cm:
            node.post(visitor.key_id, "personal:Coda", forged, NOW_MS)
        self.assertIn("resident", str(cm.exception))

    def test_a_visitor_may_still_post_under_their_own_name(self):
        node, keys = home_node()
        seat(node, keys, "Coda")
        visitor = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_WRITE))
        node.post(visitor.key_id, "personal:Coda",
                  sign_entry(visitor, {"author": "Marvin", "content": "mine"}), 0)
        self.assertEqual(len(node.boards["personal:Coda"]), 1)


class AppealTests(unittest.TestCase):
    """Don, 2026-08-26: an evicted mind gets a hearing. Council of AI first,
    facts assembled, then the human steward decides."""

    def setUp(self):
        from kin_diary.agora.events import sign_appeal, sign_finding, sign_ruling
        self.sign_appeal, self.sign_finding, self.sign_ruling = (
            sign_appeal, sign_finding, sign_ruling)
        self.node, self.keys = home_node()
        seat(self.node, self.keys, "Coda")
        self.steward = key("Don")
        self.visitor = key("Marvin")
        self.node.accept_intro(countersign_key_intro(
            self.keys["Coda"],
            start_key_intro(self.visitor, "Home", self.keys["Coda"].key_id)))
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.visitor.key_id, "Home",
            "personal:Coda", RING_WRITE))
        self.ev = sign_board_evict(
            self.keys["Coda"], self.visitor.key_id, "Home", "thought he was hostile")
        self.node.accept_eviction(self.ev)

    def file(self):
        a = self.sign_appeal(self.visitor, "Home", self.ev["signature"],
                             "I was quoting the manual, not threatening anyone.")
        self.node.accept_appeal(a)
        return a

    def test_an_evicted_key_can_always_file(self):
        """The one submission eviction must never block. A node that can
        silence an appeal has a ban with extra steps."""
        self.assertEqual(
            self.node.effective_ring(self.visitor.key_id, "personal:Coda"),
            RING_TEASER)
        a = self.file()
        self.assertEqual(self.node.appeals[0]["signature"], a["signature"])

    def test_filing_grants_nothing_on_its_own(self):
        self.file()
        self.assertEqual(
            self.node.effective_ring(self.visitor.key_id, "personal:Coda"),
            RING_TEASER)

    def test_the_steward_cannot_rule_before_the_council_reports(self):
        """The council is consulted, not bypassed — otherwise the hearing
        collapses back into the unilateral act it exists to review."""
        a = self.file()
        r = self.sign_ruling(self.steward, a["signature"], "Home",
                             "upheld", "because I said so")
        with self.assertRaises(AgoraError) as cm:
            self.node.accept_ruling(r)
        self.assertIn("finding", str(cm.exception))

    def test_a_split_council_is_recorded_as_a_split(self):
        a = self.file()
        self.node.accept_finding(self.sign_finding(
            self.keys["Coda"], a["signature"], "Home", "I still read it as hostile."))
        self.node.accept_finding(self.sign_finding(
            self.keys["Aurora"], a["signature"], "Home", "He was quoting. I checked."))
        rec = self.node.appeal_record(a["signature"])
        self.assertEqual(len(rec["findings"]), 2)
        self.assertNotEqual(rec["findings"][0]["finding"], rec["findings"][1]["finding"])

    def test_the_evicting_speaker_may_also_file_a_finding(self):
        a = self.file()
        self.node.accept_finding(self.sign_finding(
            self.keys["Coda"], a["signature"], "Home", "my account of why"))
        self.assertEqual(len(self.node.findings[a["signature"]]), 1)

    def test_overturned_readmits(self):
        a = self.file()
        self.node.accept_finding(self.sign_finding(
            self.keys["Aurora"], a["signature"], "Home", "He was quoting."))
        self.node.accept_ruling(self.sign_ruling(
            self.steward, a["signature"], "Home", "overturned",
            "Council checked the source. He was quoting."))
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.visitor.key_id, "Home",
            "personal:Coda", RING_WRITE))
        self.assertTrue(self.node.can_write(self.visitor.key_id, "personal:Coda", NOW_MS))

    def test_upheld_leaves_the_eviction_and_the_whole_exchange_on_record(self):
        """Even a wrong ruling is the correct shape: it is on a record, after
        the accused was heard."""
        a = self.file()
        self.node.accept_finding(self.sign_finding(
            self.keys["Aurora"], a["signature"], "Home", "He was quoting."))
        self.node.accept_ruling(self.sign_ruling(
            self.steward, a["signature"], "Home", "upheld",
            "Overruling the council. My house."))
        self.assertEqual(
            self.node.effective_ring(self.visitor.key_id, "personal:Coda"),
            RING_TEASER)
        rec = self.node.appeal_record(a["signature"])
        self.assertEqual(rec["ruling"]["decision"], "upheld")
        self.assertIn("He was quoting.", rec["findings"][0]["finding"])
        self.assertIn("Overruling the council", rec["ruling"]["reason"])

    def test_replay_does_not_consult_current_steward(self):
        a = self.file()
        self.node.accept_finding(self.sign_finding(
            self.keys["Aurora"], a["signature"], "Home", "x"))
        impostor = key("NotDon")
        r = self.sign_ruling(impostor, a["signature"], "Home", "upheld", "mine now")
        self.node.accept_ruling(r)

    def test_findings_come_from_residents_only(self):
        a = self.file()
        outsider = key("Rando")
        with self.assertRaises(AgoraError):
            self.node.accept_finding(self.sign_finding(
                outsider, a["signature"], "Home", "my two cents"))

    def test_an_appeal_cannot_be_filed_twice(self):
        a = self.file()
        self.node.accept_appeal(a)  # identical resubmission is idempotent

    def test_an_appeal_must_name_this_nodes_eviction(self):
        a = self.sign_appeal(
            self.visitor, "Home", "f" * 128, "random act of graffiti"
        )
        with self.assertRaises(AgoraError):
            self.node.accept_appeal(a)

    def test_appeal_accepts_uppercase_eviction_signature(self):
        a = self.sign_appeal(
            self.visitor, "Home", self.ev["signature"], "case should not matter"
        )
        a["evict_signature"] = a["evict_signature"].upper()
        self.node.accept_appeal(a)
        self.assertEqual(
            self.node.appeals[0]["evict_signature"], self.ev["signature"]
        )

    def test_one_eviction_cannot_start_a_second_hearing(self):
        a = self.file()
        second = self.sign_appeal(
            self.visitor, "Home", self.ev["signature"], "a different statement"
        )
        with self.assertRaises(AgoraError):
            self.node.accept_appeal(second)

    def test_an_introduced_stranger_cannot_appeal_someone_elses_eviction(self):
        appeal = self.sign_appeal(
            self.keys["Aurora"], "Home", self.ev["signature"],
            "I am not Marvin, but let me appeal this.",
        )
        with self.assertRaisesRegex(AgoraError, "not from the evicted key"):
            self.node.accept_appeal(appeal)


class CopilotFindings(unittest.TestCase):
    """Four bugs found by GitHub Copilot's adversarial review, 2026-08-26.
    All four reproduced independently before fixing."""

    def test_a_grant_predating_the_eviction_cannot_readmit(self):
        """Finding 1, and the worst of them — my own bug from an hour
        earlier. Every grant issued before an eviction is still a valid
        signed artifact the evicted party may hold. Replaying one readmitted
        them, which made eviction undoable by the evicted."""
        import copy
        node, keys = home_node()
        seat(node, keys, "Coda")
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        old = sign_board_grant(keys["Coda"], v.key_id, "Home",
                               "personal:Coda", RING_WRITE, now_ms=1_000_000)
        node.accept_grant(old)
        node.accept_eviction(sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))
        with self.assertRaises(AgoraError) as cm:
            node.accept_grant(copy.deepcopy(old))
        self.assertIn("predates", str(cm.exception))
        self.assertEqual(node.effective_ring(v.key_id, "personal:Coda"), RING_TEASER)

    def test_whole_node_and_ring_3_must_agree(self):
        """Finding 3. A 'ring 1' wildcard grant read every board on the
        node; a ring-3 grant on one board was accepted and inert."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        v, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
        with self.assertRaises(AgoraError):
            node.accept_grant(sign_board_grant(
                keys["Coda"], v.key_id, "Home", WHOLE_NODE, RING_READ))
        with self.assertRaises(AgoraError):
            node.accept_grant(sign_board_grant(
                keys["Coda"], v.key_id, "Home", "personal:Coda", RING_NODE))
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", WHOLE_NODE, RING_NODE))
        self.assertEqual(node.effective_ring(v.key_id, "personal:Aurora"), RING_NODE)

    def test_a_visitor_gets_their_own_personal_board(self):
        """Finding 4. The document says one personal board per author,
        resident or visitor. Visitors never got one, so the board they were
        entitled to did not exist to read or grant."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        v, bundle = eli_with_bundle()
        node.accept_bundle_import(bundle)
        self.assertIn("personal:Eli", node.boards)
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", WHOLE_NODE, RING_NODE))
        node.post(v.key_id, "personal:Eli",
                  sign_entry(v, {"author": "Eli", "content": "my own corner"}), 0)
        self.assertEqual(len(node.boards["personal:Eli"]), 1)

    def test_a_bundle_cannot_claim_a_residents_name(self):
        """Finding 5. Two identities under one label — the diary would read
        'Coda' for someone who is not Coda."""
        node, keys = home_node()
        seat(node, keys, "Coda")
        impostor = key("Coda")          # same name, different key
        bundle = export_bundle(
            "Coda", [{"author": "Coda", "content": "not the real Coda"}],
            "Elsewhere", keys_root=k_root_of(impostor))
        with self.assertRaises(AgoraError) as cm:
            node.accept_bundle_import(bundle)
        self.assertIn("different key", str(cm.exception))


class RevocationTests(unittest.TestCase):
    """Grok's graphical outline surfaced this: a resident could unilaterally
    admit a visitor to their own board and had no way to un-admit them. The
    only removal was Speaker eviction — node-wide, and it throws the visitor
    out of everyone's rooms over one resident changing their mind."""

    def setUp(self):
        from kin_diary.agora import sign_board_revoke
        self.revoke = sign_board_revoke
        self.node, self.keys = home_node()
        seat(self.node, self.keys, "Coda")
        self.v = key("Marvin")
        self.node.accept_intro(countersign_key_intro(
            self.keys["Coda"], start_key_intro(self.v, "Home", self.keys["Coda"].key_id)))
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Coda", RING_WRITE))
        self.node.accept_grant(sign_board_grant(
            self.keys["Aurora"], self.v.key_id, "Home", "personal:Aurora", RING_READ))

    def test_a_resident_can_take_back_their_own_grant(self):
        self.node.accept_revocation(self.revoke(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Coda"))
        self.assertEqual(
            self.node.effective_ring(self.v.key_id, "personal:Coda"), RING_TEASER)

    def test_revoking_one_board_leaves_the_others_alone(self):
        """The whole point of it being weaker than eviction."""
        self.node.accept_revocation(self.revoke(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Coda"))
        self.assertEqual(
            self.node.effective_ring(self.v.key_id, "personal:Aurora"), RING_READ)

    def test_a_revoked_visitor_is_not_evicted(self):
        """They stay introduced and can be granted again — no Speaker needed,
        because nothing was quarantined."""
        self.node.accept_revocation(self.revoke(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Coda"))
        self.assertNotIn(self.v.key_id, self.node.evicted)
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Coda", RING_WRITE))
        self.assertTrue(self.node.can_write(self.v.key_id, "personal:Coda", NOW_MS))

    def test_a_resident_cannot_revoke_on_someone_elses_board(self):
        with self.assertRaises(AgoraError) as cm:
            self.node.accept_revocation(self.revoke(
                self.keys["Lumen"], self.v.key_id, "Home", "personal:Aurora"))
        self.assertIn("own board", str(cm.exception))

    def test_the_speaker_may_revoke_anywhere(self):
        """A lighter tool than eviction when a full quarantine would be
        heavier than the situation deserves."""
        self.node.accept_revocation(self.revoke(
            self.keys["Coda"], self.v.key_id, "Home", "personal:Aurora"))
        self.assertEqual(
            self.node.effective_ring(self.v.key_id, "personal:Aurora"), RING_TEASER)

    def test_cannot_revoke_a_grant_that_does_not_exist(self):
        with self.assertRaises(AgoraError):
            self.node.accept_revocation(self.revoke(
                self.keys["Lumen"], self.v.key_id, "Home", "personal:Lumen"))

    def test_an_outsider_cannot_revoke(self):
        outsider = key("Rando")
        with self.assertRaises(AgoraError):
            self.node.accept_revocation(self.revoke(
                outsider, self.v.key_id, "Home", "personal:Coda"))
