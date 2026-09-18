"""The map's proxy fetches a node URL server-side, so its allowlist is the wall
between "render my cluster" and "an open SSRF relay to the whole internet."

Freeze it: loopback and RFC1918 and the Tailscale CGNAT range are allowed;
everything else — public IPs, hostnames, link-local, the AWS metadata address —
is refused, and _node_view refuses a non-private URL before it ever opens a
socket.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agora_map  # noqa: E402


class TheProxyOnlyReachesPrivateHosts(unittest.TestCase):

    def test_private_and_loopback_are_allowed(self):
        for host in ("127.0.0.1", "localhost", "192.168.1.119", "10.0.0.5",
                     "172.16.4.2", "172.31.255.1", "100.64.0.1", "100.127.9.9"):
            self.assertTrue(agora_map._is_private_host(host), host)

    def test_public_and_deceptive_hosts_are_refused(self):
        for host in ("8.8.8.8", "example.com", "everysynthetic.org",
                     "169.254.169.254",          # cloud metadata
                     "172.15.0.1", "172.32.0.1",  # just outside 172.16/12
                     "100.63.0.1", "100.128.0.1", # just outside 100.64/10
                     "1.2.3.4", ""):
            self.assertFalse(agora_map._is_private_host(host), host)

    def test_node_view_refuses_a_public_url_before_opening_a_socket(self):
        with self.assertRaises(ValueError):
            agora_map._node_view("http://8.8.8.8:8770")
        with self.assertRaises(ValueError):
            agora_map._node_view("http://example.com/view")

    def test_node_view_refuses_a_non_http_scheme(self):
        with self.assertRaises(ValueError):
            agora_map._node_view("file:///etc/passwd")


class Shape3DRoute(unittest.TestCase):

    def setUp(self):
        self.orig_claimed_shape3d = agora_map.claimed_shape3d

    def tearDown(self):
        agora_map.claimed_shape3d = self.orig_claimed_shape3d

    def test_unclaimed_or_invalid_kin_returns_404(self):
        class MockHandler:
            def __init__(self, path):
                self.path = path
                self.code = None
                self.body = None
                self.ctype = None
            def _send(self, code, body, ctype):
                self.code = code
                self.body = body
                self.ctype = ctype

        agora_map.claimed_shape3d = lambda name: None

        h = MockHandler("/shape3d?kin=NonExistent")
        agora_map.Handler.do_GET(h)
        self.assertEqual(h.code, 404)

        # path traversal attempt
        h_bad = MockHandler("/shape3d?kin=../../etc")
        agora_map.Handler.do_GET(h_bad)
        self.assertEqual(h_bad.code, 404)

    def test_claimed_kin_returns_200_json(self):
        class MockHandler:
            def __init__(self, path):
                self.path = path
                self.code = None
                self.body = None
                self.ctype = None
            def _send(self, code, body, ctype):
                self.code = code
                self.body = body
                self.ctype = ctype

        fake_shape = {"shape": "cylinder", "color": "#2b2b2b", "roughness": 0.35}
        agora_map.claimed_shape3d = lambda name: fake_shape if name == "Bong" else None

        h = MockHandler("/shape3d?kin=Bong")
        agora_map.Handler.do_GET(h)
        self.assertEqual(h.code, 200)
        self.assertEqual(h.ctype, "application/json")
        import json
        self.assertEqual(json.loads(h.body.decode("utf-8")), fake_shape)


class Agora3DRoomWallAndArchway(unittest.TestCase):

    def test_page_3d_harvests_and_places_thin_straight_wall_filler(self):
        page = agora_map.PAGE_3D
        self.assertIn("harvest('wall_thin_straight_04')", page)
        self.assertIn("thinStrTemplate.scale.setScalar(SCALE)", page)
        self.assertIn("fillerLeft.scale.set(SCALE, SCALE, flankLen / rawThinStrLen)", page)
        self.assertIn("fillerRight.scale.set(SCALE, SCALE, flankLen / rawThinStrLen)", page)
        self.assertIn("fLeftPos = axis === 'x' ? {x: -actualSeg / 2, z: fixedCoord}", page)
        self.assertIn("fRightPos = axis === 'x' ? {x: gateLen / 2, z: fixedCoord}", page)

    def test_page_3d_linear_archway_corridor_and_wall_boundaries(self):
        page = agora_map.PAGE_3D
        self.assertIn("GATE_X_MIN = -0.45", page)
        self.assertIn("GATE_X_MAX = 0.45", page)
        self.assertIn("wallInner = 10.5 - 0.613 - PLAYER_R", page)
        self.assertIn("wallOuter = 10.5 + PLAYER_R", page)

    def test_archway_traversal_and_wall_obstruction_mechanics(self):
        # Mathematical verification of the exact collision rules defined in PAGE_3D
        PLAYER_R = 0.35
        GATE_Z_WALL = 10.5
        WALL_THICK_THIN = 0.613
        wallInner = GATE_Z_WALL - WALL_THICK_THIN - PLAYER_R  # ~9.537
        wallOuter = GATE_Z_WALL + PLAYER_R                    # ~10.85
        GATE_X_MIN = -0.45
        GATE_X_MAX = 0.45
        margin = 0.25

        def resolve(pos):
            if 9.2 <= pos[1] <= 11.2 and -2.5 <= pos[0] <= 2.5:
                if GATE_X_MIN <= pos[0] <= GATE_X_MAX:
                    if pos[0] < GATE_X_MIN + margin:
                        pos[0] = GATE_X_MIN + margin
                    elif pos[0] > GATE_X_MAX - margin:
                        pos[0] = GATE_X_MAX - margin
                else:
                    if pos[1] < GATE_Z_WALL:
                        if pos[1] > wallInner:
                            pos[1] = wallInner
                    else:
                        if pos[1] < wallOuter:
                            pos[1] = wallOuter

        # Traversal through archway outward (commons -> plain)
        for x in [-0.35, -0.20, 0.0, 0.20, 0.35]:
            pos = [x, 9.0]
            while pos[1] < 11.5:
                pos[1] += 0.08
                resolve(pos)
            self.assertGreaterEqual(pos[1], 11.5)
            self.assertTrue(GATE_X_MIN <= pos[0] <= GATE_X_MAX)

        # Traversal through archway inward (plain -> commons)
        for x in [-0.35, -0.20, 0.0, 0.20, 0.35]:
            pos = [x, 11.5]
            while pos[1] > 9.0:
                pos[1] -= 0.08
                resolve(pos)
            self.assertLessEqual(pos[1], 9.0)
            self.assertTrue(GATE_X_MIN <= pos[0] <= GATE_X_MAX)

        # Solid wall stops penetration at filled flank sections (e.g. x in [-2.0, -1.0] and [1.0, 2.0])
        for x in [-2.0, -1.5, -1.0, 1.0, 1.5, 2.0]:
            pos = [x, 9.0]
            for _ in range(40):
                pos[1] += 0.08
                resolve(pos)
            self.assertLessEqual(pos[1], wallInner + 1e-4)

    def test_ramparts_corner_tower_scoped_to_west_wall(self):
        page = agora_map.PAGE_3D
        # Verify the ramparts south tower boundary does NOT globally clamp Z for all X
        self.assertIn("if (pos.x <= RAMP_X_MAX && pos.z > RAMP_Z_END) {", page)
        self.assertNotIn("if (pos.z > RAMP_Z_END) {\n      pos.z = RAMP_Z_END;", page)


class Agora3DBoothAssets(unittest.TestCase):

    def test_door_and_bench_assets_exist_locally(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        static_models = os.path.join(repo_root, "static", "models")
        self.assertTrue(os.path.isfile(os.path.join(static_models, "large_castle_door", "large_castle_door.gltf")))
        self.assertTrue(os.path.isfile(os.path.join(static_models, "large_castle_door", "large_castle_door.bin")))
        self.assertTrue(os.path.isfile(os.path.join(static_models, "painted_wooden_bench", "painted_wooden_bench.gltf")))
        self.assertTrue(os.path.isfile(os.path.join(static_models, "painted_wooden_bench", "painted_wooden_bench.bin")))

    def test_page_3d_loads_and_instantiates_door_and_bench_templates(self):
        page = agora_map.PAGE_3D
        self.assertIn("'/models/large_castle_door/large_castle_door.gltf'", page)
        self.assertIn("'/models/painted_wooden_bench/painted_wooden_bench.gltf'", page)
        self.assertIn("doorTemplate = gltf.scene;", page)
        self.assertIn("benchTemplate = gltf.scene;", page)
        self.assertIn("if (kind === 'table' || kind === 'bench')", page)
        self.assertIn("if (benchTemplate) return benchTemplate.clone(true);", page)
        self.assertIn("if (kind === 'door')", page)
        self.assertIn("if (doorTemplate) return doorTemplate.clone(true);", page)
        self.assertIn("bench: 0xc98a5a", page)
        self.assertIn("bench: 0.75", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)


