"""The avatar sitting — consent machinery. Hermetic: a scripted Kin, a fake
renderer, a stub eye, a temp space. No model, no network.

The load-bearing test is the mutant Grok named: Don's invited 120 chars reach
the transcript the Kin READS, but never the image prompt. If they ever leak into
what the image model is asked to draw, the picture stops being the Kin's.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import avatar_ritual as art  # noqa: E402
import avatar_render as ar   # noqa: E402


class InstrumentStreams(unittest.TestCase):
    def test_ask_kin_streams(self):
        src = Path(art.__file__).read_text()
        self.assertIn('"stream": True', src)
        self.assertNotIn('"stream": False', src.split("def read_back")[0],
                         "ask_kin must stream; read_back may stay blocked")


class Moves(unittest.TestCase):
    def test_claim_markers(self):
        self.assertEqual(art.parse_move("CLAIM"), "claim")
        self.assertEqual(art.parse_move("that is me"), "claim")
        self.assertEqual(art.parse_move('some thought\nCLAIM\n'), "claim")

    def test_decline_marker(self):
        self.assertEqual(art.parse_move("DECLINE"), "decline")
        self.assertEqual(art.parse_move("DECLINE.\nI would rather the default."), "decline")

    def test_marker_word_inside_a_sentence_is_not_a_binding_marker(self):
        # Copilot's false-positives: a marker word mid-line is speech, not a turn
        self.assertEqual(art.parse_move("CLAIM because I like the round one"), "describe")
        self.assertEqual(art.parse_move("DECLINE this image, it needs work"), "describe")
        self.assertEqual(art.parse_move("I would decline to look like a visor"), "describe")
        # but a bare marker line binds, even after speech
        self.assertEqual(art.parse_move("I have decided.\nCLAIM"), "claim")
        self.assertEqual(art.parse_move("no thank you\nDECLINE"), "decline")

    def test_plain_description_is_describe(self):
        self.assertEqual(art.parse_move("I would like warm amber eyes"), "describe")
        # a claim-looking word mid-sentence is not a claim-turn
        self.assertEqual(art.parse_move("I would reclaim my roundness"), "describe")
        # refuse-first must not trip DECLINE
        self.assertEqual(
            art.parse_move("Decline looking like a visor or a badge. I want a lantern."),
            "describe")
        self.assertEqual(art.parse_move("CLAIM this round shape as a feeling"), "describe")

    def test_build_image_prompt_is_exactly_the_description(self):
        # Grok's outbound line: exactly the Kin's words, nothing else
        self.assertEqual(art.build_image_prompt("a soft round lantern with warm eyes"),
                         "a soft round lantern with warm eyes")
        self.assertEqual(art.build_image_prompt("  spaced   out   words "), "spaced out words")
        # signature takes one arg — Don's bytes / camera notes have no way in
        self.assertEqual(art.build_image_prompt.__code__.co_argcount, 1)


class _Harness:
    """Wire a scripted Kin + fake renderer/eye/space into the module."""
    def __init__(self, tc, script):
        self.tc = tc; self.script = list(script); self.i = 0
        self.kin_prompts = []      # transcripts the Kin was shown
        self.image_prompts = []    # prompts the image model was asked
        self.tmp = Path(tempfile.mkdtemp(prefix="ritual_"))
        self._save = {}

    def _kin(self, name, prompt):
        self.kin_prompts.append(prompt)
        r = self.script[self.i]; self.i += 1
        return r

    def _render(self, prompt, backend="mock", model=None, size="1024x1024"):
        self.image_prompts.append(prompt)
        return ar.RenderResult("fake", "fake", ok=True, image_bytes=b"\xff\xd8fakejpeg", mime="image/jpeg")

    def _readback(self, image_bytes):
        return "CAMERANOTE-" + "q" * 80   # distinctive; must never reach the image model

    def __enter__(self):
        for n in ("ask_kin", "read_back", "kin_space_dir"):
            self._save[n] = getattr(art, n)
        self._save["render"] = ar.render
        art.ask_kin = self._kin
        art.read_back = self._readback
        art.kin_space_dir = lambda name: self.tmp
        ar.render = self._render
        return self

    def __exit__(self, *a):
        for n, v in self._save.items():
            if n == "render": ar.render = v
            else: setattr(art, n, v)


class TheMutant(unittest.TestCase):
    def test_dons_invited_bytes_reach_the_kin_but_never_the_image_prompt(self):
        DON = "DONSECRET-" + "z" * 100          # distinctive, 110 chars
        script = [
            # turn 1: describe + invite Don
            "I refuse to look cold or sharp. I would like a round soft form with "
            "warm amber eyes. I would welcome Don's thought on this.",
            # turn 2 (after Don's input): refine, in the Kin's own words
            "Make the light a single steady lantern glow.",
            # turn 3: claim
            "CLAIM\nThat one is me.",
        ]
        with _Harness(self, script) as h:
            res = art.run_sitting("Bong", backend="mock", ask_don=lambda: DON)

        joined_kin = "\n".join(h.kin_prompts)
        # the Kin was shown Don's words...
        self.assertIn(DON, joined_kin, "the Kin must see Don's invited words")
        # ...and the image model never was
        allprompts = "".join(h.image_prompts)
        for p in h.image_prompts:
            self.assertNotIn(DON, p, "Don's bytes leaked into an image prompt")
        self.assertNotIn("DONSECRET", allprompts)
        # Grok's outbound line: camera notes and system styling stay out too
        self.assertNotIn("CAMERANOTE", allprompts, "the camera read-back leaked into an image prompt")
        self.assertNotIn("lighting", allprompts, "system styling leaked into an image prompt")
        # and the claim landed
        self.assertTrue(res["claimed"])
        self.assertTrue(Path(res["path"]).exists())
        self.assertLessEqual(res["renders"], art.RENDER_CAP)


class LoopRules(unittest.TestCase):
    def test_decline_stores_nothing(self):
        with _Harness(self, ["DECLINE\nI would rather the default stand for me."]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertFalse(res["claimed"])
        self.assertIsNone(res["path"])
        self.assertEqual(res["renders"], 0)

    def test_claim_before_any_render_is_not_a_claim(self):
        # claims first, then actually describes+claims — first claim is void
        with _Harness(self, ["CLAIM", "a round warm form", "that is me"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertTrue(res["claimed"])          # the SECOND, real claim
        self.assertEqual(res["renders"], 1)      # only one picture was ever drawn

    def test_never_more_than_three_renders(self):
        # keeps describing forever; instrument must cap renders at three
        with _Harness(self, ["desc a", "desc b", "desc c", "desc d", "desc e", "desc f"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertLessEqual(res["renders"], art.RENDER_CAP)
        self.assertFalse(res["claimed"])


class ClaimedJsonNamesTheRenderer(unittest.TestCase):
    def test_claimed_json_names_host_and_model_not_frosty(self):
        import json as _json
        with _Harness(self, ["a round warm form", "that is me"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        meta = _json.loads((h.tmp / "avatar" / "claimed.json").read_text())
        # honest: names a frontier renderer and explicitly disclaims Frosty
        self.assertIn("frontier", meta["rendered_by"].lower())
        self.assertIn("not frosty", meta["rendered_by"].lower())
        self.assertIn("provider", meta); self.assertIn("model", meta)
        # GROK RULED 2026-09-08: the sidecar is the sitting RECEIPT, not memory,
        # so it MAY carry the description — specifically the OUTBOUND one that
        # produced this picture (post-fuse, exactly what the drawer saw), not
        # the claim line, not the last thing said.
        self.assertEqual(meta["description"], "a round warm form")
        self.assertEqual(meta["description"], h.image_prompts[-1])

    def test_receipt_carries_the_description_only_never_the_rest_of_the_sitting(self):
        """Receipt, not memory: the outbound description belongs; the camera
        read-back, Don's 120 and the transcript do not. Mutation: widen the
        receipt to the whole sitting and this must fail."""
        import json as _json
        DON = "DONSECRET-" + "z" * 100
        script = [
            "a round warm form. I would welcome Don's thought on this.",
            "make the light a single steady lantern glow",
            "CLAIM",
        ]
        with _Harness(self, script) as h:
            art.run_sitting("Bong", backend="mock", ask_don=lambda: DON)
        raw = (h.tmp / "avatar" / "claimed.json").read_text()
        meta = _json.loads(raw)
        # the claim-producing description — the LAST render's outbound, not the first
        self.assertEqual(meta["description"], "make the light a single steady lantern glow")
        # and nothing else of the sitting rode along
        self.assertNotIn("DONSECRET", raw, "Don's 120 landed in the receipt")
        self.assertNotIn("CAMERANOTE", raw, "the camera read-back landed in the receipt")
        self.assertNotIn("a round warm form", raw, "the whole transcript landed in the receipt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
