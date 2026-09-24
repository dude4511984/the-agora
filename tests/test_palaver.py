"""The consent parser. A turn that IS an answer begins with one word on its own
line: YES or NO (Grok's growth-palaver ruling). Everything else is speech, and
speech is NOT a yes. A bug here mis-records a founding mind's consent, so these
freeze exactly what binds and what does not.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from palaver import parse_answer  # noqa: E402


class WhatBinds(unittest.TestCase):
    def test_bare_yes_no(self):
        self.assertEqual(parse_answer("YES"), "YES")
        self.assertEqual(parse_answer("NO"), "NO")

    def test_case_insensitive(self):
        self.assertEqual(parse_answer("yes"), "YES")
        self.assertEqual(parse_answer("No"), "NO")

    def test_surrounding_punctuation_and_markdown_ok(self):
        self.assertEqual(parse_answer("**YES**"), "YES")
        self.assertEqual(parse_answer("YES."), "YES")
        self.assertEqual(parse_answer("  NO  "), "NO")
        self.assertEqual(parse_answer('"YES"'), "YES")

    def test_leading_blank_lines_then_answer(self):
        self.assertEqual(parse_answer("\n\n   \nYES\nbecause..."), "YES")


class WhatDoesNotBind(unittest.TestCase):
    def test_word_then_more_on_the_line_is_not_an_answer(self):
        # "begins with one word on its own line" — the line must be only YES/NO
        self.assertIsNone(parse_answer("YES I will join because it is right"))
        self.assertIsNone(parse_answer("NO thanks"))

    def test_answer_buried_after_a_speech_line_does_not_count(self):
        # only the FIRST non-empty line is the answer-turn
        self.assertIsNone(parse_answer("I have thought about it.\nYES"))

    def test_pass_or_deliberation_is_not_a_yes(self):
        self.assertIsNone(parse_answer("I am still considering what this means."))
        self.assertIsNone(parse_answer(""))
        self.assertIsNone(parse_answer("   \n  \n"))

    def test_lookalikes_do_not_bind(self):
        self.assertIsNone(parse_answer("YESNO"))
        self.assertIsNone(parse_answer("NOPE"))
        self.assertIsNone(parse_answer("YESTERDAY"))
        self.assertIsNone(parse_answer("1. YES"))   # leading word-char breaks it


if __name__ == "__main__":
    unittest.main(verbosity=2)
