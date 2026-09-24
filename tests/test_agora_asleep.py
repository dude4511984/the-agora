"""A node that doesn't answer: the door stays shut, and nobody is left behind.

Don, 2026-09-24 ~4am: crossed to Home while Home was asleep. The map rebuilt
Home's room, the fetch failed ("Unexpected token '<'": an HTML error page fed
to r.json()), and the catch never cleared Frosty's residents, so Home's room
showed Frosty's people. Reproduced headless (rig in claude-room,
rigs/agora/): before, a failed crossing ended in Home with Eli, Crungus and
Bong present; after, the walker stays put and reads "Home is asleep."

These are string guards on the page's JavaScript, the same style as
test_agora_crossfade. The behaviour itself was shown in a real browser.
"""
import unittest

import agora_map


class TheDoorKnocksFirst(unittest.TestCase):
    def setUp(self):
        self.page = agora_map.PAGE_3D
        start = self.page.index("async function crossDoor")
        self.cross = self.page[start:self.page.index("\n}\n", start)]

    def test_the_far_side_is_asked_before_the_room_is_rebuilt(self):
        self.assertIn("farSideAnswers(t.url)", self.cross)
        self.assertLess(self.cross.index("farSideAnswers(t.url)"),
                        self.cross.index("buildRoom("))
        self.assertLess(self.cross.index("farSideAnswers(t.url)"),
                        self.cross.index("sel.value = opt.value"))

    def test_no_answer_says_asleep_and_returns(self):
        self.assertIn("is asleep. The door stays shut.", self.cross)


class AFailedLoadLeavesNoGhosts(unittest.TestCase):
    def setUp(self):
        page = agora_map.PAGE_3D
        start = page.index("async function loadNode")
        body = page[start:page.index("\n}\n", start)]
        self.catch = body[body.rindex("} catch (e) {"):]
        self.body = body

    def test_catch_clears_presence_and_places(self):
        self.assertIn("clearPresence()", self.catch)
        self.assertIn("buildPlaces([]", self.catch)

    def test_node_replies_are_never_fed_straight_to_json(self):
        # The node fetches; /commons-recent etc. keep their own .catch().
        self.assertNotIn("encodeURIComponent(node)).then(r => r.json())", self.body)
        self.assertIn("readNodeJson(r,", self.body)


if __name__ == "__main__":
    unittest.main()
