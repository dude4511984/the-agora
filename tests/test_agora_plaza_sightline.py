"""The UNSAFE plate must not stand between the gate and the Commons boards.

2026-09-24: the plate at (-1.2, -13) hid the only live post from the
courtyard, the gateway and a step past it. Raycast in a real browser
(rig in claude-room, rigs/agora/): all three sightlines BLOCKED before,
all SEEN after with 1, 2 and 4 posts. This is the same geometry done
flat: the segment from each eye to each board must clear the plate's
footprint.
"""
import math
import re
import unittest

import agora_map

EYES = [(0.0, -7.0), (0.0, -10.5), (0.0, -12.2)]


def _plate():
    page = agora_map.PAGE_3D
    x, _, z = map(float, re.search(r"plate\.position\.set\(([-\d.]+), ([-\d.]+), ([-\d.]+)\)", page).groups())
    ry = float(re.search(r"plate\.rotation\.y = ([-\d.]+);", page).group(1))
    w = float(re.search(r"const PLATE_W = ([\d.]+);", page).group(1))
    dx, dz = math.cos(ry) * w / 2, -math.sin(ry) * w / 2
    return (x - dx, z - dz), (x + dx, z + dz)


def _boards(n):
    page = agora_map.PAGE_3D
    cz = float(re.search(r"PLAZA_CENTER_Z = ([-\d.]+)", page).group(1))
    r = float(re.search(r"PLAZA_ARC_R = ([\d.]+)", page).group(1))
    step = float(re.search(r"const STEP = ([\d.]+);", page).group(1))
    return [(r * math.sin((i - (n - 1) / 2) * step), cz + r * math.cos((i - (n - 1) / 2) * step)) for i in range(n)]


def _cross(p, q, r, s):
    def o(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return o(p, q, r) * o(p, q, s) < 0 and o(r, s, p) * o(r, s, q) < 0


class ThePlateDoesNotHideThePosts(unittest.TestCase):
    def test_every_board_is_in_sight_from_the_way_out(self):
        a, b = _plate()
        for n in (1, 2, 3, 4):
            for eye in EYES:
                for board in _boards(n):
                    self.assertFalse(_cross(eye, board, a, b),
                                     f"{n} posts: plate blocks {board} from {eye}")


if __name__ == "__main__":
    unittest.main()
