"""A minted key is written where a person can find and restore it.

The Home Kin's keys lived on the vault server, not their node, and looked lost
for a day because nothing wrote them anywhere obvious. Don's fix: every mint
also appends to a plain, findable file with everything needed to rebuild the
key. These freeze that — including that the backup is genuinely recovery-valid
(the private_hex reconstructs the same key_id) and that a test's custom keys
root never pollutes the real home file.
"""
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from kin_diary.keys import generate_keypair, key_id_of  # noqa: E402


class MintedKeysAreBackedUpFindably(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_"))

    def _mint(self, author):
        return generate_keypair(author, keys_root=self.root)

    def test_a_mint_appends_a_recoverable_block(self):
        rec = self._mint("Kestrel")
        bak = self.root / "agora_keys.txt"
        self.assertTrue(bak.exists())
        txt = bak.read_text()
        self.assertIn(f"author=Kestrel", txt)
        self.assertIn(f"key_id={rec.key_id}", txt)
        self.assertIn("private_hex=", txt)
        self.assertIn("public_hex=", txt)

    def test_the_backup_is_private_600(self):
        self._mint("Kestrel")
        bak = self.root / "agora_keys.txt"
        self.assertEqual(stat.S_IMODE(os.stat(bak).st_mode), 0o600)

    def test_the_private_hex_reconstructs_the_same_key(self):
        """A backup that cannot restore the key is decoration. The recorded
        private material must rebuild the exact same identity."""
        rec = self._mint("Kestrel")
        txt = (self.root / "agora_keys.txt").read_text()
        priv_hex = re.search(r"private_hex=([0-9a-f]+)", txt).group(1)
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
        self.assertEqual(key_id_of(sk.public_key().public_bytes_raw()), rec.key_id)

    def test_each_mint_appends_rather_than_overwrites(self):
        self._mint("Kestrel")
        self._mint("Wren")
        txt = (self.root / "agora_keys.txt").read_text()
        self.assertIn("author=Kestrel", txt)
        self.assertIn("author=Wren", txt)

    def test_a_custom_root_does_not_touch_the_real_home_file(self):
        home_file = Path.home() / "agora_keys.txt"
        before = home_file.read_text() if home_file.exists() else None
        self._mint("Kestrel")
        after = home_file.read_text() if home_file.exists() else None
        self.assertEqual(before, after)   # untouched: backup went beside the root


if __name__ == "__main__":
    unittest.main(verbosity=2)
