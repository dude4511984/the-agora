"""Content-addressed artifact retrieval tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import home_node, key, seat

from kin_diary.agora import RING_READ, RING_WRITE, sign_board_evict, sign_board_grant
from kin_diary.agora.artifacts import (
    ArtifactAccessDenied,
    ArtifactHashMismatch,
    ArtifactListingError,
    ArtifactStore,
    ArtifactTooLarge,
    ArtifactUnknownHash,
)
from kin_diary.agora.places import Atlas, sign_listing, sign_place


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.node, self.keys = home_node()
        seat(self.node, self.keys, "Coda")
        self.atlas = Atlas(self.node)
        self.node_key = key("Home-node")
        self.atlas.add_place(sign_place(
            self.node_key, "stall-1", "Home", "kiosk"
        ))
        self.store = ArtifactStore(Path(tempfile.mkdtemp()) / "artifacts")
        self.visitor = key("Marvin")
        from kin_diary.agora import countersign_key_intro, start_key_intro
        self.node.accept_intro(countersign_key_intro(
            self.keys["Coda"],
            start_key_intro(self.visitor, "Home", self.keys["Coda"].key_id),
        ))
        self.node.accept_grant(sign_board_grant(
            self.keys["Coda"], self.visitor.key_id, "Home",
            "personal:Coda", RING_WRITE,
        ))
        self.data = b"plain bytes, never a command"
        self.digest = self.store.put(self.data)
        self.listing = sign_listing(
            self.keys["Coda"], "artifact-1", "Home", "stall-1",
            "plain artifact", self.data,
        )
        self.atlas.add_listing(self.listing)

    def test_fetch_returns_exact_listed_bytes(self):
        self.assertEqual(
            self.store.fetch(
                self.node, self.visitor.key_id, self.listing, self.atlas,
                access_board="personal:Coda",
            ),
            self.data,
        )
        self.assertEqual(self.listing["artifact_sha256"], self.digest)

    def test_unknown_hash_fails_loudly(self):
        missing = sign_listing(
            self.keys["Coda"], "missing", "Home", "stall-1",
            "missing", b"not stored",
        )
        self.atlas.add_listing(missing)
        with self.assertRaises(ArtifactUnknownHash):
            self.store.fetch(
                self.node, self.visitor.key_id, missing, self.atlas,
                access_board="personal:Coda",
            )

    def test_low_ring_is_denied_before_bytes_are_served(self):
        with self.assertRaises(ArtifactAccessDenied):
            self.store.fetch(
                self.node, key("Stranger").key_id, self.listing, self.atlas,
                access_board="personal:Coda",
            )

    def test_oversized_artifact_is_rejected(self):
        small = ArtifactStore(Path(tempfile.mkdtemp()) / "small", max_bytes=3)
        with self.assertRaises(ArtifactTooLarge):
            small.put(b"four")

    def test_stored_hash_mismatch_fails_loudly(self):
        (self.store.root / self.digest).write_bytes(b"tampered")
        with self.assertRaises(ArtifactHashMismatch):
            self.store.fetch(
                self.node, self.visitor.key_id, self.listing, self.atlas,
                access_board="personal:Coda",
            )

    def test_evicted_seller_cannot_keep_listing_live(self):
        self.node.accept_eviction(sign_board_evict(
            self.keys["Coda"], self.keys["Coda"].key_id, "Home", "seller evicted"
        ))
        with self.assertRaises(ArtifactListingError):
            self.store.fetch(
                self.node, self.visitor.key_id, self.listing, self.atlas,
                access_board="personal:Coda",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
