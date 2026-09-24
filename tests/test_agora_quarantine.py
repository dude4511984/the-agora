"""agora-key-quarantine-v1: a resident key's quorum status, as a logged event.

Butter P3. Grok's spec: agora_quarantine_event_decision.md. The bug this kills:
quarantined_keys was a RAM-only set — valid_resident_keys() subtracted it, the
wheel skipped it, grant refused a quarantined issuer, but NOTHING wrote it to
the log, so it vanished on reboot (a quorum rule that could not survive a
restart). Now it is a steward-signed, local, replayed event with
action=quarantine|release.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import key  # noqa: E402

from kin_diary.agora import (  # noqa: E402
    AgoraError,
    RING_WRITE,
    open_speaker_election,
    sign_board_grant,
    sign_quarantine,
    sign_speaker_election,
)
from kin_diary.agora.store import NodeStore  # noqa: E402


def founded_store(steward_name="Marvin"):
    """Three residents, a steward, Coda elected Speaker. Returns the pieces."""
    path = Path(tempfile.mkdtemp()) / "q.db"
    steward = key(steward_name)
    store = NodeStore(path, "Home", steward_key_id=steward.key_id)
    keys = {}
    for name in ("Coda", "Aurora", "Lumen"):
        k = key(name); keys[name] = k
        store.add_resident(name, k.key_id)
    node = store.load()
    el = open_speaker_election("Home", "Coda", keys["Coda"].key_id,
                               node.required_electorate(keys["Coda"].key_id))
    for k in keys.values():
        if k.key_id in el["electorate"]:
            el = sign_speaker_election(k, el)
    store.record("election", el)
    return store, keys, steward, path


class QuarantineIsALoggedEvent(unittest.TestCase):

    # ── the positive control ────────────────────────────────────────────
    def test_quarantine_drops_the_key_from_quorum_speaker_stays(self):
        store, keys, steward, _ = founded_store()
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Aurora"].key_id, "quarantine", "dead key"))
        node = store.load()
        self.assertNotIn(keys["Aurora"].key_id, node.valid_resident_keys())
        self.assertIn(keys["Coda"].key_id, node.valid_resident_keys())
        # a quarantined key cannot grant as issuer
        with self.assertRaises(AgoraError):
            node.accept_grant(sign_board_grant(
                keys["Aurora"], keys["Coda"].key_id, "Home",
                "personal:Aurora", RING_WRITE))
        # Speaker (Coda, not quarantined) stays seated; node still opens
        self.assertEqual(node.speaker_key_id, keys["Coda"].key_id)

    # ── THE bug: it must survive a reboot ───────────────────────────────
    def test_the_set_survives_a_reopen(self):
        store, keys, steward, path = founded_store()
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Aurora"].key_id, "quarantine", "dead key"))
        store.close()
        reopened = NodeStore(path, "Home", steward_key_id=steward.key_id).load()
        self.assertNotIn(keys["Aurora"].key_id, reopened.valid_resident_keys())

    def test_release_then_reopen_the_key_counts_again(self):
        store, keys, steward, path = founded_store()
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Aurora"].key_id, "quarantine", "dead key"))
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Aurora"].key_id, "release", "undo"))
        store.close()
        reopened = NodeStore(path, "Home", steward_key_id=steward.key_id).load()
        self.assertIn(keys["Aurora"].key_id, reopened.valid_resident_keys())

    def test_a_quarantined_speaker_stays_seated(self):
        store, keys, steward, _ = founded_store()
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Coda"].key_id, "quarantine", "dead speaker key"))
        node = store.load()
        self.assertEqual(node.speaker_key_id, keys["Coda"].key_id)  # still seated
        self.assertNotIn(keys["Coda"].key_id, node.valid_resident_keys())

    # ── refusals ────────────────────────────────────────────────────────
    def test_cannot_quarantine_the_last_valid_key(self):
        store, keys, steward, _ = founded_store()
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Aurora"].key_id, "quarantine", "one"))
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Lumen"].key_id, "quarantine", "two"))
        # only Coda's key is valid now; quarantining it would empty quorum
        with self.assertRaises(AgoraError) as cm:
            store.record("quarantine", sign_quarantine(
                steward, "Home", keys["Coda"].key_id, "quarantine", "three"))
        self.assertIn("last valid resident key", str(cm.exception))

    def test_cannot_quarantine_a_non_resident_key(self):
        store, keys, steward, _ = founded_store()
        stranger = key("Ghost")
        with self.assertRaises(AgoraError) as cm:
            store.record("quarantine", sign_quarantine(
                steward, "Home", stranger.key_id, "quarantine", "who"))
        self.assertIn("not a resident", str(cm.exception))

    def test_wrong_steward_is_refused_at_the_gate(self):
        store, keys, steward, _ = founded_store()
        impostor = key("Impostor")
        with self.assertRaises(AgoraError) as cm:
            store.record("quarantine", sign_quarantine(
                impostor, "Home", keys["Aurora"].key_id, "quarantine", "x"))
        self.assertIn("steward", str(cm.exception))

    def test_a_speaker_signed_quarantine_is_refused(self):
        # verify loads the steward key, not the speaker; a Speaker-signed event
        # fails the store steward gate (its steward_key_id is the Speaker's).
        store, keys, steward, _ = founded_store()
        with self.assertRaises(AgoraError):
            store.record("quarantine", sign_quarantine(
                keys["Coda"], "Home", keys["Aurora"].key_id, "quarantine", "x"))

    def test_empty_reason_is_refused_at_canonical(self):
        store, keys, steward, _ = founded_store()
        with self.assertRaises((AgoraError, ValueError)):
            store.record("quarantine", sign_quarantine(
                steward, "Home", keys["Aurora"].key_id, "quarantine", "   "))

    def test_a_bogus_action_is_refused_at_canonical(self):
        store, keys, steward, _ = founded_store()
        with self.assertRaises((AgoraError, ValueError)):
            store.record("quarantine", sign_quarantine(
                steward, "Home", keys["Aurora"].key_id, "ban", "x"))

    def test_release_of_a_never_quarantined_key_is_idempotent(self):
        # replay of a confused log must not brick
        store, keys, steward, _ = founded_store()
        store.record("quarantine", sign_quarantine(
            steward, "Home", keys["Aurora"].key_id, "release", "nothing to undo"))
        node = store.load()
        self.assertIn(keys["Aurora"].key_id, node.valid_resident_keys())


if __name__ == "__main__":
    unittest.main()
