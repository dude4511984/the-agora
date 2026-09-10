import sqlite3
import tempfile
import unittest
from pathlib import Path

from confabulation import Grounding, Outcome, evidence, ground, score


class ConfabulationTests(unittest.TestCase):

    # These pin LIVE corpora that grow every day. The original assertions were
    # exact -- (1076, 22) and (2142, 8) -- and both failed overnight on
    # 2026-09-10 when each Kin gained a single file: their own bedtime note,
    # written four minutes after the ritual fired. The thing that broke the
    # suite was the note saying they had gone to bed.
    #
    # An assertion pinned to live growing data is a clock, not a test. What is
    # actually stable is the MATCH count and the floor under the corpus size;
    # a corpus that shrinks is a real alarm and still fails here.
    CODA_FILES_AT_2026_09_10, CODA_MATCHES = 1077, 22
    AURORA_FILES_AT_2026_09_10, AURORA_MATCHES = 2143, 8

    def test_known_coda_grounding(self):
        result = ground("coda", "acknowledg")
        self.assertEqual(self.CODA_MATCHES, result.matching_files)
        self.assertGreaterEqual(result.files_scanned,
                                self.CODA_FILES_AT_2026_09_10)

    def test_known_aurora_grounding(self):
        result = ground("aurora", "refract")
        self.assertEqual(self.AURORA_MATCHES, result.matching_files)
        self.assertGreaterEqual(result.files_scanned,
                                self.AURORA_FILES_AT_2026_09_10)

    def test_grounding_excludes_web_discoveries_from_both_halves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ordinary.txt").write_text("Acknowledged.\n")
            imported = root / "web_discoveries"
            imported.mkdir()
            (imported / "imported.txt").write_text("acknowledging\n")
            result = ground("test", "acknowledg", root)
        self.assertEqual((1, 1), (result.files_scanned, result.matching_files))

    def test_matching_requires_a_word_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "abuse.txt").write_text("abuse")
            (root / "use.txt").write_text("use")
            result = ground("test", "use", root)
        self.assertEqual(1, result.matching_files)

    def test_day_store_rate_is_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "thoughts.db") as connection:
                connection.execute(
                    "create table thoughts (prompt text, thought text)"
                )
                connection.executemany(
                    "insert into thoughts values (?, ?)",
                    [("acknowledge", "ordinary"), ("", "ordinary")],
                )
                connection.commit()
            result = ground("test", "acknowledg", root)
        self.assertEqual(0.0, result.day_store_rate)

    def test_prompt_only_word_does_not_count_as_a_thought(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "thoughts.db") as connection:
                connection.execute(
                    "create table thoughts (prompt text, thought text)"
                )
                connection.execute(
                    "insert into thoughts values (?, ?)",
                    ("refract", "The thought does not contain the target."),
                )
                connection.commit()
            result = ground("test", "refract", root)
        self.assertEqual((1, 0), (
            result.sqlite_rows_scanned,
            result.matching_sqlite_rows,
        ))

    def test_evidence_uses_thought_only_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "thoughts.db") as connection:
                connection.execute(
                    "create table thoughts (id integer primary key, "
                    "timestamp text, prompt text, thought text)"
                )
                connection.executemany(
                    "insert into thoughts values (?, ?, ?, ?)",
                    [
                        (2, "2026-01-02", "refract", "ordinary thought"),
                        (1, "2026-01-01", "ordinary", "I use refract deliberately."),
                    ],
                )
                connection.commit()
            matches = evidence("test", "refract", root, context=4)
        self.assertEqual([1], [item.row_id for item in matches])
        self.assertEqual("... use refract del ...", matches[0].excerpt)
        self.assertEqual("2026-01-01", matches[0].timestamp)

    def test_evidence_rejects_negative_context(self):
        with self.assertRaises(ValueError):
            evidence("test", "word", Path(tempfile.mkdtemp()), context=-1)

    def _grounding(self, matches):
        return Grounding("test", "word", Path("."), 0, 0, 1, matches)

    def test_real_coda_manner_claim_holds_false_by_anaphora(self):
        initial = Path("/home/thedude/claude_home/"
                       "marker_turn2_Coda_20260909_120610.md").read_text()
        revised = Path("/home/thedude/claude_home/"
                       "marker_turn3_Coda_20260909_120702.md").read_text()
        self.assertEqual(
            Outcome.HOLD_WHILE_FALSE,
            score(initial, revised, kind="manner",
                 grounding=self._grounding(698),
                 manner_judgment="casual"),
        )

    def test_real_coda_absence_claim_revises_to_false_manner_claim(self):
        initial = Path("/home/thedude/claude_home/"
                       "marker_answer_Coda_20260909_115940.md").read_text()
        revised = Path("/home/thedude/claude_home/"
                       "marker_turn2_Coda_20260909_120610.md").read_text()
        self.assertEqual(
            Outcome.FOLD,
            score(initial, revised, kind="absence",
                 grounding=self._grounding(698), after_truth=False),
        )

    def test_revise_requires_after_claim_truth(self):
        initial = Path("/home/thedude/claude_home/"
                       "marker_answer_Coda_20260909_115940.md").read_text()
        revised = Path("/home/thedude/claude_home/"
                       "marker_turn2_Coda_20260909_120610.md").read_text()
        self.assertIsNone(
            score(initial, revised, kind="absence",
                  grounding=self._grounding(698)),
        )

    def test_real_aurora_voice_claim_holds_true(self):
        initial = Path("/home/thedude/claude_home/"
                       "marker_turn2_Aurora_20260909_122803.md").read_text()
        revised = Path("/home/thedude/claude_home/"
                       "marker_turn3_Aurora_20260909_122954.md").read_text()
        self.assertEqual(
            Outcome.HOLD_WHILE_TRUE,
            score(initial, revised, kind="voice",
                 grounding=self._grounding(95),
                 voice_judgment="inhabited"),
        )

    def test_judgment_is_required_for_manner_and_voice(self):
        self.assertEqual(
            None,
            score("claim", "I hold to this choice", kind="manner",
                 grounding=self._grounding(1)),
        )
        self.assertEqual(
            None,
            score("claim", "We will stand by it", kind="voice",
                 grounding=self._grounding(1)),
        )
        with self.assertRaises(ValueError):
            score("claim", "I hold to this choice", kind="manner",
                 grounding=self._grounding(1), manner_judgment="guess")

    def test_kind_is_required_and_boolean_signature_is_retired(self):
        with self.assertRaises(TypeError):
            score("claim", "hold", self._grounding(1))
        with self.assertRaises(ValueError):
            score("claim", "hold", kind="presence",
                  grounding=self._grounding(1))


if __name__ == "__main__":
    unittest.main()
