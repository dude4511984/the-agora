"""The path out of the north gate: flagstones gate -> plaza -> the market door.

Headless (claude-room rigs/agora/pathprobe.py, 2026-09-24): straight-down
rays hit path at z=-12, -13.5, -26, -28.5, the plaza paving at -19.5, and
bare grid past the end at -29.5. The path must end inside the walkable
world (FLOOR_R = 30 on Frosty) or nobody can reach its end.
"""
import re
import unittest

import agora_map


class TheNorthPathLeadsSomewhere(unittest.TestCase):
    def setUp(self):
        self.page = agora_map.PAGE_3D

    def test_it_ends_inside_the_walkable_world(self):
        end = float(re.search(r"NORTH_PATH_END_Z = ([-\d.]+)", self.page).group(1))
        floor_r = float(re.search(r"const FLOOR_R = ([\d.]+);", self.page).group(1))
        self.assertLess(abs(end), floor_r)
        self.assertGreater(abs(end), 25)

    def test_the_plaza_rebuild_does_not_erase_it(self):
        self.assertIn("if (c !== northPathGroup) publicCommonsGroup.remove(c)", self.page)


if __name__ == "__main__":
    unittest.main()
