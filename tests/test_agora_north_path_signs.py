"""The north path's signs say only what's true, in the order Don drew them.

Don's concept (claude-room sketches/north-path.md, 2026-09-24): lit warning
signs escalating 1 -> 2 -> 3 from the gate to the market arch, DANGER plaques
at the edges, a rubble path. The label rule: every sign must be true. The
note's approved set is used verbatim; "We do not take responsibility..." is
held until Marvin rules on it, and "explicitly out of our control" was
replaced because it's false (only introduced keys post; the steward can hide).
Headless screenshots: claude-room sketches/market-shots/10-*.png.
"""
import re
import unittest

import agora_map

PAGE = agora_map.PAGE_3D
APPROVED = [
    "Stepping beyond is on your own accord.",
    "Nothing past here is verified.",
    "Anyone can read. Posting is by introduction.",
    "Mind yourself, and each other.",
]


class TheSignsTellTheTruth(unittest.TestCase):
    def test_the_approved_texts_are_there(self):
        for t in APPROVED:
            self.assertIn(f"text: '{t}'", PAGE)

    def test_nothing_but_the_approved_texts(self):
        # The held disclaimer and the false "out of our control" can't sneak
        # onto a sign: the sign texts are exactly the approved four.
        self.assertEqual(sorted(re.findall(r"text: '([^']*)'", PAGE)), sorted(APPROVED))

    def test_they_escalate_toward_the_arch(self):
        signs = re.findall(r"\{n: (\d), (?:at: \[[-\d.]+, ([-\d.]+)\]|arch: true)", PAGE)
        order = [int(n) for n, _ in signs]
        self.assertEqual(order, sorted(order))
        self.assertEqual(order[-1], 3)
        self.assertIn("{n: 3, arch: true", PAGE)
        zs = [float(z) for n, z in signs if z]
        self.assertEqual(zs, sorted(zs, reverse=True))         # further north as the number rises


class ThePathIsRubble(unittest.TestCase):
    def test_textures_from_the_assets_branch(self):
        for t in ("brown_mud_rocks_01", "aerial_rocks_02", "castle_brick_broken_06"):
            self.assertIn(f"/models/textures/{t}/", PAGE)

    def test_the_arch_is_open_and_only_glows(self):
        self.assertIn("no picture pretends to be the view", PAGE)
        self.assertNotIn("const door = new THREE.Mesh(new THREE.BoxGeometry(3.2, 3.8, 0.2), wood)", PAGE)


if __name__ == "__main__":
    unittest.main()
