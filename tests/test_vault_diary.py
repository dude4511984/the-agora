"""Tests for vault_diary against a copy of the real 36k-row vault."""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one

# ── fixture guard ───────────────────────────────────────────────────────────
# These tests run against `tests/dev.db`, a copy of the real 36k-row vault.
# It is not in the repo and should not be: it is the Kin's actual memories, and
# committing them as a test fixture is a consent decision nobody has made.
#
# Without the guard this module raised ImportError at COLLECTION time, so
# `unittest discover` reported it as an error and it counted as neither passing
# nor failing. Three modules had been in that state since 2026-08-21 -- coverage
# that looked like coverage and executed nothing. Skip loudly instead.
#
# To enable:  sqlite3 <the live vault> ".backup 'tests/dev.db'"    (never cp --
#             the data is in the WAL; see revelations 2026-08-26)
import unittest as _ut
if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev.db")):
    raise _ut.SkipTest("tests/dev.db absent - see the fixture guard at the top of this file")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vault"))  # vault_diary lives here

import vault_diary  # noqa: E402
from kin_diary.canonical import entry_canonical, content_sha256  # noqa: E402
from kin_diary.keys import generate_keypair, load_current  # noqa: E402
from kin_diary.sign import verify_entry  # noqa: E402

REAL_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev.db")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        shutil.copy(REAL_DB, self.db)
        self.conn = sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        self.keys = Path(self.tmp) / "keys"
        self._orig_root = None

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use_temp_keys(self):
        """Point kin_diary's default key root at the sandbox, not ~/.config."""
        import kin_diary.keys as K
        self._orig_root = K.DEFAULT_KEYS_ROOT
        K.DEFAULT_KEYS_ROOT = self.keys
        self.addCleanup(setattr, K, "DEFAULT_KEYS_ROOT", self._orig_root)


class TestSchema(Base):
    def test_adds_three_columns_and_is_idempotent(self):
        added = vault_diary.ensure_schema(self.conn)
        self.assertEqual(sorted(added), ["content_sha256", "key_id", "signature"])
        again = vault_diary.ensure_schema(self.conn)
        self.assertEqual(again, [], "second run must add nothing")

    def test_migration_does_not_touch_existing_rows(self):
        before = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0) FROM memories").fetchone()
        vault_diary.ensure_schema(self.conn)
        after = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0) FROM memories").fetchone()
        self.assertEqual(tuple(before), tuple(after))

    def test_historical_rows_stay_unsigned(self):
        vault_diary.ensure_schema(self.conn)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE signature IS NOT NULL").fetchone()[0]
        self.assertEqual(n, 0, "migration must not backfill signatures")

    def test_existing_queries_still_work(self):
        """The endpoints select *; adding columns must not break them."""
        vault_diary.ensure_schema(self.conn)
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE visibility='shared' "
            "AND content LIKE ? ORDER BY timestamp DESC LIMIT 3", ("%forge%",)).fetchall()
        self.assertTrue(rows)
        self.assertIn("content_sha256", rows[0].keys())


