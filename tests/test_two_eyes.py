"""Two eyes — the Kin is told only what both saw. Hermetic.

The load-bearing test is the one taken from life: on 2026-09-09 gemma3:12b
reported "a dark border below that" on a card containing no border. qwen3-vl
did not see it. The intersection must kill it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import two_eyes as T  # noqa: E402


def eyes(a, b):
    """Two fake cameras returning fixed labelled-line text."""
    texts = {"eyeA": a, "eyeB": b}
    return ([("eyeA", "h"), ("eyeB", "h")],
            lambda model, host, image, timeout=300: texts[model])


class InventionDiesAtTheIntersection(unittest.TestCase):
    def test_a_phantom_seen_by_one_eye_never_reaches_the_kin(self):
        """From life. Mutation: fall back to a single eye and this fails."""
        e, ask = eyes(
            "UPPER: horizontal lines\nLOWER: a dark line and a dark border below that\n"
            "ISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE",
            "UPPER: thin horizontal lines\nLOWER: a thick black line\n"
            "ISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE")
        seen = T.look(b"x", eyes=e, _ask=ask)
        shown = seen.for_the_kin()
        self.assertNotIn("border", shown, "a phantom reached the Kin")
        # the shorter, more conservative wording is what is shown — verbatim
        self.assertIn("a thick black line", shown)

    def test_disagreement_is_a_slot_not_a_blend(self):
        e, ask = eyes("UPPER: a red square\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE",
                      "UPPER: a blue circle\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE")
        shown = T.look(b"x", eyes=e, _ask=ask).for_the_kin()
        self.assertIn("did not agree", shown)
        for word in ("red", "blue", "square", "circle"):
            self.assertNotIn(word, shown, "disputed content leaked to the Kin")

    def test_one_eye_is_not_two_eyes(self):
        """A dead camera must not silently become a single-camera read-back —
        that is exactly how invention gets through."""
        def half(model, host, image, timeout=300):
            if model == "eyeB":
                raise TimeoutError("camera down")
            return "UPPER: a dark border\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE"
        seen = T.look(b"x", eyes=[("eyeA", "h"), ("eyeB", "h")], _ask=half)
        self.assertEqual(seen.agreed, {})
        self.assertNotIn("border", seen.for_the_kin())
        self.assertIn("eyeB", seen.errors)

    def test_raw_text_stays_out_of_the_kins_mouth(self):
        e, ask = eyes("UPPER: a dark border\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE",
                      "UPPER: NONE\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE")
        seen = T.look(b"x", eyes=e, _ask=ask)
        self.assertIn("a dark border", seen.raw["eyeA"])      # operator sees it
        self.assertNotIn("border", seen.for_the_kin())        # the Kin does not

    def test_agreement_is_selected_verbatim_never_authored(self):
        e, ask = eyes("UPPER: two thin pale lines running across\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE",
                      "UPPER: thin pale lines\nLOWER: NONE\nISOLATED: NONE\nEMPTY: NONE\nDIFFERS: NONE")
        seen = T.look(b"x", eyes=e, _ask=ask)
        self.assertIn(seen.agreed["UPPER"],
                      ("two thin pale lines running across", "thin pale lines"),
                      "the shown text must be something a camera actually wrote")


class TheAskDoesNotLeadTheWitness(unittest.TestCase):
    def test_no_resemblance_no_meaning_no_yes_no_questions(self):
        p = " ".join(T.READBACK_SLOTS.lower().split())
        # the prohibitions must be PRESENT — this scans for the ask leading the
        # witness, not for the words used to forbid it. (I have now written this
        # test wrong four times in one day by scanning prose instead of intent.)
        for prohibition in ("do not guess", "do not say what it resembles",
                            "what it means", "mood"):
            self.assertIn(prohibition, p, f"the ask dropped a guard: {prohibition!r}")
        for leading in ("is there a", "do you see", "does it contain",
                        "would you say", "?"):
            self.assertNotIn(leading, p, f"the ask leads the witness: {leading!r}")
        for required in ("upper", "lower", "isolated", "empty", "differs", "none"):
            self.assertIn(required, p)

    def test_two_lineages_not_two_gemmas(self):
        fams = {m.split(":")[0].split("-")[0] for m, _ in T.EYES}
        self.assertEqual(len(fams), 2, f"both eyes are the same family: {T.EYES}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
