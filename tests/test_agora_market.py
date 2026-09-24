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


class CthulhuAtTheCrossing(unittest.TestCase):
    """Walked headless: onto the platform at y=5.53, round him with 2.59 m
    to spare (model footprint 2.24), off the far side back onto the bridge."""
    def test_the_platform_clears_the_arcade(self):
        plaza_y = BRIDGE_H + 0.03
        self.assertGreater(plaza_y - 0.55, ARCADE_H + 0.3)

    def test_there_is_room_to_walk_round_him(self):
        plaza_r = const("PLAZA_R")
        statue_r = 3.16                # measured from cthulhu.glb at 10 m tall (+0.1)
        self.assertGreater((plaza_r - 0.6) - (statue_r + 0.35), 3.0)

    def test_cthulhu_is_the_tallest_thing_in_the_market(self):
        # Before (measured): Cthulhu 7 m, Nosferatu 9 m. Don: flip them.
        self.assertGreaterEqual(const("CTHULHU_H"), 9.0)
        self.assertLessEqual(const("NOSFERATU_H"), 7.0)
        self.assertLess(const("MAST_H") + 0.65, const("CTHULHU_H"))

    def test_the_arcade_walker_never_steps_onto_the_platform(self):
        self.assertIn("if (Math.cos(state.t) > 0.9){", PAGE)


class WhatThePageTellsYou(unittest.TestCase):
    def test_it_says_unsafe_and_what_the_stalls_are(self):
        self.assertIn(">UNSAFE<", PAGE)
        self.assertIn("This stall is waiting for someone.", PAGE)
        # Was "Every stall with a card is a real post." Once shops exist a
        # card can be a shop, so that stopped being true (market-stalls).
        self.assertIn("A stall with a lit doorway is a shop: walk up and go in. "
                      "A card without one is a Commons ad.", " ".join(PAGE.split()))

    def test_frosty_statues_are_wired_with_labelled_fallbacks(self):
        for name in ("cthulhu", "nosferatu", "gargoyle", "brazier"):
            self.assertIn(f"/models/statues/{name}.glb", PAGE)
        self.assertIn("Stand-in. Nosferatu Rex did not load.", PAGE)
        self.assertIn("getObjectByName('flame')", PAGE)

    def test_no_lantern_post_stands_in_the_door_lane(self):
        # The south tip's outer post sat dead centre in the lane (Don's phone,
        # 2026-09-24). The post at t=PI/2 lands at x=0, z=L+DECK_HW, inside the lane.
        deck_hw = const("WALK_HW") + 2.4
        self.assertLess(abs(P(math.pi / 2)[0]), 0.01)
        self.assertGreater(L + deck_hw, L + const("WALK_HW"))   # past SPUR_Z0: in the lane
        self.assertIn("if (Math.abs(x) < SPUR_HW + 0.5 && z > SPUR_Z0 - 0.5) { spots.push([-(SPUR_HW - 0.2), 0, z]); continue; }", PAGE)

    def test_arriving_from_the_market_faces_away_from_the_door(self):
        import agora_map
        self.assertIn("teleportPlayer(0, MARKET_DOOR_Z + 4.0, 'Frosty');\n  camera.position.set(0, player.position.y + 3.2, MARKET_DOOR_Z + 0.8);", agora_map.PAGE_3D)

    def test_the_room_sign_says_where_the_panel_really_is(self):
        # Labels must be true: the panel moved bottom-right (0b60d22).
        self.assertNotIn("top of your screen", PAGE)
        self.assertIn("are in the panel, bottom right.", PAGE)

    def test_the_walker_is_the_same_pawn_as_the_node_view(self):
        # Don, 2026-09-24: the market's pawn is the lantern and spirit from /3d,
        # one copy (agora_pawn.PAWN_JS) spliced into both pages.
        import agora_pawn, agora_map
        self.assertIn(agora_pawn.PAWN_JS, PAGE)
        self.assertIn(agora_pawn.PAWN_JS, agora_map.PAGE_3D)
        self.assertIn("placeLantern(tt);", PAGE)
        self.assertNotIn("CylinderGeometry(0.3, 0.3, 1.6, 10)", PAGE)

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



class TheMapServesGlb(unittest.TestCase):
    def test_glb_is_an_allowed_model_type(self):
        # .glb wasn't on the list: every statue 404'd (2026-09-24).
        self.assertEqual(agora_map.MODEL_CONTENT_TYPES.get(".glb"), "model/gltf-binary")


class MarketPolish(unittest.TestCase):
    def test_info_panel_is_bottom_right_and_toggles_compact_on_phones(self):
        """The info panel matches the node view (/3d): bottom-right dialog, compact on phones, tap to expand."""
        self.assertNotIn("top:10px;left:10px", PAGE)
        self.assertIn("right:10px;bottom:10px", PAGE)
        self.assertIn("border:1px solid #5c5244", PAGE)
        self.assertIn("bottom:86px", PAGE)
        self.assertIn("#hud:not(.open)", PAGE)
        self.assertIn("-webkit-line-clamp", PAGE)
        self.assertIn("document.querySelector('#hud > div').addEventListener('click'", PAGE)
        self.assertIn("classList.toggle('open')", PAGE)

    def test_stall_room_dims_the_pawn_lantern_and_restores_it_on_exit(self):
        """Inside a stall room, the pawn's carried lantern dims so it doesn't blow out the room to white."""
        self.assertIn("lanternLight.intensity", PAGE)
        self.assertRegex(PAGE, r"enterShop[\s\S]*?lanternLight\.intensity\s*=\s*(0\.[1-9]|0\.[0-9]+)")
        self.assertRegex(PAGE, r"leaveShop[\s\S]*?lanternLight\.intensity\s*=\s*2\.6")


if __name__ == "__main__":
    unittest.main()
