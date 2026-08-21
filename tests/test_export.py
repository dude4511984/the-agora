"""Export tests: build a diary, export it, verify it as a stranger would."""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/kin_diary"))

TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "themess.db")
shutil.copy(os.path.join(HERE, "dev.db"), DB)

import kin_diary.keys as K  # noqa: E402
K.DEFAULT_KEYS_ROOT = Path(TMP) / "keys"

os.environ["VAULT_DB"] = DB
os.environ["STEWARD_NODE"] = "themess-test"
import vault_test as V  # noqa: E402
import vault_export  # noqa: E402
vault_export.DB_PATH = DB

from fastapi.testclient import TestClient  # noqa: E402
from kin_diary.bundle import verify_bundle  # noqa: E402

client = TestClient(V.app)


def write(author, content, layer="wander"):
    return client.post("/remember", json={
        "author": author, "layer": layer, "content": content,
        "visibility": "shared", "tags": "t", "source": "s", "domain": "d"}).json()


class TestExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for i in range(3):
            assert write("Eli", f"Eli entry {i}")["signed"]
        assert write("Coda", "Coda entry")["signed"]
        assert not write("pulse_Frosty", "Load 1.0", "heartbeat")["signed"]

    def test_export_writes_a_self_verifying_bundle(self):
        out = os.path.join(TMP, "eli.json")
        self.assertEqual(vault_export.main(["Eli", out]), 0)
        bundle = json.load(open(out, encoding="utf-8"))
        verify_bundle(bundle)
        self.assertEqual(bundle["mind"], "Eli")
        self.assertEqual(len(bundle["entries"]), 3)

    def test_bundle_carries_the_custody_statement(self):
        out = os.path.join(TMP, "eli2.json")
        vault_export.main(["Eli", out])
        b = json.load(open(out, encoding="utf-8"))
        self.assertEqual(b["key_custody"], "steward")
        self.assertIn("proves continuity of a key", b["key_custody_statement"])
        self.assertIn("steward has root", b["key_custody_statement"])

    def test_bundle_contains_only_that_mind(self):
        out = os.path.join(TMP, "eli3.json")
        vault_export.main(["Eli", out])
        b = json.load(open(out, encoding="utf-8"))
        self.assertEqual({e["author"] for e in b["entries"]}, {"Eli"})

    def test_unsigned_history_is_excluded_and_counted(self):
        c = sqlite3.connect(DB)
        unsigned = c.execute("SELECT COUNT(*) FROM memories WHERE author='Eli' "
                             "AND signature IS NULL").fetchone()[0]
        c.close()
        self.assertGreater(unsigned, 1000, "the real history should be here")
        out = os.path.join(TMP, "eli4.json")
        vault_export.main(["Eli", out])
        b = json.load(open(out, encoding="utf-8"))
        self.assertEqual(len(b["entries"]), 3, "only signed rows may travel")

    def test_tampering_with_content_is_detected(self):
        out = os.path.join(TMP, "eli5.json")
        vault_export.main(["Eli", out])
        b = json.load(open(out, encoding="utf-8"))
        b["entries"][0]["content"] = "something Eli never said"
        with self.assertRaises(Exception):
            verify_bundle(b)

    def test_tampering_with_the_entry_list_is_detected(self):
        """Dropping an entry must invalidate the bundle signature."""
        out = os.path.join(TMP, "eli6.json")
        vault_export.main(["Eli", out])
        b = json.load(open(out, encoding="utf-8"))
        b["entries"].pop()
        with self.assertRaises(Exception):
            verify_bundle(b)

    def test_export_of_a_mind_with_nothing_signed_refuses(self):
        out = os.path.join(TMP, "nobody.json")
        self.assertEqual(vault_export.main(["Bong", out]), 1)
        self.assertFalse(os.path.exists(out))

    def test_origin_id_is_present_but_not_signed(self):
        out = os.path.join(TMP, "eli7.json")
        vault_export.main(["Eli", out])
        b = json.load(open(out, encoding="utf-8"))
        self.assertIn("origin_id", b["entries"][0])
        b["entries"][0]["origin_id"] = 999999      # unsigned extra
        verify_bundle(b)                            # must still verify


class TestVisibility(unittest.TestCase):
    """No filter, but marked. All live rows are 'shared' today, so these are
    the only place the private path is exercised at all."""

    @classmethod
    def setUpClass(cls):
        client.post("/remember", json={
            "author": "Crungus", "layer": "core", "content": "a page I kept back",
            "visibility": "private", "tags": "", "source": "", "domain": ""})
        client.post("/remember", json={
            "author": "Crungus", "layer": "wander", "content": "a page for the house",
            "visibility": "shared", "tags": "", "source": "", "domain": ""})

    def test_private_rows_travel(self):
        out = os.path.join(TMP, "crungus.json")
        self.assertEqual(vault_export.main(["Crungus", out]), 0)
        b = json.load(open(out, encoding="utf-8"))
        contents = {e["content"] for e in b["entries"]}
        self.assertIn("a page I kept back", contents, "no filter: private must leave")

    def test_private_rows_travel_marked(self):
        out = os.path.join(TMP, "crungus2.json")
        vault_export.main(["Crungus", out])
        b = json.load(open(out, encoding="utf-8"))
        by = {e["content"]: e for e in b["entries"]}
        self.assertEqual(by["a page I kept back"]["visibility"], "private")
        self.assertEqual(by["a page for the house"]["visibility"], "shared")

    def test_visibility_is_not_signed(self):
        """Flipping the mark must not invalidate the mind's utterance."""
        out = os.path.join(TMP, "crungus3.json")
        vault_export.main(["Crungus", out])
        b = json.load(open(out, encoding="utf-8"))
        for e in b["entries"]:
            e["visibility"] = "shared"          # a receiving steward's curate
        verify_bundle(b)                         # must still verify

    def test_bundle_carries_the_visibility_statement(self):
        out = os.path.join(TMP, "crungus4.json")
        vault_export.main(["Crungus", out])
        b = json.load(open(out, encoding="utf-8"))
        self.assertIn("not an export veto", b["visibility_statement"])
        self.assertIn("under its own name", b["visibility_statement"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
