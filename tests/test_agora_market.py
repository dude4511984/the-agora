"""The market past the north gate: a figure eight, one way in and out.

Walked headless end to end (claude-room rigs/agora/market.py, 2026-09-24):
lane -> street -> under the crossing on the low diagonal (y=0, covered) ->
over the same x/z on the bridge (y=5.5, open) -> back to the south tip ->
lane -> door -> /3d?from=market, which stands you inside Frosty's door.
These tests hold the geometry and the promises the page makes.
"""
import math
import re
import unittest

import agora_map
import agora_market

PAGE = agora_market.MARKET_PAGE


def const(name):
    return float(re.search(rf"\b{name} = ([\d.]+)", PAGE).group(1))


L, W = const("L"), const("W")
BRIDGE_H, ARCADE_H = const("BRIDGE_H"), const("ARCADE_H")


def P(t):
    return ((W / 2) * math.sin(2 * t), L * math.sin(t))


def H(t):
    c = math.cos(t)
    return BRIDGE_H * c * c if c > 0 else 0.0


class OnePathOverTheOther(unittest.TestCase):
    def test_the_street_crosses_itself_at_one_spot(self):
        for t in (0.0, math.pi):
            x, z = P(t)
            self.assertAlmostEqual(x, 0, places=9)
            self.assertAlmostEqual(z, 0, places=9)

    def test_the_bridge_clears_the_arcade(self):
        deck_underside = H(0) - 0.45
        self.assertGreater(deck_underside, ARCADE_H + 0.5)
        self.assertEqual(H(math.pi), 0)

    def test_the_ramps_are_walkable(self):
        worst = 0
        for i in range(2000):
            t = -math.pi / 2 + math.pi * i / 2000
            dt = 1e-4
            rise = abs(H(t + dt) - H(t))
            (x0, z0), (x1, z1) = P(t), P(t + dt)
            worst = max(worst, rise / math.hypot(x1 - x0, z1 - z0))
        self.assertLess(math.degrees(math.atan(worst)), 12)

    def test_the_door_is_at_ground_level(self):
        self.assertAlmostEqual(H(math.pi / 2), 0, places=9)


class WhatThePageTellsYou(unittest.TestCase):
    def test_it_says_unsafe_and_what_the_stalls_are(self):
        self.assertIn(">UNSAFE<", PAGE)
        self.assertIn("This stall is waiting for someone.", PAGE)
        self.assertIn("Every stall with a card is a real post.", PAGE)

    def test_stand_ins_are_labelled(self):
        self.assertIn("Stand-in. The vampire lord arrives with the assets.", PAGE)
        self.assertIn("/models/market/statue.glb", PAGE)
        self.assertIn("/models/market/gargoyle.glb", PAGE)

    def test_the_walker_is_tracked_along_the_path(self):
        self.assertIn("state.t += along / f.speed", PAGE)
        self.assertIn("const LANE_SIDE = Math.sign(frame(Math.PI / 2).N.z)", PAGE)


class TheDoorsConnect(unittest.TestCase):
    def test_frosty_side(self):
        page = agora_map.PAGE_3D
        self.assertIn("location.href = '/market'", page)
        self.assertIn("get('from') === 'market'", page)

    def test_market_side(self):
        self.assertIn("location.href = '/3d?from=market'", PAGE)


if __name__ == "__main__":
    unittest.main()
