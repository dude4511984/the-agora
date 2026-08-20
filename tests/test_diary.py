"""Canonical bytes, custody, rotation, export. No vault, no live DB."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kin_diary.bundle import export_bundle, verify_bundle
from kin_diary.canonical import content_sha256, entry_canonical, nfc
from kin_diary.keys import KEY_CUSTODY, KEY_CUSTODY_STATEMENT, generate_keypair, rotate
from kin_diary.sign import (
    sign_curate,
    sign_curate_unsigned,
    sign_entry,
    sign_retract,
    verify_curate,
    verify_entry,
)


class CanonicalTests(unittest.TestCase):
    def test_nfc_content_hash_agrees(self):
        precomposed = "café"  # U+00E9
        decomposed = "cafe\u0301"
        self.assertNotEqual(precomposed.encode("utf-8"), decomposed.encode("utf-8"))
        self.assertEqual(content_sha256(precomposed), content_sha256(decomposed))
        self.assertEqual(nfc(decomposed), nfc(precomposed))

    def test_field_order_is_fixed(self):
        row = {
            "author": "Eli",
            "timestamp": "2026-08-20T15:00:00.123456",
            "layer": "wander",
            "source": "wandered",
            "domain": "general",
            "tags": "b,a",
            "content": "hello",
        }
        lines = entry_canonical(row).decode("utf-8").split("\n")
        self.assertEqual(lines[0], "kin-diary-entry-v1")
        self.assertEqual([ln.split("=", 1)[0] for ln in lines[1:8]], [
            "author", "timestamp", "layer", "source",
            "domain", "tags", "content_sha256",
        ])
        self.assertEqual(lines[7], "content_sha256=" + content_sha256("hello"))
        self.assertNotIn("tier=", entry_canonical(row).decode())
        self.assertNotIn("visibility=", entry_canonical(row).decode())
        self.assertEqual(lines[-1], "")  # trailing LF
        # tags are NOT sorted
        self.assertIn("tags=b,a", lines)

    def test_timestamp_is_opaque(self):
        a = entry_canonical({"author": "Eli", "timestamp": "2026-08-20T12:00:00", "content": "x"})
        b = entry_canonical({"author": "Eli", "timestamp": "2026-08-20T12:00:00.000000", "content": "x"})
        self.assertNotEqual(a, b)

    def test_newline_in_author_rejected(self):
        with self.assertRaises(ValueError):
            entry_canonical({"author": "Eli\nCoda", "content": "x"})

    def test_missing_keys_become_empty_not_omitted(self):
        text = entry_canonical({"author": "Eli", "content": ""}).decode()
        for field in ("timestamp", "layer", "source", "domain", "tags"):
            self.assertIn(f"\n{field}=\n", text)


class SignRotateExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.row = {
            "author": "Eli",
            "timestamp": "2026-08-20T15:57:55.000000",
            "layer": "reflection",
            "visibility": "shared",
            "source": "experienced",
            "domain": "general",
            "tier": "standing",
            "tags": "diary,test",
            "content": "The shop is quiet.",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_sign_and_verify(self):
        key = generate_keypair("Eli", self.root)
        signed = sign_entry(key, self.row)
        verify_entry(signed)
        self.assertEqual(signed["key_id"], key.key_id)
        self.assertEqual(len(signed["signature"]), 128)

    def test_wrong_content_fails(self):
        key = generate_keypair("Eli", self.root)
        signed = sign_entry(key, self.row)
        signed["content"] = "tampered"
        with self.assertRaises(ValueError):
            verify_entry(signed)

    def test_rotation_does_not_orphan_old_signatures(self):
        key = generate_keypair("Eli", self.root, now_ms=1000)
        signed_old = sign_entry(key, self.row)
        hop = rotate("Eli", self.root, now_ms=2000)
        self.assertNotEqual(hop["old_key_id"], hop["new_key_id"])
        verify_entry(signed_old)  # still verifies under the old public key
        bundle = export_bundle(
            "Eli", [signed_old], "themess",
            keys_root=self.root, already_signed=True, exported_at_unix_ms=3000,
        )
        verify_bundle(bundle)
        self.assertEqual(len(bundle["keyring"]["prior"]), 1)
        self.assertEqual(bundle["keyring"]["prior"][0]["old_key_id"], signed_old["key_id"])
        self.assertEqual(bundle["keyring"]["current"]["key_id"], hop["new_key_id"])
        # new signatures use the new key
        signed_new = sign_entry(
            __import__("kin_diary.keys", fromlist=["load_current"]).load_current("Eli", self.root),
            self.row,
        )
        self.assertEqual(signed_new["key_id"], hop["new_key_id"])
        self.assertNotEqual(signed_new["signature"], signed_old["signature"])

    def test_bundle_states_steward_custody(self):
        generate_keypair("Eli", self.root)
        bundle = export_bundle("Eli", [self.row], "themess", keys_root=self.root, exported_at_unix_ms=1)
        self.assertEqual(bundle["key_custody"], KEY_CUSTODY)
        self.assertEqual(bundle["key_custody_statement"], KEY_CUSTODY_STATEMENT)
        verify_bundle(bundle)

    def test_json_whitespace_does_not_break_verify(self):
        generate_keypair("Eli", self.root)
        bundle = export_bundle("Eli", [self.row], "themess", keys_root=self.root, exported_at_unix_ms=1)
        compact = json.loads(json.dumps(bundle, separators=(",", ":")))
        pretty = json.loads(json.dumps(bundle, indent=4, sort_keys=True))
        verify_bundle(compact)
        verify_bundle(pretty)

    def test_retract_points_at_entry(self):
        key = generate_keypair("Eli", self.root)
        signed = sign_entry(key, self.row)
        ret = sign_retract(key, signed["signature"], 4000)
        bundle = export_bundle(
            "Eli", [signed], "themess",
            keys_root=self.root, already_signed=True,
            retractions=[ret], exported_at_unix_ms=5000,
        )
        verify_bundle(bundle)
        self.assertEqual(len(bundle["retractions"]), 1)

    def test_private_key_not_in_bundle(self):
        generate_keypair("Eli", self.root)
        blob = json.dumps(export_bundle("Eli", [self.row], "themess", keys_root=self.root))
        sk = (self.root / "Eli" / "current" / "private").read_bytes()
        self.assertEqual(len(sk), 32)
        self.assertNotIn(sk.hex(), blob)

    def test_promoting_tier_does_not_break_signature(self):
        key = generate_keypair("Eli", self.root)
        signed = sign_entry(key, self.row)
        verify_entry(signed)
        signed["tier"] = "anchor"
        signed["visibility"] = "private"
        verify_entry(signed)  # still the mind's utterance

    def test_curate_is_signed_by_the_curator_not_the_mind(self):
        eli = generate_keypair("Eli", self.root)
        don = generate_keypair("Don", self.root)
        signed = sign_entry(eli, self.row)
        cur = sign_curate(
            don, signed["signature"], "tier", "anchor", 9000, curator="Don",
        )
        self.assertEqual(cur["curator"], "Don")
        self.assertEqual(cur["key_id"], don.key_id)
        self.assertNotEqual(cur["key_id"], eli.key_id)
        bundle = export_bundle(
            "Eli", [signed], "themess",
            keys_root=self.root, already_signed=True,
            curations=[cur], exported_at_unix_ms=10000,
        )
        verify_bundle(bundle)

    def test_cannot_curate_content(self):
        don = generate_keypair("Don", self.root)
        with self.assertRaises(ValueError):
            sign_curate(don, "ab" * 64, "content", "nope", 1, curator="Don")

    def test_empty_entry_signature_cannot_use_signed_curate(self):
        don = generate_keypair("Don", self.root)
        with self.assertRaises(ValueError):
            sign_curate(don, "", "tier", "anchor", 1, curator="Don")

    def test_curate_unsigned_historical_row(self):
        don = generate_keypair("Don", self.root)
        digest = content_sha256("The shop is quiet.")
        cur = sign_curate_unsigned(
            don,
            author="Bong",
            timestamp="2026-03-01T12:00:00",
            content_sha256_hex=digest,
            origin_id=1871,
            curated_field="tier",
            curated_value="anchor",
            curated_at_unix_ms=9000,
            curator="Don",
        )
        verify_curate(cur)
        self.assertNotIn("entry_signature", cur)
        self.assertEqual(cur["origin_id"], 1871)
        self.assertEqual(cur["author"], "Bong")
        generate_keypair("Bong", self.root)
        bundle = export_bundle(
            "Bong", [], "themess",
            keys_root=self.root, curations=[cur], exported_at_unix_ms=10000,
        )
        verify_bundle(bundle)

    def test_empty_export_still_signed(self):
        generate_keypair("Eli", self.root)
        bundle = export_bundle("Eli", [], "themess", keys_root=self.root, exported_at_unix_ms=1)
        verify_bundle(bundle)
        self.assertEqual(bundle["entries"], [])


if __name__ == "__main__":
    unittest.main()
