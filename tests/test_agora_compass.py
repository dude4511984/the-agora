"""The compass: bottom centre, N/E/S/W only, north = -z (toward the north gate).

Headless check (claude-room rigs/agora/compass.py, 2026-09-24): camera aimed
-z reads N, +x E, +z S, -x W. These guards keep it wired in.
"""
import unittest

import agora_map


class CompassIsWiredIn(unittest.TestCase):
    def setUp(self):
        self.page = agora_map.PAGE_3D

    def test_it_is_on_the_page_and_out_of_the_way(self):
        self.assertIn('id="compass"', self.page)
        self.assertIn("#compass{position:fixed;left:50%;bottom:18px", self.page)
        self.assertIn("pointer-events:none", self.page.split("#compass{")[1].split("}")[0])

    def test_it_updates_every_frame_but_only_on_quarter_turns(self):
        anim = self.page[self.page.index("function animate(){"):][:200]
        self.assertIn("updateCompass();", anim)
        self.assertIn("['N', 'E', 'S', 'W'][Math.round(deg / 90) % 4]", self.page)
        self.assertIn("if (d === _compassLast) return;", self.page)

    def test_north_is_minus_z(self):
        self.assertIn("Math.atan2(dx, -dz)", self.page)


if __name__ == "__main__":
    unittest.main()
