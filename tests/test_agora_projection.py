"""Pure, adversarial tests for the Atlas scene projection."""

from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))

from kin_diary.agora.projection import ProjectionError, project  # noqa: E402


def view():
    return {
        "node": "Home",
        "as_of_unix_ms": 100,
        "places": [
            {"place_id": "concourse", "kind": "commons", "parent": ""},
            {"place_id": "stall", "kind": "kiosk", "parent": "concourse"},
            {"place_id": "room", "kind": "door", "parent": "concourse"},
        ],
        "peer_doors": [{
            "place_id": "door-to-Frosty", "kind": "door",
            "parent": "concourse", "peer": "Frosty",
            "peer_key_id": "f" * 64, "url": "http://frosty:8770",
            "locked": True,
        }],
        "listings": [{
            "listing_id": "listing-1", "place_id": "stall",
            "title": "calculator", "artifact_sha256": "a" * 64,
        }],
        "presence": [
            {"key_id": "live", "place_id": "concourse",
             "expires_at_unix_ms": 200, "label": "Live"},
            {"key_id": "gone", "place_id": "concourse",
             "expires_at_unix_ms": 100, "label": "Gone"},
        ],
    }


class ProjectionTests(unittest.TestCase):
    def test_projects_tree_edges_listings_and_live_occupants(self):
        scene = project(view(), now_ms=100)
        self.assertEqual(scene["roots"], ["concourse"])
        nodes = {node["id"]: node for node in scene["nodes"]}
        self.assertEqual(nodes["concourse"]["children"], ["room", "stall"])
        self.assertEqual(nodes["stall"]["listings"][0]["listing_id"], "listing-1")
        self.assertEqual([o["key_id"] for o in nodes["concourse"]["occupants"]], ["live"])
        self.assertEqual(scene["edges"][0]["label"], "Frosty")
        self.assertTrue(scene["edges"][0]["locked"])

    def test_replace_not_merge(self):
        first = view()
        second = view()
        second["places"] = [second["places"][0]]
        second["peer_doors"] = []
        second["listings"] = []
        second["presence"] = []
        project(first, now_ms=100)
        replacement = project(second, now_ms=100)
        self.assertEqual([node["id"] for node in replacement["nodes"]], ["concourse"])
        self.assertEqual(replacement["edges"], [])

    def test_scene_is_a_strict_function_of_view(self):
        source = view()
        scene = project(source, now_ms=100)
        source["places"][1]["kind"] = "changed"
        source["listings"][0]["title"] = "changed"
        source["peer_doors"][0]["label"] = "changed"
        source["presence"][0]["label"] = "changed"
        self.assertEqual(
            project(view(), now_ms=100),
            scene,
        )

    def test_same_input_and_time_are_identical(self):
        source = view()
        self.assertEqual(project(source, 100), project(copy.deepcopy(source), 100))

    def test_expired_presence_is_not_an_occupant(self):
        scene = project(view(), now_ms=200)
        nodes = {node["id"]: node for node in scene["nodes"]}
        self.assertEqual(nodes["concourse"]["occupants"], [])

    def test_absent_parent_is_rejected(self):
        bad = view()
        bad["places"][1]["parent"] = "missing"
        with self.assertRaises(ProjectionError):
            project(bad, 100)

    def test_parent_cycle_is_rejected(self):
        bad = view()
        bad["places"][0]["parent"] = "room"
        bad["places"][2]["parent"] = "concourse"
        with self.assertRaises(ProjectionError):
            project(bad, 100)

    def test_listing_pointing_to_absent_place_is_rejected(self):
        bad = view()
        bad["listings"][0]["place_id"] = "missing"
        with self.assertRaises(ProjectionError):
            project(bad, 100)

    def test_duplicate_place_ids_are_rejected(self):
        bad = view()
        bad["places"].append(dict(bad["places"][0]))
        with self.assertRaises(ProjectionError):
            project(bad, 100)

    def test_door_place_collision_is_rejected(self):
        bad = view()
        bad["peer_doors"][0]["place_id"] = "stall"
        with self.assertRaises(ProjectionError):
            project(bad, 100)

    def test_unlocked_peer_door_is_rejected(self):
        bad = view()
        bad["peer_doors"][0]["locked"] = False
        with self.assertRaises(ProjectionError):
            project(bad, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
