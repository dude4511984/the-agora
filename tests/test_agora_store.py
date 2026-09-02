"""Persistence: the log is the truth, node state is the replay."""

import os
import json
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
from kin_diary.agora.events import (  # noqa: E402
    sign_appeal, sign_finding, sign_resident, sign_ruling,
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
    def test_hearing_survives_close_and_reopen(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        store.record("grant", sign_board_grant(
            keys["Coda"], visitor.key_id, "Home", "personal:Coda", RING_WRITE))
        eviction = sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "jumped Sable twice")
        store.record("evict", eviction)

        appeal = sign_appeal(
            visitor, "Home", eviction["signature"],
            "I was quoting the manual, not threatening anyone.",
        )
        store.record("appeal", appeal)
        first = sign_finding(
            keys["Coda"], appeal["signature"], "Home",
            "I still read it as hostile.",
        )
        second = sign_finding(
            keys["Aurora"], appeal["signature"], "Home",
            "He was quoting. I checked.",
        )
        store.record("finding", first)
        store.record("finding", second)
        store.close()

        reopened = NodeStore(path, "Home")
        node = reopened.load()
        self.assertEqual(node.appeals, [appeal])
        self.assertEqual(node.findings[appeal["signature"]], [first, second])
        self.assertEqual(
            node.appeal_record(appeal["signature"]),
            {"appeal": appeal, "findings": [first, second], "ruling": None},
        )

    def test_duplicate_appeal_after_reopen_does_not_corrupt_findings(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        eviction = sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "jumped Sable twice")
        store.record("evict", eviction)
        appeal = sign_appeal(
            visitor, "Home", eviction["signature"],
            "I was quoting the manual, not threatening anyone.",
        )
        finding = sign_finding(
            keys["Aurora"], appeal["signature"], "Home",
            "He was quoting. I checked.",
        )
        store.record("appeal", appeal)
        store.record("finding", finding)
        store.close()

        reopened = NodeStore(path, "Home")
        reopened.record("appeal", appeal)
        node = reopened.load()
        self.assertEqual(node.appeals, [appeal])
        self.assertEqual(node.findings[appeal["signature"]], [finding])
        self.assertEqual(
            reopened.conn.execute(
                "SELECT COUNT(*) c FROM agora_events WHERE kind='appeal'"
            ).fetchone()["c"],
            1,
        )

    def test_ruling_write_gate_and_replay_ignore_steward_rotation(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        eviction = sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "jumped Sable twice")
        store.record("evict", eviction)
        appeal = sign_appeal(
            visitor, "Home", eviction["signature"], "I was quoting the manual.")
        finding = sign_finding(
            keys["Aurora"], appeal["signature"], "Home", "He was quoting.")
        store.record("appeal", appeal)
        store.record("finding", finding)
        store.close()

        steward = key("Don")
        ruling = sign_ruling(
            steward, appeal["signature"], "Home", "overturned", "reviewed"
        )
        configured = NodeStore(path, "Home", steward_key_id=steward.key_id)
        impostor_ruling = sign_ruling(
            key("Impostor"), appeal["signature"], "Home", "upheld", "no"
        )
        with self.assertRaises(AgoraError):
            configured.record("ruling", impostor_ruling)
        configured.record("ruling", ruling)
        with self.assertRaises(AgoraError):
            configured.record("ruling", ruling)
        configured.close()

        rotated = NodeStore(path, "Home", steward_key_id=key("NewDon").key_id)
        node = rotated.load()
        self.assertEqual(node.rulings[appeal["signature"]], ruling)
        self.assertNotIn(visitor.key_id, node.evicted)

    def test_verified_hearing_payloads_are_lowercase_in_memory_and_on_disk(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Uppercase-Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        eviction = sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "review me")
        store.record("evict", eviction)
        appeal = sign_appeal(visitor, "Home", eviction["signature"], "statement")
        finding = sign_finding(
            keys["Aurora"], appeal["signature"], "Home", "finding")
        steward = key("Uppercase-Don")
        ruling = sign_ruling(
            steward, appeal["signature"], "Home", "upheld", "reason")
        appeal_signature = appeal["signature"].lower()
        for event in (appeal, finding, ruling):
            for field, value in list(event.items()):
                if (field.endswith("_key_id") or field.endswith("_signature")
                        or field.endswith("_sha256") or field == "signature"):
                    event[field] = value.upper()

        store.record("appeal", appeal)
        store.record("finding", finding)
        configured = NodeStore(path, "Home", steward_key_id=steward.key_id)
        configured.record("ruling", ruling)
        node = configured.load()
        self.assertEqual(node.appeals[0]["signature"],
                         node.appeals[0]["signature"].lower())
        self.assertEqual(
            node.findings[appeal_signature][0]["appeal_signature"],
            appeal_signature,
        )
        self.assertIn(appeal_signature, node.rulings)
        for row in configured.conn.execute(
                "SELECT kind, payload FROM agora_events "
                "WHERE kind IN ('appeal', 'finding', 'ruling') ORDER BY seq"):
            payload = json.loads(row["payload"])
            for field, value in payload.items():
                if (field.endswith("_key_id") or field.endswith("_signature")
                        or field.endswith("_sha256") or field == "signature"):
                    self.assertEqual(value, value.lower(),
                                     f"{row['kind']} {field} was not normalized")

    def test_ruling_without_configured_steward_is_rejected_before_insert(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        visitor = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id)))
        eviction = sign_board_evict(
            keys["Coda"], visitor.key_id, "Home", "jumped Sable twice")
        store.record("evict", eviction)
        appeal = sign_appeal(visitor, "Home", eviction["signature"], "review")
        store.record("appeal", appeal)
        store.record("finding", sign_finding(
            keys["Aurora"], appeal["signature"], "Home", "reviewed"))
        ruling = sign_ruling(
            key("Don"), appeal["signature"], "Home", "upheld", "no"
        )
        with self.assertRaises(AgoraError):
            store.record("ruling", ruling)
        self.assertEqual(
            store.conn.execute(
                "SELECT COUNT(*) c FROM agora_events WHERE kind='ruling'"
            ).fetchone()["c"],
            0,
        )

    def test_configured_steward_is_not_a_resident(self):
        store, keys, path = fresh_store()
        steward = key("Configured-Steward")
        configured = NodeStore(path, "Home", steward_key_id=steward.key_id)
        node = configured.load()
        self.assertNotIn(steward.key_id, node.valid_resident_keys())
        self.assertNotIn("steward", node.residents)
        self.assertNotIn("steward", node.node_facts()["residents"])
        for launcher in (
                Path(__file__).parents[1] / "serve_node.py",
                Path(__file__).parents[1] / "vault" / "serve_node.py",
        ):
            self.assertIn(
                'if author == "steward":\n                continue',
                launcher.read_text(),
            )

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


