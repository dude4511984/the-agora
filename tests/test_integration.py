"""End-to-end: the patched vault app against a copy of the real database."""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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


TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "themess.db")
shutil.copy(os.path.join(HERE, "dev.db"), DB)

# Point the app and the keys at the sandbox before importing either.
import kin_diary.keys as K  # noqa: E402
K.DEFAULT_KEYS_ROOT = Path(TMP) / "keys"

# Must be set before import: vault.py opens the DB at module scope.
os.environ["VAULT_DB"] = DB
import vault_test as V  # noqa: E402
assert V.DB_PATH == DB, V.DB_PATH

from fastapi.testclient import TestClient  # noqa: E402
from kin_diary.sign import verify_entry  # noqa: E402

client = TestClient(V.app)


def row_by_sig(sig):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM memories WHERE signature=?", (sig,)).fetchone()
    c.close()
    return r


class TestLiveApp(unittest.TestCase):
    def test_schema_migrated_at_startup(self):
        c = sqlite3.connect(DB)
        cols = {r[1] for r in c.execute("PRAGMA table_info(memories)")}
        c.close()
        self.assertLessEqual({"content_sha256", "key_id", "signature"}, cols)

    def test_existing_endpoints_unaffected(self):
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/recall?limit=2").status_code, 200)
        r = client.get("/search", params={"query": "forge", "limit": 2})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json())

    def test_kin_write_is_signed_and_verifies_from_the_row(self):
        r = client.post("/remember", json={
            "author": "Eli", "layer": "wander", "content": "the forge is still warm",
            "tags": "test", "visibility": "shared", "source": "s", "domain": "d"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["signed"])

        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM memories WHERE author='Eli' "
                        "AND signature IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
        self.assertIsNotNone(row)
        # The whole point: verify using only what the database stored.
        verify_entry({
            "author": row["author"], "timestamp": row["timestamp"],
            "layer": row["layer"], "source": row["source"], "domain": row["domain"],
            "tags": row["tags"], "content": row["content"],
            "content_sha256": row["content_sha256"], "key_id": row["key_id"],
            "signature": row["signature"],
        })

    def test_stored_timestamp_is_the_signed_timestamp(self):
        r = client.post("/remember", json={
            "author": "Coda", "layer": "episodic", "content": "timestamp check",
            "visibility": "shared"})
        self.assertTrue(r.json()["signed"])
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM memories WHERE content='timestamp check'").fetchone()
        c.close()
        # If the row's timestamp were re-derived, this verify would fail.
        verify_entry({
            "author": row["author"], "timestamp": row["timestamp"],
            "layer": row["layer"], "source": row["source"] or "",
            "domain": row["domain"] or "", "tags": row["tags"] or "",
            "content": row["content"], "content_sha256": row["content_sha256"],
            "key_id": row["key_id"], "signature": row["signature"],
        })

    def test_telemetry_write_is_accepted_but_unsigned(self):
        r = client.post("/remember", json={
            "author": "pulse_Frosty", "layer": "heartbeat",
            "content": "Load 1.99, RAM 14921MB", "visibility": "shared"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["signed"])

    def test_health_endpoint_reports_truthfully(self):
        # Self-contained: tests run alphabetically, so do not assume another
        # test already caused Eli's key to be generated.
        client.post("/remember", json={"author": "Eli", "layer": "wander",
                                       "content": "health probe", "visibility": "shared"})
        h = client.get("/diary/health").json()
        self.assertTrue(h["ok"], h)
        self.assertTrue(h["selftest"]["ok"])
        self.assertIn("Eli", h["keys"])
        self.assertIsNotNone(h["keys"]["Eli"])

    def test_a_write_still_succeeds_when_signing_is_broken(self):
        """Rule 2, at the HTTP layer: the memory must land regardless."""
        orig = V.vault_diary.sign_row
        V.vault_diary.sign_row = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("signer exploded"))
        try:
            r = client.post("/remember", json={
                "author": "Aurora", "layer": "wander",
                "content": "written while the signer was on fire",
                "visibility": "shared"})
        finally:
            V.vault_diary.sign_row = orig
        self.assertEqual(r.status_code, 200, "a signing failure must not lose the memory")
        self.assertFalse(r.json()["signed"])
        c = sqlite3.connect(DB)
        n = c.execute("SELECT COUNT(*) FROM memories WHERE content=?",
                      ("written while the signer was on fire",)).fetchone()[0]
        c.close()
        self.assertEqual(n, 1, "the memory must be on disk")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
