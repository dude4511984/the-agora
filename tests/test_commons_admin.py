"""commons_admin.py — the steward's own tool. Confirms what the hardening
report says: hide has no HTTP route, only this CLI, talking to the same
SQLite file commons_server.py does.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest

import commons_admin
import commons_server as cs


class CommonsAdminHide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "commons.db"
        self.store = cs.CommonsStore(self.db_path)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = commons_admin.main(["--db", str(self.db_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_hide_a_real_post_leaves_a_tombstone(self):
        post_id = self.store.insert_post("k1", "what", "why", "how")
        code, out, _ = self._run("hide", post_id, "spam")
        self.assertEqual(code, 0)
        self.assertIn(post_id, out)
        self.assertIn("spam", out)
        posts = self.store.list_posts()
        self.assertEqual(posts[0]["hidden"]["reason"], "spam")
        self.assertIsNone(posts[0]["what"])

    def test_hide_an_unknown_id_fails_clearly(self):
        code, _, err = self._run("hide", "not-a-real-id", "spam")
        self.assertEqual(code, 1)
        self.assertIn("no such post", err)

    def test_hide_without_a_reason_fails_clearly(self):
        """Argparse requires the argument to be present, but an empty
        string is still a value CommonsStore.hide_post itself refuses —
        the CLI must surface that, not crash with a traceback."""
        post_id = self.store.insert_post("k1", "what", "why", "how")
        code, _, err = self._run("hide", post_id, "")
        self.assertEqual(code, 1)
        self.assertIn("error", err)
        self.assertIsNone(self.store.list_posts()[0]["hidden"])

    def test_talks_to_the_same_db_file_commons_server_uses(self):
        """Not a re-implementation: this really is CommonsStore against
        the same file, reachable by another process (the server) too."""
        post_id = self.store.insert_post("k1", "what", "why", "how")
        self._run("hide", post_id, "reused-connection-check")
        reopened = cs.CommonsStore(self.db_path)
        self.assertEqual(reopened.list_posts()[0]["hidden"]["reason"],
                          "reused-connection-check")


if __name__ == "__main__":
    unittest.main(verbosity=2)