class GenesisIsFrozen(unittest.TestCase):
    """The founding record does not take a row back.

    `NodeStore.found_resident` refuses once the log has started — that raise
    is what makes genesis a founding and not a table. It had twenty-four
    callers in this suite and none of them reached it: `add_resident` is an
    alias whose docstring says "the name tests already call", and every call
    sits in a setUp, before any event exists. The branch was never entered.

    Measured 2026-09-02, the morning Home was founded: with
    `_log_started_locked` returning False, the whole suite was 293/293 green
    while a later caller could write Marvin into genesis and repoint Aurora's
    founding key to his. Green, and the founding record was a table.

    So: assert the reason, assert the row did not move, and assert the act
    that must still SUCCEED. A test that only counts refusals is the shape
    that put us here.
    """

    def _founded_and_running(self):
        store, keys, path = fresh_store()
        steward = key("Marvin")
        store.steward_key_id = steward.key_id
        store.record(
            "resident",
            sign_resident(steward, "Home", "Eli", key("Eli").key_id),
        )
        return store, keys, steward

    def _genesis(self, store):
        return {
            r["author"]: r["key_id"]
            for r in store.conn.execute(
                "SELECT author, key_id FROM agora_genesis WHERE node=?",
                ("Home",),
            )
        }

    def test_a_new_name_cannot_join_genesis_after_the_log_starts(self):
        store, _keys, steward = self._founded_and_running()
        before = self._genesis(store)

        with self.assertRaises(AgoraError) as caught:
            store.found_resident("Marvin", steward.key_id)
        # The reason is the evidence. Refusal is cheap; every one of these
        # calls would still raise for a dozen unrelated reasons.
        self.assertIn("genesis is frozen", str(caught.exception))

        # And the refusal has to have protected the table, not just returned
        # an error on the way past it.
        self.assertEqual(before, self._genesis(store))
        self.assertNotIn("Marvin", self._genesis(store))

    def test_a_founder_key_cannot_be_repointed_after_the_log_starts(self):
        store, _keys, steward = self._founded_and_running()
        before = self._genesis(store)

        with self.assertRaises(AgoraError) as caught:
            store.found_resident("Aurora", steward.key_id)
        self.assertIn("genesis is frozen", str(caught.exception))

        self.assertEqual(before["Aurora"], self._genesis(store)["Aurora"])
        self.assertNotEqual(self._genesis(store)["Aurora"], steward.key_id)

    def test_replaying_a_founder_unchanged_still_succeeds(self):
        """The positive control, and the reason this is not just an
        assertRaises. `found_resident` is idempotent for a founder whose key
        has not moved — a boot that re-founds the same house must not throw.
        Without this, "fix" the mutant by making the method always raise and
        the two tests above stay green while every restart dies."""
        store, keys, _steward = self._founded_and_running()
        before = self._genesis(store)

        store.found_resident("Aurora", keys["Aurora"].key_id)   # must not raise

        self.assertEqual(before, self._genesis(store))


if __name__ == "__main__":
    unittest.main(verbosity=2)