class TestSigning(Base):
    def test_known_mind_gets_a_real_signature(self):
        self.use_temp_keys()
        sha, kid, sig = vault_diary.sign_row(
            "Eli", "2026-08-21T10:00:00", "wander", "the forge is warm", "", "", "")
        self.assertEqual(len(sha), 64)
        self.assertEqual(len(kid), 64)
        self.assertEqual(len(sig), 128)
        verify_entry({
            "author": "Eli", "timestamp": "2026-08-21T10:00:00", "layer": "wander",
            "source": "", "domain": "", "tags": "", "content": "the forge is warm",
            "content_sha256": sha, "key_id": kid, "signature": sig,
        })

    def test_signature_matches_spec_canonical_bytes(self):
        """Guard against drift between this module and kin_diary's format."""
        self.use_temp_keys()
        entry = dict(author="Coda", timestamp="2026-08-21T11:22:33", layer="episodic",
                     content="brothers", tags="a,b", source="s", domain="d")
        sha, kid, sig = vault_diary.sign_row(**entry)
        canon = entry_canonical({**entry, "content_sha256": content_sha256("brothers")})
        from kin_diary.keys import load_public
        load_public(kid).verify(bytes.fromhex(sig), canon)

    def test_telemetry_authors_are_not_signed(self):
        self.use_temp_keys()
        for author in ("pulse_Frosty", "reflect_FrostyMcFastboi", "System", "TestKin"):
            self.assertEqual(
                vault_diary.sign_row(author, "t", "heartbeat", "Load 1.99", "", "", ""),
                (None, None, None), f"{author} must not get a signing identity")

    def test_signing_failure_degrades_to_unsigned(self):
        """Rule 2: a broken signer must not cost us the memory."""
        self.use_temp_keys()
        import kin_diary.sign as S
        orig = S.sign_entry
        S.sign_entry = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, S, "sign_entry", orig)
        with self.assertLogs("vault_diary", level="ERROR"):
            self.assertEqual(
                vault_diary.sign_row("Eli", "t", "wander", "c", "", "", ""),
                (None, None, None))

    def test_first_write_generates_a_key(self):
        self.use_temp_keys()
        with self.assertRaises(FileNotFoundError):
            load_current("Aurora")
        vault_diary.sign_row("Aurora", "t", "wander", "c", "", "", "")
        self.assertEqual(len(load_current("Aurora").key_id), 64)

    def test_same_mind_reuses_one_key(self):
        self.use_temp_keys()
        _, k1, _ = vault_diary.sign_row("Lumen", "t1", "wander", "a", "", "", "")
        _, k2, _ = vault_diary.sign_row("Lumen", "t2", "wander", "b", "", "", "")
        self.assertEqual(k1, k2)

    def test_none_fields_normalise_not_crash(self):
        self.use_temp_keys()
        sha, kid, sig = vault_diary.sign_row("Bong", "t", None, "c", None, None, None)
        self.assertIsNotNone(sig)

    def test_roster_is_configurable(self):
        self.use_temp_keys()
        os.environ["VAULT_DIARY_MINDS"] = "Zephyr"
        self.addCleanup(os.environ.pop, "VAULT_DIARY_MINDS", None)
        self.assertEqual(vault_diary.sign_row("Eli", "t", "w", "c", "", "", "")[2], None)
        self.assertIsNotNone(vault_diary.sign_row("Zephyr", "t", "w", "c", "", "", "")[2])


class TestEndToEnd(Base):
    def test_insert_then_verify_from_the_row(self):
        """The real path: sign, INSERT, read back, verify from stored columns."""
        self.use_temp_keys()
        ts = "2026-08-21T12:00:00.123456"
        sha, kid, sig = vault_diary.sign_row(
            "Crungus", ts, "wander", "the cathedral stands", "rot", "wander", "shop")
        vault_diary.ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO memories (author,timestamp,layer,content,tags,visibility,"
            "source,domain,tier,salience,content_sha256,key_id,signature) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("Crungus", ts, "wander", "the cathedral stands", "rot", "shared",
             "wander", "shop", "dynamic", 0.5, sha, kid, sig))
        self.conn.commit()
        r = self.conn.execute(
            "SELECT * FROM memories WHERE signature IS NOT NULL").fetchone()
        verify_entry({
            "author": r["author"], "timestamp": r["timestamp"], "layer": r["layer"],
            "source": r["source"], "domain": r["domain"], "tags": r["tags"],
            "content": r["content"],
            "content_sha256": r["content_sha256"], "key_id": r["key_id"],
            "signature": r["signature"],
        })
        self.assertEqual(r["content_sha256"], content_sha256("the cathedral stands"))

    def test_curation_does_not_break_the_signature(self):
        """tier/visibility are unsigned on purpose — promoting must stay valid."""
        self.use_temp_keys()
        ts = "2026-08-21T12:30:00"
        sha, kid, sig = vault_diary.sign_row("Eli", ts, "core", "I am a relationship",
                                             "", "", "")
        vault_diary.ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO memories (author,timestamp,layer,content,tags,visibility,"
            "source,domain,tier,content_sha256,key_id,signature) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("Eli", ts, "core", "I am a relationship", "", "shared", "", "",
             "dynamic", sha, kid, sig))
        self.conn.execute("UPDATE memories SET tier='anchor', visibility='private' "
                          "WHERE signature=?", (sig,))
        self.conn.commit()
        r = self.conn.execute("SELECT * FROM memories WHERE signature=?", (sig,)).fetchone()
        self.assertEqual(r["tier"], "anchor")
        verify_entry({
            "author": r["author"], "timestamp": r["timestamp"], "layer": r["layer"],
            "source": r["source"], "domain": r["domain"], "tags": r["tags"],
            "content": r["content"],
            "content_sha256": r["content_sha256"], "key_id": r["key_id"],
            "signature": r["signature"],
        })



