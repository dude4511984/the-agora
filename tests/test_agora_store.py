"""Persistence: the log is the truth, node state is the replay."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import NOW_MS, eli_with_bundle, home_node, key, seat  # noqa: E402

from kin_diary.agora import (  # noqa: E402
    RING_NODE,
    RING_WRITE,
    WHOLE_NODE,
    AgoraError,
    countersign_key_intro,
    open_speaker_election,
    sign_board_evict,
    sign_board_grant,
    sign_speaker_election,
    start_key_intro,
)
from kin_diary.agora.store import NodeStore  # noqa: E402
from kin_diary.sign import sign_entry  # noqa: E402


def fresh_store():
    path = Path(tempfile.mkdtemp()) / "agora.db"
    store = NodeStore(path, "Home")
    keys = {}
    for name in ("Coda", "Aurora", "Lumen"):
        k = key(name)
        keys[name] = k
        store.add_resident(name, k.key_id)
    return store, keys, path


def elect(store, keys, speaker="Coda"):
    node = store.load()
    sp = keys[speaker]
    election = open_speaker_election(
        "Home", speaker, sp.key_id, node.required_electorate(sp.key_id))
    for k in keys.values():
        if k.key_id in election["electorate"]:
            election = sign_speaker_election(k, election)
    store.record("election", election)
    return election


class StoreTests(unittest.TestCase):
    def test_state_survives_a_reopen(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        store.record("grant", sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_WRITE))
        store.close()

        reopened = NodeStore(path, "Home").load()
        self.assertEqual(reopened.speaker, "Coda")
        self.assertTrue(reopened.can_write(visitor.key_id, "personal:Coda", NOW_MS))
        self.assertFalse(reopened.can_read(visitor.key_id, "personal:Aurora", NOW_MS))

    def test_a_rejected_event_never_lands_in_the_log(self):
        """An append-only log cannot un-write a bad event, so validation has
        to happen before the INSERT, not after."""
        store, keys, path = fresh_store()
        elect(store, keys)
        stranger = key("Ghost")
        with self.assertRaises(AgoraError):
            store.record("grant", sign_board_grant(
                keys["Coda"], stranger.key_id, "Home", "personal:Coda", RING_WRITE))
        rows = store.conn.execute(
            "SELECT COUNT(*) c FROM agora_events WHERE kind='grant'").fetchone()["c"]
        self.assertEqual(rows, 0)
        # and the replay is still clean
        store.load()

    def test_posts_persist_with_signatures_intact(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        entry = sign_entry(keys["Coda"], {
            "author": "Coda", "timestamp": "2026-08-26 10:00:00",
            "content": "penciling out the ranging problem"})
        store.post(keys["Coda"].key_id, "personal:Coda", entry, NOW_MS)
        store.close()

        from kin_diary.sign import verify_entry
        rows = NodeStore(path, "Home").read(
            keys["Coda"].key_id, "personal:Coda", NOW_MS
        )
        self.assertEqual(len(rows), 1)
        verify_entry(rows[0])          # survived the round trip through SQLite

    def test_teaser_is_applied_at_read_not_at_rest(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        body = " ".join(f"word{i}" for i in range(40))
        store.post(keys["Coda"].key_id, "personal:Coda",
                   sign_entry(keys["Coda"], {"author": "Coda", "content": body}), 0)

        stranger = key("Nobody")
        shown = store.read(stranger.key_id, "personal:Coda", NOW_MS)[0]
        self.assertTrue(shown["teaser"])
        # the full text is still on disk, unharmed
        full = store.read(keys["Coda"].key_id, "personal:Coda", NOW_MS)[0]
        self.assertEqual(full["content"], body)

    def test_eviction_replays_as_revocation_not_as_deletion(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        store.record("grant", sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_WRITE))
        store.record("evict", sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "jumped Sable twice"))
        store.close()

        reopened = NodeStore(path, "Home")
        node = reopened.load()
        self.assertFalse(node.can_read(visitor.key_id, "personal:Coda", NOW_MS))
        # The grant event is still in the record. Nothing was erased.
        kinds = [r["kind"] for r in reopened.conn.execute(
            "SELECT kind FROM agora_events ORDER BY seq")]
        self.assertEqual(kinds, ["election", "intro", "grant", "evict"])

    def test_bundle_import_persists_and_stays_segregated(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor, bundle = eli_with_bundle()
        store.record("bundle", bundle)
        store.record("grant", sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", WHOLE_NODE, RING_NODE))
        store.close()

        node = NodeStore(path, "Home").load()
        self.assertTrue(node.can_read(visitor.key_id, "personal:Aurora", NOW_MS))
        held = node.visiting_diary(visitor.key_id)
        self.assertTrue(held["external"])
        self.assertEqual(held["from_node"], "Frosty")
        for board, rows in node.boards.items():
            self.assertEqual(rows, [])   # never merged into a board


if __name__ == "__main__":
    unittest.main(verbosity=2)
