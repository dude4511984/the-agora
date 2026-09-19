"""Peer-door crossing is a pass-through, not a snap-cut.

Mutation: restore `teleportPlayer(0, 6); loadNode().finally(...)` with no
veil and this fails.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import agora_map  # noqa: E402


class CrossingIsAPassThrough(unittest.TestCase):
    def test_veil_and_wait_are_in_the_crossing(self):
        page = agora_map.PAGE_3D
        self.assertIn('id="cross"', page)
        self.assertIn("veil.classList.add('on')", page)
        self.assertIn("await wait(750)", page)
        self.assertIn("async function crossDoor", page)
        self.assertNotIn("teleportPlayer(0, 6);\n  loadNode().finally", page)


if __name__ == "__main__":
    unittest.main()

