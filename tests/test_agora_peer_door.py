"""Peer crossing is at the south walkway end beside the chalkboard sign with a real doorway frame.

Don / Gem, 2026-09-21: door sits at the end of the south walkway (z=28.8 on
Frosty, z=18.8 on Home) scaled to Home's 0.655-scale world.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import agora_map  # noqa: E402


class CrossingIsAtTheWalkwayEnd(unittest.TestCase):
    def test_trigger_uses_walkway_end_coordinates(self):
        page = agora_map.PAGE_3D
        self.assertIn("const z = isHome ? 18.8 : 28.8;", page)
        self.assertIn("peerDoorTemplate", page)
        self.assertIn("large_castle_door.gltf", page)
        self.assertNotIn("z = RAMP_Z_END - 0.30", page)
        self.assertNotIn("y = RAMPART_DECK_H", page)
        self.assertNotIn("z = gateZWall", page)
        self.assertIn("async function crossDoor", page)


if __name__ == "__main__":
    unittest.main()
