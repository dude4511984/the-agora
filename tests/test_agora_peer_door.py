"""Peer crossing is at the rampart end, not a wall cutout.

Don, 2026-09-19: stop fighting the embedded gate. Mutation: put the
trigger back on gateZWall / doorZ=28.8 and this fails.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import agora_map  # noqa: E402


class CrossingIsAtTheWalkwayEnd(unittest.TestCase):
    def test_trigger_uses_rampart_end_constants(self):
        page = agora_map.PAGE_3D
        self.assertIn("z = RAMP_Z_END - 0.30", page)
        self.assertIn("x = (RAMP_X_MIN + RAMP_X_MAX) / 2", page)
        self.assertIn("y = RAMPART_DECK_H", page)
        self.assertNotIn("const doorZ = isHome ? 15.5 : 28.8", page)
        self.assertNotIn("z = gateZWall", page)
        self.assertIn("async function crossDoor", page)


if __name__ == "__main__":
    unittest.main()
