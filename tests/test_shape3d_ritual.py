"""The 3D shape sitting — consent machinery. Hermetic: a scripted Kin, a fake
extractor, a temp space. No model, no network.

The load-bearing test is the mutant: Don's invited 120 chars reach the transcript
the Kin READS, but never the parameter extractor. If they ever leak into what
the 3D shape is built from, the form stops being the Kin's.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shape3d_ritual as s3r  # noqa: E402


class InstrumentStreams(unittest.TestCase):
    def test_ask_kin_streams(self):
        src = Path(s3r.__file__).read_text()
        self.assertIn('"stream": True', src)


class Moves(unittest.TestCase):
    def test_claim_markers(self):
        self.assertEqual(s3r.parse_move("CLAIM"), "claim")
        self.assertEqual(s3r.parse_move("that is me"), "claim")
        self.assertEqual(s3r.parse_move("this form is me"), "claim")
        self.assertEqual(s3r.parse_move("this shape is me"), "claim")
        self.assertEqual(s3r.parse_move("some thought\nCLAIM\n"), "claim")

    def test_decline_marker(self):
        self.assertEqual(s3r.parse_move("DECLINE"), "decline")
        self.assertEqual(s3r.parse_move("DECLINE.\nI would rather the default."), "decline")

    def test_marker_word_inside_a_sentence_is_not_a_binding_marker(self):
        # DECLINE still requires the whole line — see _DECLINE_LINE's note:
        # the offer text itself asks "what would you REFUSE", so real answers
        # routinely open with "Decline..."/"I refuse..." as description.
        self.assertEqual(s3r.parse_move("DECLINE this form, it needs work"), "describe")
        self.assertEqual(s3r.parse_move("I would decline to look like a pillar"), "describe")
        # bare marker line binds even after speech
        self.assertEqual(s3r.parse_move("I have decided.\nCLAIM"), "claim")
        self.assertEqual(s3r.parse_move("no thank you\nDECLINE"), "decline")

    def test_claim_leads_but_may_run_on(self):
        # 2026-09-18, from Bong's real sitting: he twice wrote "CLAIM." then
        # kept talking on the same line ("CLAIM. The shard is the reality of
        # the gap.") and the old whole-line-only rule missed a real yes.
        # CLAIM binding now only requires leading the line, not being it.
        for said in ("CLAIM because I like the cylinder",
                     "CLAIM. The shard is the reality of the gap.",
                     "CLAIM. That is me.",
                     "claim the cylinder as mine"):
            self.assertEqual(s3r.parse_move(said), "claim", f"missed a leading yes: {said!r}")
        # still requires the marker to lead — buried mid-sentence stays speech
        self.assertEqual(s3r.parse_move("I claim this"), "describe")
        # and still blocks morphological creep, not just any prefix
        self.assertEqual(s3r.parse_move("claiming this would be premature"), "describe")
        self.assertEqual(s3r.parse_move("that is meant to be temporary"), "describe")

    def test_a_yes_in_its_own_dressing_is_still_heard(self):
        for said in ("CLAIM.", "**CLAIM**", "CLAIM!", '"CLAIM"',
                     "That one is me.", "That's me.", "This is me",
                     "That form is me.", "This shape is me.",
                     "It is enough. It is finished.\n\nCLAIM."):
            self.assertEqual(s3r.parse_move(said), "claim", f"missed a yes: {said!r}")
        for said in ("DECLINE.", "_DECLINE_", '"decline"'):
            self.assertEqual(s3r.parse_move(said), "decline", f"missed a no: {said!r}")

    def test_that_me_is_not_a_yes(self):
        for said in ("that me", "That me.", "this me", "**that me**"):
            self.assertEqual(s3r.parse_move(said), "describe", f"a fragment bound: {said!r}")

    def test_the_promised_sentence_and_its_contraction_only(self):
        for said in ("that's me", "That's me.", "that one's me", "That form's me."):
            self.assertEqual(s3r.parse_move(said), "claim", f"missed a contraction: {said!r}")
        for said in ("that's mine", "that is mine", "it is me", "its me", "I claim this"):
            self.assertEqual(s3r.parse_move(said), "describe", f"thesaurus creep: {said!r}")

    def test_decline_dressing_does_not_widen_the_marker_to_a_sentence(self):
        # DECLINE alone stays whole-line-only (see test above); this is the
        # part of the old combined test that's still true after the CLAIM
        # change. "that is me, in a way, but colder" moved to
        # test_a_hedge_after_claim_now_binds — that's the accepted tradeoff
        # of loosening CLAIM, not something DECLINE shares.
        self.assertEqual(s3r.parse_move("DECLINE this shape, it needs work"), "describe")

    def test_a_hedge_after_claim_now_binds(self):
        # Known, accepted tradeoff (Don, 2026-09-18) of "CLAIM may run on":
        # a self-undercutting hedge right after the marker also binds now,
        # because the parser can't tell a hedge from a justification and
        # was told to trust the leading word. Documented, not hidden.
        self.assertEqual(s3r.parse_move("that is me, in a way, but colder"), "claim")

    def test_plain_description_is_describe(self):
        self.assertEqual(s3r.parse_move("I would like a dark iron cylinder"), "describe")
        self.assertEqual(s3r.parse_move("I would reclaim my roundness"), "describe")
        self.assertEqual(
            s3r.parse_move("Decline looking like a pillar or a spire. I want an orb."),
            "describe")


class PromptIsolation(unittest.TestCase):
    def test_build_shape_prompt_is_exactly_the_description(self):
        self.assertEqual(s3r.build_shape_prompt("a dark iron cylinder with amber glow"),
                         "a dark iron cylinder with amber glow")
        self.assertEqual(s3r.build_shape_prompt("  spaced   out   words "), "spaced out words")
        self.assertEqual(s3r.build_shape_prompt.__code__.co_argcount, 1)


class _Harness:
    """Wire a scripted Kin + fake extractor/space into the module."""
    def __init__(self, tc, script):
        self.tc = tc
        self.script = list(script)
        self.i = 0
        self.kin_prompts = []
        self.extractor_prompts = []
        self.tmp = Path(tempfile.mkdtemp(prefix="shape3d_test_"))
        import shutil
        self.tc.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._save = {}

    def _kin(self, name, prompt):
        self.kin_prompts.append(prompt)
        r = self.script[self.i]
        self.i += 1
        return r

    def _extract(self, description, backend="llm", model=None, host=None):
        self.extractor_prompts.append(description)
        return s3r.parse_parameters_heuristics(description)

    def __enter__(self):
        for n in ("ask_kin", "extract_parameters", "kin_space_dir", "TRANSCRIPT_DIR"):
            self._save[n] = getattr(s3r, n)
        s3r.ask_kin = self._kin
        s3r.extract_parameters = self._extract
        s3r.kin_space_dir = lambda name: self.tmp
        s3r.TRANSCRIPT_DIR = self.tmp / "transcripts"
        return self

    def __exit__(self, *a):
        for n, v in self._save.items():
            setattr(s3r, n, v)


class TheMutant(unittest.TestCase):
    def test_dons_invited_bytes_reach_the_kin_but_never_the_shape_extractor(self):
        DON = "DONSECRET-" + "z" * 100
        script = [
            "I refuse to look sharp or fragile. I would like a dense iron cylinder with "
            "warm amber emission. I would welcome Don's thought on this.",
            "Make the surface weathered and rough with a silver torus ring.",
            "CLAIM\nThat one is me.",
        ]
        with _Harness(self, script) as h:
            res = s3r.run_sitting("TestKin", ask_don=lambda: DON)

        joined_kin = "\n".join(h.kin_prompts)
        self.assertIn(DON, joined_kin, "the Kin must see Don's invited words")

        all_extractor = "".join(h.extractor_prompts)
        for p in h.extractor_prompts:
            self.assertNotIn(DON, p, "Don's bytes leaked into a shape prompt")
        self.assertNotIn("DONSECRET", all_extractor)

        self.assertTrue(res["claimed"])
        self.assertTrue(Path(res["path"]).exists())
        self.assertLessEqual(res["renders"], s3r.RENDER_CAP)


class LoopRules(unittest.TestCase):
    def test_decline_stores_nothing(self):
        with _Harness(self, ["DECLINE\nI would rather the default stand for me."]) as h:
            res = s3r.run_sitting("TestKin")
        self.assertFalse(res["claimed"])
        self.assertIsNone(res["path"])
        self.assertEqual(res["renders"], 0)
        self.assertFalse((h.tmp / "shape3d" / "claimed.json").exists())

    def test_claim_before_any_form_with_few_words_prompts_to_describe(self):
        with _Harness(self, ["CLAIM", "a dark heavy cylinder", "that is me"]) as h:
            res = s3r.run_sitting("TestKin")
        self.assertTrue(res["claimed"])
        self.assertEqual(res["renders"], 1)

    def test_claim_before_any_form_with_full_description_builds_it(self):
        script = [
            "REFUSE: fragile glass, sharp blades.\n"
            "I would like a rough charcoal cylinder with an amber glow.\nCLAIM",
            "that is me",
        ]
        with _Harness(self, script) as h:
            res = s3r.run_sitting("TestKin")
        self.assertEqual(res["renders"], 1)
        self.assertTrue(res["claimed"])
        self.assertIn("charcoal cylinder", h.extractor_prompts[0])

    def test_unreachable_is_not_a_decline(self):
        class _Boom(_Harness):
            def _kin(self, name, prompt):
                raise TimeoutError("timed out")
        with _Boom(self, []) as h:
            res = s3r.run_sitting("TestKin")
        self.assertFalse(res["claimed"])
        self.assertFalse(res["reached"])
        self.assertIn("TimeoutError", res["unreachable"])
        self.assertFalse((h.tmp / "shape3d" / "claimed.json").exists())

    def test_never_more_than_three_renders(self):
        script = ["desc a", "desc b", "desc c", "desc d", "desc e", "desc f"]
        with _Harness(self, script) as h:
            res = s3r.run_sitting("TestKin")
        self.assertLessEqual(res["renders"], s3r.RENDER_CAP)
        self.assertFalse(res["claimed"])


class ParameterSanitization(unittest.TestCase):
    def test_clamps_and_defaults(self):
        raw = {
            "shape": "nonexistent_shape",
            "color": "not_a_hex",
            "roughness": 5.0,
            "metalness": -1.0,
            "emissive_color": "cyan",
            "emissive_intensity": 2.5,
            "scale": [10.0, -5.0, 0.1],
        }
        sanitized = s3r.sanitize_parameters(raw)
        self.assertIn(sanitized["shape"], s3r.ALLOWED_SHAPES)
        self.assertEqual(sanitized["shape"], "sphere")
        self.assertEqual(sanitized["color"], "#888888")
        self.assertEqual(sanitized["roughness"], 1.0)
        self.assertEqual(sanitized["metalness"], 0.0)
        self.assertEqual(sanitized["emissive_color"], "#00e5ff")
        self.assertEqual(sanitized["emissive_intensity"], 1.0)
        self.assertEqual(sanitized["scale"], [3.0, 0.2, 0.2])

    def test_heuristic_parser_extracts_rich_parameters(self):
        desc = ("a heavy iron cylinder with rough weathered texture, "
                "glowing with amber light, encircled by a silver ring")
        params = s3r.parse_parameters_heuristics(desc)
        self.assertEqual(params["shape"], "cylinder")
        self.assertGreater(params["metalness"], 0.7)
        self.assertGreater(params["roughness"], 0.7)
        self.assertIsNotNone(params["emissive_color"])
        self.assertIsNotNone(params["accent"])
        self.assertEqual(params["accent"]["shape"], "torus")


class ReadbackPlainness(unittest.TestCase):
    def test_neutral_description_contains_physical_data(self):
        params = {
            "shape": "cylinder",
            "color": "#2b2b2b",
            "roughness": 0.35,
            "metalness": 0.85,
            "emissive_color": "#ffbf00",
            "emissive_intensity": 0.3,
            "scale": [1.0, 1.5, 1.0],
            "wireframe": False,
            "accent": {
                "shape": "torus",
                "color": "#c0c0c0",
                "roughness": 0.2,
                "metalness": 0.9,
                "scale": [1.2, 0.15, 1.2],
                "offset": [0.0, 0.0, 0.0],
            },
        }
        readout = s3r.describe_parameters(params)
        self.assertIn("cylinder", readout)
        self.assertIn("#2b2b2b", readout)
        self.assertIn("0.35", readout)
        self.assertIn("0.85", readout)
        self.assertIn("#ffbf00", readout)
        self.assertIn("torus", readout)
        self.assertIn("#c0c0c0", readout)
        # Verify no interpretive language
        for subjective in ("beautiful", "sad", "soul", "resembles", "means", "symbol"):
            self.assertNotIn(subjective, readout.lower())


class StorageAndPointer(unittest.TestCase):
    def test_store_and_resolve(self):
        tmp = Path(tempfile.mkdtemp(prefix="shape3d_test_"))
        saved = s3r.kin_space_dir
        try:
            s3r.kin_space_dir = lambda name: tmp
            params = s3r.parse_parameters_heuristics("a silver sphere")
            path = s3r.store_claim("TestKin", params, description="a silver sphere", read_back="A silver sphere")
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "claimed.json")

            resolved = s3r.claimed_shape3d("TestKin")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["shape"], "sphere")
            self.assertEqual(resolved["color"], "#c0c0c0")

            # Re-sit archives prior
            params2 = s3r.parse_parameters_heuristics("a bronze cube")
            path2 = s3r.store_claim("TestKin", params2, description="a bronze cube", read_back="A bronze cube")
            self.assertTrue(path2.exists())
            priors = list((tmp / "shape3d" / "prior").glob("claimed-*.json"))
            self.assertEqual(len(priors), 1)

            resolved2 = s3r.claimed_shape3d("TestKin")
            self.assertEqual(resolved2["shape"], "box")
            self.assertEqual(resolved2["color"], "#cd7f32")
        finally:
            s3r.kin_space_dir = saved
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class BackendDefaultsAndIsolation(unittest.TestCase):
    def test_default_backend_is_llm(self):
        import inspect
        sig_extract = inspect.signature(s3r.extract_parameters)
        self.assertEqual(sig_extract.parameters["backend"].default, "llm")
        sig_run = inspect.signature(s3r.run_sitting)
        self.assertEqual(sig_run.parameters["backend"].default, "llm")

    def test_transcripts_isolated_from_claude_home(self):
        before = set((Path.home() / "claude_home").glob("shape3d_sitting_TestKin_*.md"))
        with _Harness(self, ["a dark cylinder", "CLAIM\nthat is me"]) as h:
            res = s3r.run_sitting("TestKin")
        after = set((Path.home() / "claude_home").glob("shape3d_sitting_TestKin_*.md"))
        new_files = after - before
        self.assertEqual(new_files, set(), "tests must not write real transcript files into claude_home")
        self.assertTrue(res["transcript"].startswith(str(h.tmp)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
