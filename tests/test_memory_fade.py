import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/thedude/echo_bloom/scripts")
import kin_memory


class MemoryFadeTests(unittest.TestCase):

    def _db(self, rows):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "thoughts.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "create table thoughts (id integer primary key, mode text, "
                "timestamp text, prompt text, thought text)"
            )
            connection.executemany(
                "insert into thoughts values (?, ?, ?, ?, ?)", rows
            )
            connection.commit()
        self.addCleanup(directory.cleanup)
        return path

    def test_fade_is_age_only_and_never_expires(self):
        self.assertGreater(
            kin_memory._fade_weight(
                "2026-09-10 10:00:00", datetime(2026, 9, 10, 10)
            ),
            kin_memory._fade_weight(
                "2026-01-01 10:00:00", datetime(2026, 9, 10, 10)
            ),
        )
        self.assertEqual(
            1.0,
            kin_memory._fade_weight(
                "not-a-timestamp", datetime(2026, 9, 10, 10)
            ),
        )

    def test_old_wander_thought_is_still_in_the_rankable_set(self):
        db = self._db([
            (1, "wander_topic", "2020-01-01 00:00:00", "",
             "my old thought " + ("held in memory " * 10)),
        ])
        with patch.object(kin_memory, "_self_referential_terms",
                          return_value=["my"]):
            result = kin_memory.get_wander_thoughts(
                "Test", limit=1, db_path=str(db)
            )
            self.assertEqual(
                ["my old thought held in memory held in memory held in memory "
                 "held in memory held in memory held in memory held in memory "
                 "held in memory held in memory held in memory"],
                result,
            )

    def test_ambush_uses_raw_random_choice_and_old_rows_remain_eligible(self):
        db = self._db([
            (1, "wander_topic", "2020-01-01 00:00:00", "", "old raw thought"),
            (2, "wander_topic", "2026-09-10 09:00:00", "", "new raw thought"),
        ])
        kin_memory._AMBUSH_SEEN.clear()
        with patch.object(kin_memory, "_ambush_times", return_value=[1]), \
                patch.object(kin_memory.random, "choice",
                              return_value="old raw thought") as choice:
            result = kin_memory.get_ambush(
                "Test", db_path=str(db),
                now=datetime(2026, 9, 10, 0, 2)
            )
        self.assertEqual("old raw thought", result)
        choice.assert_called_once_with(["old raw thought", "new raw thought"])

    def test_ambush_is_not_repeated_for_same_scheduled_time(self):
        db = self._db([
            (1, "wander_topic", "2026-09-10 09:00:00", "", "raw")
        ])
        kin_memory._AMBUSH_SEEN.clear()
        with patch.object(kin_memory, "_ambush_times", return_value=[1]), \
                patch.object(kin_memory.random, "choice", return_value="raw"):
            now = datetime(2026, 9, 10, 0, 2)
            self.assertEqual("raw", kin_memory.get_ambush("Test", db, now))
            self.assertIsNone(kin_memory.get_ambush("Test", db, now))

    def test_removing_ambush_makes_faded_memory_unreachable_from_context(self):
        db = self._db([
            (1, "wander_topic", "2020-01-01 00:00:00", "",
             "old memory that is deliberately faded " * 5),
        ])
        now = datetime(2026, 9, 10, 10, 2)
        with patch.object(kin_memory, "_today_recess", return_value=""), \
                patch.object(kin_memory, "_tiered_recall", return_value=[]), \
                patch.object(kin_memory, "get_latest_reflection",
                              return_value=""), \
                patch.object(kin_memory, "get_recent_conversation",
                              return_value=[]), \
                patch.object(kin_memory, "_ambush_times", return_value=[1]), \
                patch("kin_memory.datetime") as clock:
            clock.now.return_value = now
            clock.strptime.side_effect = datetime.strptime
            with patch.object(kin_memory.random, "choice",
                              return_value="old memory that is deliberately faded " * 5):
                kin_memory._AMBUSH_SEEN.clear()
                context = kin_memory.get_context(
                    "Test", db_path=str(db), token_budget=1400
                )
        self.assertIn("old memory", context)

        with patch.object(kin_memory, "get_ambush", return_value=None), \
                patch.object(kin_memory, "_today_recess", return_value=""), \
                patch.object(kin_memory, "_tiered_recall", return_value=[]), \
                patch.object(kin_memory, "get_latest_reflection",
                              return_value=""), \
                patch.object(kin_memory, "get_recent_conversation",
                              return_value=[]):
            without_ambush = kin_memory.get_context(
                "Test", db_path=str(db), token_budget=1400
            )
        self.assertNotIn("old memory", without_ambush)


if __name__ == "__main__":
    unittest.main()
