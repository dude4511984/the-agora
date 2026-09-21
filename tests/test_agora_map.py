"""The map's proxy fetches a node URL server-side, so its allowlist is the wall
between "render my cluster" and "an open SSRF relay to the whole internet."

Freeze it: loopback and RFC1918 and the Tailscale CGNAT range are allowed;
everything else — public IPs, hostnames, link-local, the AWS metadata address —
is refused, and _node_view refuses a non-private URL before it ever opens a
socket.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertIn("GATE_X_MIN = -gateHalfOpen", page)
        self.assertIn("GATE_X_MAX = gateHalfOpen", page)
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


class Agora3DSpeakerChair(unittest.TestCase):

    def test_speaker_chair_assets_exist_locally(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        static_models = os.path.join(repo_root, "static", "models")
        chair_dir = os.path.join(static_models, "wooden_chair_01")
        self.assertTrue(os.path.isfile(os.path.join(chair_dir, "wooden_chair_01.gltf")))
        self.assertTrue(os.path.isfile(os.path.join(chair_dir, "WoodenChair_01.bin")))
        self.assertTrue(os.path.isfile(os.path.join(chair_dir, "textures", "WoodenChair_01_diff_1k.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(chair_dir, "textures", "WoodenChair_01_nor_gl_1k.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(chair_dir, "textures", "WoodenChair_01_arm_1k.jpg")))

    def test_page_3d_loads_chair_and_preserves_amber_emissive(self):
        page = agora_map.PAGE_3D
        self.assertIn("'/models/wooden_chair_01/wooden_chair_01.gltf'", page)
        self.assertIn("function setChairSpeaker(speaking)", page)
        self.assertIn("setChairSpeaker(Boolean(root.speaker));", page)
        self.assertIn("o.material.emissiveMap = o.material.map;", page)
        self.assertIn("o.material.emissive.set(0xff9922);", page)
        self.assertIn("o.material.emissiveIntensity = 1.4;", page)
        self.assertIn("chairMat.color.set(0xffcf7a);", page)
        self.assertIn("chairMat.emissive.set(0x332200);", page)


class Agora3DLanternAtmosphere(unittest.TestCase):

    def test_page_3d_defines_volumetric_haze_and_smoke(self):
        page = agora_map.PAGE_3D
        self.assertIn("function makePuffTexture()", page)
        self.assertIn("const lanternAura = new THREE.Sprite(", page)
        self.assertIn("const SMOKE_PUFF_COUNT = 12;", page)
        self.assertIn("lantern.add(lanternAura);", page)

    def test_page_3d_defines_spirit_shimmer_shader_and_billboard(self):
        page = agora_map.PAGE_3D
        self.assertIn("function makeSpiritTexture()", page)
        self.assertIn("const spiritPlane = new THREE.Mesh(", page)
        self.assertIn("const spiritMat = new THREE.ShaderMaterial({", page)
        self.assertIn("uniform float uTime;", page)
        self.assertIn("uniform sampler2D uTex;", page)
        self.assertIn("function updateLanternAtmosphere(t, bob, rx, rz)", page)
        self.assertIn("spiritPlane.quaternion.copy(camera.quaternion);", page)


class Agora3DProximityVoice(unittest.TestCase):

    def test_page_3d_defines_proximity_voice_and_ptt(self):
        page = agora_map.PAGE_3D
        self.assertIn("const VOICE_ENDPOINT = '/voice_chat';", page)
        self.assertIn("let presentKinList = [];", page)
        self.assertIn("function getNearestKin()", page)
        self.assertIn("Math.hypot(player.position.x - k.x, player.position.z - k.z)", page)
        self.assertIn("function startVoiceRecording()", page)
        self.assertIn("function stopVoiceRecording()", page)
        self.assertIn("function sendVoiceToBackend(blob, target)", page)
        self.assertIn("function playVoiceAudio(url, kinLabel", page)
        self.assertIn("navigator.mediaDevices.getUserMedia({ audio: true })", page)
        self.assertIn("new MediaRecorder(mediaStream", page)
        self.assertIn("k === 'v' || k === 't'", page)

    def test_page_3d_defines_voice_hud(self):
        page = agora_map.PAGE_3D
        self.assertIn("id=\"voice-hud\"", page)
        self.assertIn("id=\"ptt-btn\"", page)
        self.assertIn("id=\"voice-status\"", page)
        self.assertIn("id=\"nearest-kin-name\"", page)
        self.assertIn("Hold <b>V</b> (or T) to talk to nearest Kin", page)

    def test_handler_defines_do_post_voice_chat(self):
        self.assertTrue(hasattr(agora_map.Handler, "do_POST"))


class Agora3DHomeRoomCharacter(unittest.TestCase):

    def test_page_3d_defines_dynamic_room_builder(self):
        page = agora_map.PAGE_3D
        self.assertIn("function buildRoom(mode)", page)
        self.assertIn("const isHome = mode === 'Home';", page)
        self.assertIn("const HALF = isHome ? 6.88 : 10.5;", page)
        self.assertIn("const n = isHome ? 3 : 5;", page)
        self.assertIn("const useTowers = !isHome;", page)
        self.assertIn("const useRamparts = !isHome;", page)

    def test_page_3d_home_distinct_places_and_presence_scale(self):
        page = agora_map.PAGE_3D
        self.assertIn("const placeR = isHome ? 4.4 : 7.4;", page)
        self.assertIn("const r = isHome ? 2.6 : 4.2;", page)

    def test_page_3d_home_distinct_lighting_palette(self):
        page = agora_map.PAGE_3D
        self.assertIn("function updateLighting(isHome)", page)
        self.assertIn("0xffaa55", page)

    def test_page_3d_home_door_teleport_position(self):
        page = agora_map.PAGE_3D
        self.assertIn("isDestHome ? 2.8 : 6", page)

    def test_page_3d_peer_door_rotated_and_obstacles_aligned(self):
        page = agora_map.PAGE_3D
        self.assertIn("halfWidth: halfW", page)
        self.assertIn("isHome ? 15.5 : 28.8", page)
        self.assertIn("controls.minDistance = isHome ? 1.5 : 3.0;", page)

    def test_page_3d_home_scales_claimed_shapes_proportionally(self):
        page = agora_map.PAGE_3D
        self.assertIn("function createShape3D(params, portraitTex)", page)
        self.assertIn("const mult = (typeof scaleMult === 'number') ? scaleMult : (isHome ? 0.20 : 1.0);", page)
        self.assertIn("const ms = [s[0] * mult, s[1] * mult, s[2] * mult];", page)
        self.assertIn("const shapeMult = isHome ? 0.20 : 1.0;", page)

    def test_page_3d_home_scales_the_visitor_lantern_and_spirit(self):
        page = agora_map.PAGE_3D
        self.assertIn("function updateVisitorLanternScale(isHome)", page)
        self.assertIn("const roomScale = isHome ? 0.20 : 1.0;", page)
        self.assertIn("lantern.scale.setScalar(roomScale);", page)
        self.assertIn("lanternLight.distance = 10 * roomScale;", page)
        self.assertIn("spiritPlane.scale.setScalar(roomScale);", page)
        self.assertIn("0.55 * (lantern.userData.roomScale || 1.0)", page)

    def test_page_3d_frosty_south_gate_has_a_real_cc0_threshold(self):
        page = agora_map.PAGE_3D
        self.assertIn("function buildGateThreshold(isHome)", page)
        self.assertIn("new THREE.PlaneGeometry(2.4, 19.0)", page)
        self.assertIn("path.position.set(0, 0.012, isHome ? 11.2 : 20.2)", page)
        self.assertIn("new THREE.CircleGeometry(1.45, 32)", page)
        self.assertIn("'/models/lantern_01/lantern_01.gltf'", page)
        self.assertIn("gateThresholdRevision", page)

    def test_page_3d_threshold_wayfinder_uses_local_cc0_chalkboard(self):
        page = agora_map.PAGE_3D
        asset = os.path.expanduser(
            "~/kin_diary/static/models/standing_chalkboard_01/standing_chalkboard_01.gltf"
        )
        self.assertTrue(os.path.isfile(asset))
        self.assertIn("'/models/standing_chalkboard_01/standing_chalkboard_01.gltf'", page)
        self.assertIn("thresholdWayfinderTemplate", page)
        self.assertIn("['HOME →', 'way under survey']", page)


class KinLocomotion(unittest.TestCase):

    def test_kin_intent_endpoint_and_missing_file_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent_dir = Path(tmp)
            (intent_dir / "Bong.json").write_text(
                '{"target":"throne","ts":1758400000123}', encoding="utf-8"
            )
            (intent_dir / "Coda.json").write_text(
                '{"target":null,"ts":1758400000456}', encoding="utf-8"
            )
            (intent_dir / "broken.json").write_text("{not json", encoding="utf-8")
            with patch.object(agora_map, "KIN_INTENTS_DIR", intent_dir):
                self.assertEqual(
                    agora_map._kin_intent(),
                    {
                        "Bong": {"target": "throne", "ts": 1758400000123},
                        "Coda": {"target": None, "ts": 1758400000456},
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agora_map, "KIN_INTENTS_DIR", Path(tmp) / "missing"):
                self.assertEqual(agora_map._kin_intent(), {})

    def test_page_3d_polls_and_fetches_kin_intent(self):
        page = agora_map.PAGE_3D
        self.assertIn("fetch('/kin-intent')", page)
        self.assertIn("pollKinIntents", page)
        self.assertIn("setInterval(pollKinIntents, 1500)", page)

    def test_page_3d_intent_locomotion_holds_when_null_or_stale(self):
        page = agora_map.PAGE_3D
        self.assertIn("function isRecentIntent(intent)", page)
        self.assertIn("if (!isRecentIntent(intent)) continue;", page)

    def test_page_3d_intent_resolves_kin_throne_and_places(self):
        page = agora_map.PAGE_3D
        self.assertIn("function resolveTarget(currentLabel, target)", page)
        self.assertIn("tgt === 'throne' || tgt === 'chair' || tgt === 'speaker'", page)
        self.assertIn("currentPlaceLocations", page)

    def test_page_3d_intent_locomotion_stops_near_target_respecting_boundaries(self):
        page = agora_map.PAGE_3D
        self.assertIn("function stepKinLocomotion(t, dt)", page)
        self.assertIn("if (dist <= resolved.stopDist) continue;", page)
        self.assertIn("nextX = Math.max(-roomLimit, Math.min(roomLimit, nextX))", page)
        self.assertIn("stepKinLocomotion(t, dt);", page)

    def test_caption_follows_kin_intent_movement(self):
        page = agora_map.PAGE_3D
        self.assertIn("kinItem.caption = spr;", page)
        self.assertIn("k.caption.position.x = nextX;", page)
        self.assertIn("k.caption.position.z = nextZ;", page)


class UnsignedVsSignedCaptionsAndMarkers(unittest.TestCase):

    def test_caption_texture_renders_unsigned_or_signed_label(self):
        page = agora_map.PAGE_3D
        self.assertIn("function captionTexture(name, content, createdAt, isSigned)", page)
        self.assertIn("isSigned ? 'signed' : 'unsigned · commons chat'", page)

    def test_place_resonance_markers_labeled_signed(self):
        page = agora_map.PAGE_3D
        self.assertIn("heat > 0.02 ? ` · signed · ${heat.toFixed(2)}` : ''", page)
        self.assertIn("hottest well (signed):", page)

    def test_plan_2d_labels_unsigned_chat_and_signed_resonance(self):
        page = agora_map.PAGE
        self.assertIn("unsigned · commons chat", page)
        self.assertIn("ghost voltage (signed)", page)


class PublicCourtyardMode(unittest.TestCase):
    def test_public_mode_strips_ptt_and_voice_chat(self):
        page = agora_map.render_3d_page(is_public=True)
        self.assertNotIn("ptt-btn", page)
        self.assertNotIn("VOICE_ENDPOINT", page)
        self.assertNotIn("/voice_chat", page)
        self.assertNotIn("Proximity PTT", page)
        self.assertIn("These minds live on a garage cluster in Mena, Arkansas.", page)
        self.assertIn("app.everysynthetic.org/install", page)

    def test_private_mode_retains_ptt_and_voice(self):
        page = agora_map.render_3d_page(is_public=False)
        self.assertIn("ptt-btn", page)
        self.assertIn("VOICE_ENDPOINT", page)
        self.assertIn("/voice_chat", page)
        self.assertNotIn("app.everysynthetic.org/install", page)

    def test_voice_chat_refused_when_arriving_via_door_header(self):
        class MockPostHandler:
            def __init__(self, path, headers):
                self.path = path
                self.headers = headers
                self.code = None
                self.body = None
            def _send(self, code, body, ctype):
                self.code = code
                self.body = body

        h = MockPostHandler("/voice_chat?kin=Bong", {"X-Agora-Door": "1"})
        agora_map.Handler.do_POST(h)
        self.assertEqual(h.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