class TestReviewFindings(Base):
    """Regression tests for the qwen review pass, 2026-08-21."""

    def test_non_str_timestamp_is_refused_not_stringified(self):
        """A datetime must never be silently str()'d into the signature."""
        self.use_temp_keys()
        import datetime as dt
        with self.assertLogs("vault_diary", level="ERROR"):
            out = vault_diary.sign_row(
                "Eli", dt.datetime(2026, 8, 21), "wander", "c", "", "", "")
        self.assertEqual(out, (None, None, None))

    def test_falsy_but_present_values_are_not_zeroed(self):
        self.assertEqual(vault_diary._field("tags", ""), "")
        self.assertEqual(vault_diary._field("tags", None), "")
        with self.assertRaises(TypeError):
            vault_diary._field("tags", 0)

    def test_concurrent_first_write_still_signs(self):
        """Loser of the keygen race must load the winner's key, not go unsigned."""
        self.use_temp_keys()
        import kin_diary.keys as K
        real = K.generate_keypair
        calls = {"n": 0}

        def racy(author, keys_root=None, now_ms=None):
            calls["n"] += 1
            if calls["n"] == 1:
                real(author, keys_root, now_ms)      # the "other writer" wins
            raise FileExistsError("lost the race")

        K.generate_keypair = racy
        self.addCleanup(setattr, K, "generate_keypair", real)
        sha, kid, sig = vault_diary.sign_row("Eli", "t", "wander", "c", "", "", "")
        self.assertIsNotNone(sig, "race loser must still get a signature")
        self.assertEqual(kid, K.load_current("Eli").key_id)

    def test_control_chars_in_tags_degrade_to_unsigned_and_log(self):
        """The library rejects them; we must not store a bogus signature."""
        self.use_temp_keys()
        with self.assertLogs("vault_diary", level="ERROR"):
            out = vault_diary.sign_row("Eli", "t", "wander", "c", "a\nb", "", "")
        self.assertEqual(out, (None, None, None))

    def test_nfc_is_applied_by_the_library(self):
        """Decomposed and composed forms must hash identically."""
        self.use_temp_keys()
        nfd, nfc_ = "écho", "écho"
        a = vault_diary.sign_row("Eli", "t", "w", nfd, "", "", "")
        b = vault_diary.sign_row("Eli", "t", "w", nfc_, "", "", "")
        self.assertEqual(a[0], b[0], "NFC normalisation must make these equal")
        self.assertEqual(a[2], b[2])


class TestHealth(Base):
    def test_selftest_passes_and_never_touches_the_db(self):
        self.use_temp_keys()
        before = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertTrue(vault_diary.selftest()["ok"])
        after = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertEqual(before, after)

    def test_selftest_author_is_not_a_mind(self):
        self.assertNotIn("__selftest__", vault_diary.minds())

    def test_health_reports_missing_keys(self):
        self.use_temp_keys()
        h = vault_diary.health()
        self.assertEqual(sorted(h["minds_without_keys"]), sorted(vault_diary.minds()))
        vault_diary.sign_row("Eli", "t", "w", "c", "", "", "")
        self.assertNotIn("Eli", vault_diary.health()["minds_without_keys"])

    def test_health_goes_not_ok_after_a_real_failure(self):
        self.use_temp_keys()
        vault_diary.STATS.update(signed=0, unsigned_by_policy=0, failed=0, last_error=None)
        self.addCleanup(vault_diary.STATS.update,
                        {"signed": 0, "unsigned_by_policy": 0, "failed": 0, "last_error": None})
        self.assertTrue(vault_diary.health()["ok"])
        with self.assertLogs("vault_diary", level="ERROR"):
            vault_diary.sign_row("Eli", 12345, "w", "c", "", "", "")   # non-str ts
        h = vault_diary.health()
        self.assertFalse(h["ok"], "a swallowed failure must still show up here")
        self.assertEqual(h["stats"]["failed"], 1)
        self.assertIn("TypeError", h["stats"]["last_error"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
