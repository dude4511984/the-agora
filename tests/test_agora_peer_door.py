"""Peer-door trigger is the fort gate, not a second frame down the path.

The three commits that missed: trigger and mesh used different numbers
(±0.45 vs ~1.84m gate, then a frame at z=28.8). Mutation: put doorZ
back at 28.8 or hardcode GATE_X ±0.45 and this fails.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import agora_map  # noqa: E402


class PeerDoorIsTheFortGate(unittest.TestCase):
    def test_trigger_and_collision_share_gateHalfOpen(self):
        page = agora_map.PAGE_3D
        self.assertIn("halfWidth: gateHalfOpen", page)
        self.assertIn("GATE_X_MIN = -gateHalfOpen", page)
        self.assertIn("gateHalfOpen = Math.max(0.4, gateLen / 2 - 0.08)", page)
        self.assertIn("z = gateZWall", page)
        self.assertNotIn("const doorZ = isHome ? 15.5 : 28.8", page)
        self.assertNotIn("GATE_X_MIN = -0.45", page)
        self.assertNotIn("West rampart walkway crossing trigger", page)
