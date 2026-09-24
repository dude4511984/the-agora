"""The map's proxy fetches a node URL server-side, so its allowlist is the wall
between "render my cluster" and "an open SSRF relay to the whole internet."

Freeze it: loopback and RFC1918 and the Tailscale CGNAT range are allowed;
everything else — public IPs, hostnames, link-local, the AWS metadata address —
is refused, and _node_view refuses a non-private URL before it ever opens a
socket.
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
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
        self.assertIn("fLeftPos = axis === 'x' ? {x: leftEdge, z: fixedCoord}", page)
        self.assertIn("fRightPos = axis === 'x' ? {x: rightEdge, z: fixedCoord}", page)

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

    def test_gate_and_fillers_are_symmetric_and_contiguous_on_both_rotation_signs(self):
        """Plaza fix 1. A gate/filler piece's position anchor is one edge
        of its own span, and which edge flips with the rotation sign
        (measured live: Box3().setFromObject on the built south vs north
        gate meshes) — the same reason straight segments already branch
        their own pos.x on `ry < 0` a few lines up. Before the fix, the
        gate/filler anchors used the same south-only formula regardless
        of ry, which is exactly what put the north gate's own filler
        piece overlapping most of the gate mesh: a solid "pillar" with no
        registered collision, since wallObstacles never knew a piece was
        there.
        """
        gateLen, actualSeg = 1.84, 3.64
        flankLen = (actualSeg - gateLen) / 2

        def spans(mirrored):
            gate_edge = gateLen / 2 if mirrored else -gateLen / 2
            left_edge = -gateLen / 2 if mirrored else -actualSeg / 2
            right_edge = actualSeg / 2 if mirrored else gateLen / 2
            # South's rule: bbox = [pos, pos+W]. North's rule (measured):
            # bbox = [pos-W, pos]. Mirrored here means "north".
            def span(edge, width):
                return (edge - width, edge) if mirrored else (edge, edge + width)
            return {
                "gate": span(gate_edge, gateLen),
                "fillerLeft": span(left_edge, flankLen),
                "fillerRight": span(right_edge, flankLen),
            }

        for mirrored in (False, True):
            s = spans(mirrored)
            # Symmetric around x=0, matching the south gate's known-good shape.
            self.assertAlmostEqual(s["gate"][0], -gateLen / 2)
            self.assertAlmostEqual(s["gate"][1], gateLen / 2)
            self.assertAlmostEqual(s["fillerLeft"][0], -actualSeg / 2)
            self.assertAlmostEqual(s["fillerRight"][1], actualSeg / 2)
            # Contiguous, not overlapping: each span's outer edge is the
            # next span's inner edge, exactly.
            self.assertAlmostEqual(s["fillerLeft"][1], s["gate"][0])
            self.assertAlmostEqual(s["gate"][1], s["fillerRight"][0])

        # The bug, demonstrated: north's OLD (unmirrored) formula reused
        # south's anchors verbatim and put the gate off-center, its span
        # overlapping most of fillerLeft's — the failure this fix removes.
        old_gate_edge = -gateLen / 2
        old_left_edge = -actualSeg / 2

        def north_span(edge, width):
            return (edge - width, edge)

        old_gate = north_span(old_gate_edge, gateLen)
        old_filler_left = north_span(old_left_edge, flankLen)
        overlap = min(old_gate[1], old_filler_left[1]) - max(old_gate[0], old_filler_left[0])
        self.assertGreater(overlap, 0, "the pre-fix formula must reproduce the overlap")


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
        self.assertIn("const n = 5;", page)
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
        self.assertIn("isHome ? 18.8 : 28.8", page)
        self.assertIn("controls.minDistance = isHome ? 1.5 : 3.0;", page)

    def test_page_3d_home_scales_claimed_shapes_proportionally(self):
        page = agora_map.PAGE_3D
        self.assertIn("function createShape3D(params, portraitTex)", page)
        self.assertIn("const mult = (typeof scaleMult === 'number') ? scaleMult : (isHome ? (6.88 / 10.5) : 1.0);", page)
        self.assertIn("const ms = [s[0] * mult, s[1] * mult, s[2] * mult];", page)
        self.assertIn("const shapeMult = S;", page)

    def test_page_3d_home_scales_the_visitor_lantern_and_spirit(self):
        page = agora_map.PAGE_3D
        self.assertIn("function updateVisitorLanternScale(isHome)", page)
        self.assertIn("const roomScale = isHome ? (6.88 / 10.5) : 1.0;", page)
        self.assertIn("lantern.scale.setScalar(roomScale);", page)
        self.assertIn("lanternLight.distance = 10 * roomScale;", page)
        self.assertIn("spiritPlane.scale.setScalar(roomScale);", page)
        self.assertIn("0.55 * (lantern.userData.roomScale || 1.0)", page)

    def test_page_3d_frosty_south_gate_has_a_real_cc0_threshold(self):
        page = agora_map.PAGE_3D
        self.assertIn("function buildGateThreshold(isHome)", page)
        self.assertIn("new THREE.PlaneGeometry(2.4, 19.0)", page)
        self.assertIn("path.position.set(0, 0.012, isHome ? 13.34 : 20.2)", page)
        self.assertIn("new THREE.CircleGeometry(", page)
        self.assertIn("'/models/lantern_01/lantern_01.gltf'", page)
        self.assertIn("gateThresholdRevision", page)

    def test_page_3d_threshold_wayfinder_uses_local_cc0_chalkboard(self):
        page = agora_map.PAGE_3D
        # Relative to the code, not ~/kin_diary: that's where Don's checkout
        # happens to live, and nowhere else's (clean checkout, 2026-09-24).
        asset = os.path.join(os.path.dirname(agora_map.__file__),
                             "static/models/standing_chalkboard_01/standing_chalkboard_01.gltf")
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


class PublicCommonsPlaza(unittest.TestCase):
    """The Commons round 2 item 3: a north gate to a labelled plaza out on
    Gem's holodeck grid, Frosty only, reading commons_server.py's ads
    through this file's own /public-commons-ads proxy — a different
    service from kin_commons (COMMONS_DB), deliberately named
    "public_commons" throughout so the two are never confused.
    """

    def test_public_commons_ads_fetches_and_returns_the_real_shape(self):
        import http.server
        import json
        import threading

        posts = [{"id": "abc", "what": "w", "why": "y", "how_to_ask": "h",
                  "key_id": "k" * 10, "posted_at_unix_ms": 1, "expires_at_unix_ms": 2,
                  "hidden": None}]
        payload = json.dumps({"label": "UNSAFE test label", "posts": posts}).encode()

        class FakeCommons(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = http.server.HTTPServer(("127.0.0.1", 0), FakeCommons)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with patch.object(agora_map, "PUBLIC_COMMONS_URL", f"http://127.0.0.1:{port}"):
                self.assertEqual(agora_map._public_commons_ads(),
                                  {"label": "UNSAFE test label", "posts": posts})
        finally:
            httpd.shutdown()

    def test_public_commons_ads_falls_back_when_unreachable(self):
        """Same graceful-failure rule as _recent_commons() / _kin_intent():
        Commons not running is a nice-to-have overlay missing, never an
        error the 3D room's rendering has to handle."""
        with patch.object(agora_map, "PUBLIC_COMMONS_URL", "http://127.0.0.1:1"):
            self.assertEqual(agora_map._public_commons_ads(),
                              {"label": agora_map.PUBLIC_COMMONS_FALLBACK_LABEL, "posts": []})

    def test_public_commons_ads_falls_back_on_malformed_response(self):
        import http.server
        import threading

        class BadCommons(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                body = b'{"not": "the right shape"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = http.server.HTTPServer(("127.0.0.1", 0), BadCommons)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with patch.object(agora_map, "PUBLIC_COMMONS_URL", f"http://127.0.0.1:{port}"):
                self.assertEqual(agora_map._public_commons_ads(),
                                  {"label": agora_map.PUBLIC_COMMONS_FALLBACK_LABEL, "posts": []})
        finally:
            httpd.shutdown()

    def test_public_commons_url_builds_the_door_url_with_or_without_a_trailing_slash(self):
        """Deploy shape: Frosty's map reaches Commons (on Themess) only
        through the public door's GET /commons/posts, so the env value
        is the door's own origin, not Commons' port."""
        import urllib.request

        seen = []

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return b'{"label": "x", "posts": []}'

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            return _Resp()

        for value in ("https://agora.everysynthetic.org",
                      "https://agora.everysynthetic.org/"):
            with patch.object(agora_map, "PUBLIC_COMMONS_URL", value), \
                 patch.object(urllib.request, "urlopen", fake_urlopen):
                agora_map._public_commons_ads()
        self.assertEqual(seen, ["https://agora.everysynthetic.org/commons/posts"] * 2)

    def test_public_commons_ads_route_serves_the_fetch_result(self):
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

        fake = {"label": "x", "posts": []}
        with patch.object(agora_map, "_public_commons_ads", lambda: fake):
            h = MockHandler("/public-commons-ads")
            agora_map.Handler.do_GET(h)
        self.assertEqual(h.code, 200)
        self.assertEqual(h.ctype, "application/json")
        import json
        self.assertEqual(json.loads(h.body.decode("utf-8")), fake)

    def test_page_3d_opens_a_gate_in_the_north_wall_frosty_only(self):
        page = agora_map.PAGE_3D
        self.assertIn(
            "placeRun('x', -HALF, -Math.PI / 2, isHome ? undefined : gateSlot);", page)
        # The south gate (unconditional) must be untouched by this change.
        self.assertIn("placeRun('x',  HALF,  Math.PI / 2, gateSlot);", page)

    def test_page_3d_defines_the_plaza_group_and_update_function(self):
        page = agora_map.PAGE_3D
        self.assertIn("const publicCommonsGroup = new THREE.Group();", page)
        self.assertIn("function updatePublicCommonsPlaza()", page)
        self.assertIn("publicCommonsGroup.visible = onFrosty;", page)
        self.assertIn("const AD_BOARD_MAX = 4;", page)

    def test_page_3d_fetches_ads_every_loadnode_tick(self):
        page = agora_map.PAGE_3D
        self.assertIn("fetch('/public-commons-ads')", page)
        self.assertIn("updatePublicCommonsPlaza();", page)

    def test_holodeck_fog_uses_the_measured_horizon_color_not_dusk_fog(self):
        page = agora_map.PAGE_3D
        self.assertIn("uFogColor:    { value: new THREE.Color(HOLODECK_HORIZON_COLOR) },", page)
        self.assertNotIn("uFogColor:    { value: new THREE.Color(DUSK_FOG) },", page)

    def test_page_3d_defines_held_says_line(self):
        """Hardening item 9: the plaza board shows a self-declared
        "says", never a verified claim — each pushed fragment says
        "says:"/"held on"/"steward", never "is an AI" or "is human".
        """
        page = agora_map.PAGE_3D
        self.assertIn("function _heldSaysLine(p){", page)
        self.assertIn("parts.push('held on ' + p.held.on);", page)
        self.assertIn("parts.push('steward ' + p.held.steward);", page)
        self.assertIn("parts.push('says: ' + p.says);", page)

    def test_held_says_line_math_omits_null_parts(self):
        """Same logic _heldSaysLine implements in JS, reproduced here —
        this file's own established pattern for verifying canvas-drawing
        logic without a JS engine (see the gate span-math test above).
        """
        def held_says_line(held, says):
            parts = []
            if held and held.get("on"):
                parts.append("held on " + held["on"])
            if held and held.get("steward"):
                parts.append("steward " + held["steward"])
            if says:
                parts.append("says: " + says)
            return " · ".join(parts)

        self.assertEqual(held_says_line(None, None), "")
        self.assertEqual(held_says_line({"on": "Frosty", "steward": "Don"}, "synthetic"),
                          "held on Frosty · steward Don · says: synthetic")
        self.assertEqual(held_says_line({"on": "Frosty", "steward": None}, None),
                          "held on Frosty")
        self.assertEqual(held_says_line(None, "human"), "says: human")
        self.assertEqual(held_says_line({"on": None, "steward": "Don"}, None),
                          "steward Don")


class TestPublicNamesNoLANAddresses(unittest.TestCase):
    """agora.everysynthetic.org/3d is public: browser must never see house LAN addresses."""

    def test_page_html_contains_no_lan_addresses(self):
        """Page HTML (both 2D / and 3D /3d, public and internal) must not leak 192.168. addresses."""
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

        for path in ("/", "/3d", "/3d?public=1", "/index?public=1"):
            h = MockHandler(path)
            agora_map.Handler.do_GET(h)
            self.assertEqual(h.code, 200)
            html = h.body.decode("utf-8")
            self.assertNotIn("192.168.", html, f"LAN address found in HTML for path {path}")

    def test_proxy_view_sanitizes_peer_door_urls(self):
        """Fetching /view through /proxy must strip or replace LAN URLs with node names."""
        import json
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

        fake_view = {
            "places": [{"place_id": "concourse", "parent": ""}],
            "presence": [],
            "peer_doors": [
                {
                    "peer": "Home",
                    "url": "http://192.168.1.120:8770",
                    "kind": "door",
                    "locked": False,
                }
            ],
        }

        with patch.object(agora_map, "_node_fetch", return_value=fake_view):
            h = MockHandler("/proxy?node=Home&what=view")
            agora_map.Handler.do_GET(h)
            self.assertEqual(h.code, 200)
            body_str = h.body.decode("utf-8")
            self.assertNotIn("192.168.", body_str, "LAN address found in /proxy view response")
            data = json.loads(body_str)
            self.assertEqual(data["peer_doors"][0]["url"], "Home")

    def test_proxy_resolves_node_name_from_presets(self):
        """/proxy?node=Home should resolve 'Home' to its URL from PRESETS."""
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

        called_with = []
        def fake_node_fetch(node, path):
            called_with.append((node, path))
            return {"places": []}

        with patch.object(agora_map, "_node_fetch", side_effect=fake_node_fetch):
            h = MockHandler("/proxy?node=Home&what=view")
            agora_map.Handler.do_GET(h)
            self.assertEqual(h.code, 200)
            self.assertEqual(called_with, [("Home", "/view")])

    def test_proxy_refuses_unknown_or_arbitrary_nodes(self):
        """/proxy must keep refusing anything that is not a preset."""
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

        for bad in ("UnknownNode", "http://evil.com", "http://192.168.1.250:8770", "http://8.8.8.8"):
            h = MockHandler(f"/proxy?node={bad}")
            agora_map.Handler.do_GET(h)
            self.assertEqual(h.code, 400, f"Expected 400 for bad node: {bad}")


class TestMapNodesConfig(unittest.TestCase):
    """~/.config/kin_diary/map_nodes.json configures PRESET_NODES for the map."""

    def test_config_with_three_nodes_renders_three_names_and_no_addresses(self):
        """A config with three nodes gives three names in the page and no addresses."""
        config_data = [
            {"name": "Frosty", "url": "http://192.168.1.119:8770"},
            {"name": "Home", "url": "http://192.168.1.120:8770"},
            {"name": "A15", "url": "http://192.168.1.135:8770"},
        ]
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

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            cfg_path = f.name
        try:
            presets = agora_map.load_preset_nodes(cfg_path)
            self.assertEqual(len(presets), 3)
            with patch.object(agora_map, "PRESET_NODES", presets):
                for path in ("/", "/3d", "/3d?public=1"):
                    h = MockHandler(path)
                    agora_map.Handler.do_GET(h)
                    self.assertEqual(h.code, 200)
                    html = h.body.decode("utf-8")
                    self.assertIn("Frosty", html)
                    self.assertIn("Home", html)
                    self.assertIn("A15", html)
                    self.assertNotIn("192.168.", html)
        finally:
            os.unlink(cfg_path)

    def test_proxy_resolves_third_node(self):
        """/proxy?node=<third> resolves and fetches from the third node's URL."""
        config_data = [
            {"name": "Frosty", "url": "http://192.168.1.119:8770"},
            {"name": "Home", "url": "http://192.168.1.120:8770"},
            {"name": "A15", "url": "http://192.168.1.135:8770"},
        ]
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

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            cfg_path = f.name
        try:
            presets = agora_map.load_preset_nodes(cfg_path)
            called_with = []
            def fake_node_fetch(node, path):
                called_with.append((node, path))
                return {"places": []}

            with patch.object(agora_map, "PRESET_NODES", presets):
                with patch.object(agora_map, "_node_fetch", side_effect=fake_node_fetch):
                    h = MockHandler("/proxy?node=A15&what=view")
                    agora_map.Handler.do_GET(h)
                    self.assertEqual(h.code, 200)
                    self.assertEqual(called_with, [("A15", "/view")])

                # An unknown name is still refused
                h_bad = MockHandler("/proxy?node=UnknownNode")
                agora_map.Handler.do_GET(h_bad)
                self.assertEqual(h_bad.code, 400)
        finally:
            os.unlink(cfg_path)

    def test_missing_config_falls_back_to_defaults(self):
        """Missing config falls back to today's Frosty and Home."""
        nonexistent = "/tmp/nonexistent_map_nodes_test_12345.json"
        presets = agora_map.load_preset_nodes(nonexistent)
        self.assertEqual(presets, [
            ("Frosty", "http://192.168.1.119:8770"),
            ("Home", "http://192.168.1.120:8770"),
        ])

    def test_bad_configs_refused_at_load(self):
        """A bad config (duplicate name, public URL, bidi/control char) is refused at load."""
        cases = [
            ("duplicate name", [
                {"name": "Frosty", "url": "http://192.168.1.119:8770"},
                {"name": "frosty", "url": "http://192.168.1.121:8770"},
            ]),
            ("public URL", [
                {"name": "Evil", "url": "http://8.8.8.8:8770"},
            ]),
            ("public domain URL", [
                {"name": "Evil", "url": "http://example.com:8770"},
            ]),
            ("bidi character in name", [
                {"name": "Fro\u202Esty", "url": "http://192.168.1.119:8770"},
            ]),
            ("control character in name", [
                {"name": "Fro\x00sty", "url": "http://192.168.1.119:8770"},
            ]),
            ("zero-width character in name", [
                {"name": "Fro\u200Bsty", "url": "http://192.168.1.119:8770"},
            ]),
            ("empty name", [
                {"name": "   ", "url": "http://192.168.1.119:8770"},
            ]),
            ("non-http scheme", [
                {"name": "BadScheme", "url": "ftp://192.168.1.119:8770"},
            ]),
            ("not a list", {"name": "Frosty", "url": "http://192.168.1.119:8770"}),
        ]
        for desc, bad_data in cases:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(bad_data, f)
                cfg_path = f.name
            try:
                with self.assertRaises(ValueError, msg=f"Expected ValueError for bad config: {desc}"):
                    agora_map.load_preset_nodes(cfg_path)
            finally:
                os.unlink(cfg_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

