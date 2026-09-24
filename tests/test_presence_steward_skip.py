"""Presence must not stand the steward's placeholder in the concourse.

Frosty is the first node where a resident (Marvin) is ALSO the steward, so the
steward-skip finally matters. Home never exercised it — no Home resident is the
steward. Hermetic: fakes the store and keys, so it runs anywhere.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import presence_heartbeat as ph  # noqa: E402


class _FakeNode:
    def __init__(self, residents):
        self.residents = residents


class _FakeStore:
    def __init__(self, residents, steward_key_id):
        self._residents = residents
        self.steward_key_id = steward_key_id
    def load(self):
        return _FakeNode(dict(self._residents))


class StewardIsSkipped(unittest.TestCase):
    def setUp(self):
        self.residents = {
            "Marvin":  "aa" * 32,
            "Eli":     "bb" * 32,
            "Crungus": "cc" * 32,
            "Bong":    "dd" * 32,
        }
        self.posted = []
        self._orig_post = ph._post_presence
        self._orig_load = ph.load_current
        ph._post_presence = lambda node, port, key: self.posted.append(key.author)
        ph.load_current = lambda author, root=None: types.SimpleNamespace(author=author)

    def tearDown(self):
        ph._post_presence = self._orig_post
        ph.load_current = self._orig_load

    def test_with_steward_configured_only_non_steward_residents_are_placed(self):
        store = _FakeStore(self.residents, "aa" * 32)   # Marvin is steward
        ph._heartbeat(store, "Frosty", 8770)
        self.assertEqual(sorted(self.posted), ["Bong", "Crungus", "Eli"])
        self.assertNotIn("Marvin", self.posted)

    def test_without_a_steward_the_placeholder_would_leak_in(self):
        # proves the skip is load-bearing: no steward => Marvin is placed too
        store = _FakeStore(self.residents, None)
        ph._heartbeat(store, "Frosty", 8770)
        self.assertIn("Marvin", self.posted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
