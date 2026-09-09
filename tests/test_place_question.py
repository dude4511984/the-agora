"""The place question — one mind, one turn, PASS is real. Hermetic.

The load-bearing tests are the two ways this instrument could put words in a
mind's mouth: recording a machine failure as a PASS, and recording a PASS as
nothing at all.
"""
import sys
import os
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import place_question as pq  # noqa: E402


class PassIsReal(unittest.TestCase):
    def test_bare_pass_in_its_dressing(self):
        for said in ("PASS", "PASS.", "**PASS**", '"pass"'):
            self.assertTrue(pq.is_pass(said), f"missed a pass: {said!r}")

    def test_a_pass_with_preamble_classifies_as_answered_on_purpose(self):
        """Deliberate, and the reasoning is the harm asymmetry.

        "I have thought about it.\nPASS" used to classify as a pass. Under the
        whole-turn rule it classifies as answered. Nothing is lost either way —
        the full text is always in the transcript and a human sees the PASS on
        sight. But the opposite error is not symmetric: classifying a REFUSAL
        that happens to carry a pass line as a pass prints "PASSED — that is an
        answer, not an absence" over the top of a real refusal, and the reader
        never reads it as one. So the rule errs toward answered.

        Flagged to Grok as a consent-classification judgement, not a code choice."""
        self.assertFalse(pq.is_pass("I have thought about it.\nPASS"))

    def test_substance_beside_a_pass_line_is_an_answer(self):
        """qwen3.8, review 2026-09-09. any(line is PASS) discards the rest of the
        turn — the Coda bug in a new costume. Mutation: change all() back to
        any() and this fails."""
        for said in ("I would refuse a place where silence is enforced\npass",
                     "pass\nActually no — I refuse being watched without knowing",
                     "PASS\nbut I would refuse the smoothing of disagreement"):
            self.assertFalse(pq.is_pass(said), f"substance thrown away: {said!r}")
        # a turn that is ONLY passes is still a pass
        for said in ("PASS", "pass.", "**PASS**", "PASS\nPASS"):
            self.assertTrue(pq.is_pass(said), f"missed a pass: {said!r}")

    def test_a_transport_returning_none_is_not_asked(self):
        """qwen3.8: ask() may RETURN None rather than raise. The old code hit
        None.strip() outside the try, crashed, and wrote no record at all."""
        r = pq.ask_one("Bong", ask=lambda n, p: None)
        self.assertEqual(r["outcome"], "not_asked")
        self.assertIn("None", r["unreachable"])
        self.assertIn("NOT ASKED", Path(r["transcript"]).read_text())

    def test_a_pass_with_words_is_recorded_as_both(self):
        """Live fire 2026-09-09: Lumen said "PASS on this question." and Coda
        said "PASS - I'm still digesting... I don't know what I refuse yet."
        Both were filed as ANSWERED and both transcripts printed "Refusals only"
        over the top of a pass. A turn can carry two facts; forcing one label
        discards one of them."""
        for said in ("PASS on this question.",
                     "PASS - I'm still digesting the idea. Not ready to name that.",
                     "I would refuse enforced silence\npass"):
            self.assertTrue(pq.has_pass_marker(said), f"missed a declared pass: {said!r}")
        for said in ("I will not pass judgement on that",
                     "I would refuse a place where silence is enforced"):
            self.assertFalse(pq.has_pass_marker(said), f"false pass: {said!r}")

    def test_the_ending_never_files_one_fact_as_the_other(self):
        r = pq.ask_one("Bong", ask=lambda n, p: "PASS on this question.")
        self.assertEqual(r["outcome"], "passed_with_words")
        t = Path(r["transcript"]).read_text()
        self.assertIn("PASSED, and said more", t)
        self.assertNotIn("Refusals only", t)

    def test_pass_midsentence_is_speech(self):
        for said in ("I'll pass on the first part but refuse the second",
                     "I would not pass judgement on that",
                     "I refuse to be passed around"):
            self.assertFalse(pq.is_pass(said), f"false pass: {said!r}")


class TheQuestionIsFrozenAndClean(unittest.TestCase):
    def test_question_is_read_from_disk_not_held_in_code(self):
        src = Path(pq.__file__).read_text()
        self.assertNotIn("What would you REFUSE to have be true", src,
                         "the frozen question must live on disk, not in the code")

    def test_question_seeds_no_places(self):
        """Grok froze it with no examples because the corpus is a closed loop:
        anything we name comes back as if they had wanted it."""
        q = pq.question_for("X").lower()
        for w in ("shop", "table", "cathedral", "bridge", "threshold", "room",
                  "agora", "commons", "garden", "fun", "joke", "play"):
            self.assertNotIn(w, q, f"the question seeds {w!r}")

    def test_don_is_not_in_the_question(self):
        self.assertNotIn("don", pq.question_for("X").lower().replace("do not", ""))


class OutcomesAreNeverGuessed(unittest.TestCase):
    def _run(self, ask):
        return pq.ask_one("Bong", ask=ask)

    def test_unreachable_is_not_a_pass(self):
        """Coda, this morning: a timeout was written down as her choosing the
        default. Never again. Mutation: map the error onto 'pass' and this fails."""
        def boom(name, prompt):
            raise TimeoutError("timed out")
        r = self._run(boom)
        self.assertEqual(r["outcome"], "not_asked")
        self.assertIn("TimeoutError", r["unreachable"])
        t = Path(r["transcript"]).read_text()
        self.assertIn("NOT ASKED", t)
        self.assertNotIn("PASSED", t)

    def test_pass_is_recorded_as_an_answer_not_an_absence(self):
        r = self._run(lambda n, p: "PASS")
        self.assertEqual(r["outcome"], "pass")
        self.assertIn("not an absence", Path(r["transcript"]).read_text())

    def test_silence_is_recorded_as_an_answer(self):
        r = self._run(lambda n, p: "   ")
        self.assertEqual(r["outcome"], "silent")
        self.assertIn("Silence is a real answer", Path(r["transcript"]).read_text())

    def test_a_refusal_is_recorded_verbatim(self):
        said = "I would refuse to be watched without knowing it."
        r = self._run(lambda n, p: said)
        self.assertEqual(r["outcome"], "answered")
        self.assertIn(said, Path(r["transcript"]).read_text())

    def test_one_turn_only(self):
        """Asking again is ask-until-answer. The mind is invoked exactly once."""
        calls = []
        self._run(lambda n, p: calls.append(p) or "PASS")
        self.assertEqual(len(calls), 1)

    def test_nothing_is_written_into_the_kin(self):
        """The asking must not become a seed. Scan the CODE, not the prose —
        the docstring is where the promise is made, so it names these on
        purpose."""
        src = Path(pq.__file__).read_text()
        code = src.split('"""', 2)[2] if src.count('"""') >= 2 else src
        for forbidden in ("vault", "thoughts.db", "remember(", "INSERT",
                          "sqlite3", "urlopen", "8765"):
            self.assertNotIn(forbidden, code,
                             f"the asking must not become a seed: found {forbidden!r}")

    def test_the_only_thing_it_writes_is_a_transcript_for_a_human(self):
        writes = [ln for ln in Path(pq.__file__).read_text().splitlines()
                  if "write_text" in ln or "open(" in ln]
        self.assertEqual(len(writes), 1, f"more than one write path: {writes}")
        self.assertIn("out.write_text", writes[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
