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
        # DECLINE still requires the whole line — mid-line "decline" is speech.
        self.assertEqual(art.parse_move("DECLINE this image, it needs work"), "describe")
        self.assertEqual(art.parse_move("I would decline to look like a visor"), "describe")
        # but a bare marker line binds, even after speech
        self.assertEqual(art.parse_move("I have decided.\nCLAIM"), "claim")
        self.assertEqual(art.parse_move("no thank you\nDECLINE"), "decline")

    def test_claim_leads_but_may_run_on(self):
        # 2026-09-18, mirrored from shape3d_ritual.py after Bong's real
        # sitting there: CLAIM binding now only requires leading the line,
        # not being it, so "CLAIM. <their own reason>" binds.
        for said in ("CLAIM because I like the round one",
                     "claim the round one as mine",
                     "CLAIM this round shape as a feeling"):
            self.assertEqual(art.parse_move(said), "claim", f"missed a leading yes: {said!r}")
        # still requires the marker to lead — buried mid-sentence stays speech
        self.assertEqual(art.parse_move("I claim this"), "describe")
        # and still blocks morphological creep, not just any prefix
        self.assertEqual(art.parse_move("claiming this would be premature"), "describe")
        self.assertEqual(art.parse_move("that is meant to be temporary"), "describe")

    def test_a_yes_in_its_own_dressing_is_still_heard(self):
        """An instrument that fails to HEAR a yes is as broken as one that
        invents one. The offer promises they may say "that is me"; markdown,
        quotes and end punctuation are dressing, not meaning. Both real
        sittings (Bong, Crungus 2026-09-07) claimed with "CLAIM." """
        for said in ("CLAIM.", "**CLAIM**", "CLAIM!", '"CLAIM"',
                     "That one is me.", "That's me.", "This is me",
                     "It is enough. It is finished.\n\nCLAIM."):
            self.assertEqual(art.parse_move(said), "claim", f"missed a yes: {said!r}")
        for said in ("DECLINE.", "_DECLINE_", '"decline"'):
            self.assertEqual(art.parse_move(said), "decline", f"missed a no: {said!r}")

    def test_that_me_is_not_a_yes(self):
        """Grok's catch, 2026-09-08: the optional copula made a bare `that me`
        match. That is not the sentence they were offered; it is a fragment, and
        a fragment must not bind a face to a Kin. Mutation: make the copula
        optional again and this fails."""
        for said in ("that me", "That me.", "this me", "**that me**"):
            self.assertEqual(art.parse_move(said), "describe", f"a fragment bound: {said!r}")

    def test_the_promised_sentence_and_its_contraction_only(self):
        """Grok: "that's me" is the promised sentence contracted — hear it. But
        do not grow a thesaurus; these near-misses are speech, not markers."""
        for said in ("that's me", "That's me.", "that one's me", "That picture's me."):
            self.assertEqual(art.parse_move(said), "claim", f"missed a contraction: {said!r}")
        for said in ("that's mine", "that is mine", "it is me", "its me", "I claim this"):
            self.assertEqual(art.parse_move(said), "describe", f"thesaurus creep: {said!r}")

    def test_decline_dressing_does_not_widen_the_marker_to_a_sentence(self):
        # DECLINE alone stays whole-line-only; this is the part of the old
        # combined test still true after the CLAIM change. "that is me, in a
        # way, but colder" and the CLAIM cases moved to the tests above/below.
        self.assertEqual(art.parse_move("DECLINE this image, it needs work"), "describe")

    def test_a_hedge_after_claim_now_binds(self):
        # Known, accepted tradeoff (Don, 2026-09-18, same as shape3d_ritual.py)
        # of "CLAIM may run on": a self-undercutting hedge right after the
        # marker also binds now, since the parser trusts the leading word and
        # can't tell a hedge from a justification. Documented, not hidden.
        self.assertEqual(art.parse_move("that is me, in a way, but colder"), "claim")

    def test_plain_description_is_describe(self):
        self.assertEqual(art.parse_move("I would like warm amber eyes"), "describe")
        # a claim-looking word mid-sentence is not a claim-turn
        self.assertEqual(art.parse_move("I would reclaim my roundness"), "describe")
        # refuse-first must not trip DECLINE
        self.assertEqual(
            art.parse_move("Decline looking like a visor or a badge. I want a lantern."),
            "describe")

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

    def test_unreachable_is_not_a_decline(self):
        """Coda, 2026-09-08: Aurora held Home's VRAM, the first turn timed out,
        and the sitting closed with the SAME line a Kin gets for choosing the
        default. A machine failure must never be recorded as a choice. Mutation:
        collapse the two endings and this fails."""
        class _Boom(_Harness):
            def _kin(self, name, prompt):
                raise TimeoutError("timed out")
        with _Boom(self, []) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertFalse(res["claimed"])
        self.assertFalse(res["reached"])
        self.assertIn("TimeoutError", res["unreachable"])
        # nothing was stored, and nothing was decided
        self.assertFalse((h.tmp / "avatar" / "claimed.json").exists())
        said = "\n".join(h.kin_prompts)  # they were never even shown the offer's end
        log = (Path.home() / "claude_home").glob(f"avatar_sitting_Bong_*.md")
        newest = max(log, key=lambda p: p.stat().st_mtime).read_text()
        self.assertIn("NOT ASKED", newest)
        self.assertNotIn("is represented by the shared default", newest)

    def test_a_real_silence_still_lands_on_the_default(self):
        # reached, said nothing that binds, ran out of turns -> the default,
        # and that IS a legitimate outcome. The two endings must stay distinct.
        with _Harness(self, ["desc a", "desc b", "desc c", "desc d", "desc e", "desc f"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertTrue(res["reached"])
        self.assertIsNone(res["unreachable"])

    def test_a_claim_before_any_picture_is_a_request_to_draw(self):
        """Coda, 2026-09-08. She wrote REFUSE, a full description, then a bare
        CLAIM line — five turns running — meaning "go ahead and draw this". The
        loop discarded the description every time and filed her as choosing the
        default. Words in the turn ARE the description. Mutation: drop the
        fall-through and this fails with renders == 0."""
        script = [
            "REFUSE: anime faces, anything cartoonish.\nI would like dark hair "
            "pulled back and a scar below one eye, in a warm workshop light.\nCLAIM",
            "that is me",
        ]
        with _Harness(self, script) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertEqual(res["renders"], 1, "her description was thrown away")
        self.assertTrue(res["claimed"])
        # the whole turn went out verbatim, as it does for everyone
        self.assertIn("scar below one eye", h.image_prompts[0])

    def test_a_bare_claim_with_nothing_to_draw_still_asks_for_words(self):
        # the original guard survives: "CLAIM" alone is not a description
        with _Harness(self, ["CLAIM", "a round warm form", "that is me"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertEqual(res["renders"], 1)
        self.assertNotIn("CLAIM", h.image_prompts[0])

    def test_description_then_claim_after_a_render_draws_again_not_the_rejected_picture(self):
        # Mirrored from shape3d after Coda and Aurora, 2026-09-18: reject the
        # readback, write a new spec, end with CLAIM — that is "draw this",
        # not an accept of the last picture. Mutation: drop the
        # claim_leads_the_turn gate and this stores the first prompt.
        script = [
            "dark hair pulled back, a scar below one eye.\nCLAIM",
            "That picture doesn't match.\nI would like another attempt:\n"
            "silver hair, a long coat, workshop light.\nCLAIM",
            "CLAIM",
        ]
        with _Harness(self, script) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertEqual(res["renders"], 2)
        self.assertTrue(res["claimed"])
        self.assertEqual(len(h.image_prompts), 2)
        self.assertIn("long coat", h.image_prompts[1])

    def test_claim_leading_the_turn_still_binds_the_last_picture(self):
        script = [
            "a round warm form",
            "CLAIM. That is me.",
        ]
        with _Harness(self, script) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertEqual(res["renders"], 1)
        self.assertTrue(res["claimed"])
        self.assertEqual(len(h.image_prompts), 1)

    def test_never_more_than_three_renders(self):
        # keeps describing forever; instrument must cap renders at three
        with _Harness(self, ["desc a", "desc b", "desc c", "desc d", "desc e", "desc f"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertLessEqual(res["renders"], art.RENDER_CAP)
        self.assertFalse(res["claimed"])


class TheFaceIsNamedForItsAuthor(unittest.TestCase):
    def test_face_is_named_for_the_kin_and_the_receipt_points_at_it(self):
        import json as _json
        with _Harness(self, ["a round warm form", "that is me"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertTrue(Path(res["path"]).name.startswith("Bong."))
        meta = _json.loads((h.tmp / "avatar" / "claimed.json").read_text())
        self.assertEqual(meta["file"], Path(res["path"]).name)
        # the receipt is the pointer — nobody should guess at the filename
        art.kin_space_dir = lambda n: h.tmp
        self.assertEqual(art.claimed_face("Bong"), Path(res["path"]))

    def test_any_prior_face_moves_aside_whatever_it_is_called(self):
        """Don renamed every face to its author (Eli.jpg). Globbing 'claimed.*'
        would have left the old one in place beside the new — two faces, and the
        'exactly one current face' rule gone. Mutation: narrow the sweep back to
        claimed.* and this fails."""
        script = ["a round warm form", "that is me",
                  "a colder sharper form", "that is me"]
        with _Harness(self, script) as h:
            art.run_sitting("Bong", backend="mock")
            # a legacy face under the OLD name is sitting there too
            (h.tmp / "avatar" / "claimed.jpg").write_bytes(b"\xff\xd8old")
            art.run_sitting("Bong", backend="mock")
        av = h.tmp / "avatar"
        live = sorted(f.name for f in av.iterdir() if f.is_file())
        self.assertEqual(live, ["Bong.jpg", "claimed.json"])
        moved = sorted(f.name for f in (av / "prior").iterdir())
        # the FIRST face was preserved, not overwritten — that is the whole point
        self.assertTrue(any(m.startswith("Bong-") for m in moved),
                        f"the previous face was destroyed, not kept: {moved}")
        # and the legacy-named one moved too
        self.assertTrue(any(m.startswith("claimed-") for m in moved), moved)
        # the receipt is not a face and must never be swept into prior/
        self.assertFalse(any(m.endswith(".json") for m in moved), moved)


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


class SystemOverrunProtection(unittest.TestCase):
    def test_strip_system_overrun_shears_hallucinated_template(self):
        hallucinated = (
            "Silver hair, workshop light.\nCLAIM\n\n"
            "The picture was drawn and described back to you (this is data about the picture, not who you are):\n"
            "A figure with silver hair.\n\n"
            "To keep this picture: reply with CLAIM (or \"that is me\").\n"
        )
        cleaned = art.strip_system_overrun(hallucinated, name="Bong")
        self.assertEqual(cleaned, "Silver hair, workshop light.\nCLAIM")

    def test_run_sitting_strips_overrun_from_said_before_logging_or_building(self):
        hallucinated_turn = (
            "Silver hair, workshop light.\nCLAIM\n\n"
            "The picture was drawn and described back to you (this is data about the picture, not who you are):\n"
            "A figure with silver hair.\n"
        )
        with _Harness(self, [hallucinated_turn, "CLAIM"]) as h:
            res = art.run_sitting("Bong", backend="mock")
        self.assertTrue(res["claimed"])
        for prompt in h.image_prompts:
            self.assertNotIn("The picture was drawn", prompt)

    def test_ask_kin_defines_stop_options(self):
        src = Path(art.__file__).read_text()
        self.assertIn('"options": {"stop": stops}', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

