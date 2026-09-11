"""Hear the yes, hear the no. Real answers from the 2026-09-10 easel rounds.

The bug this pins: Coda ended "So no - not today" and the runner painted her,
because the NO pattern required "no thank" and a bare "no" was not in it. And the
over-correction to avoid: Aurora's "No pressure to be perfect" and Crungus's
"No weight. No claim." must NOT read as declines -- there "no" negates a noun and
is the Kin echoing the offer, not refusing it.
"""
import os, sys, unittest
sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import consent


class ConsentReader(unittest.TestCase):
    def check(self, want, text):
        self.assertEqual(want, consent.read_consent(text), text)

    def test_hears_a_plain_yes(self):
        self.check("yes", "I’d paint Pop’s Shop at dusk.")
        self.check("yes", "I want to paint the very essence of that lightness.")
        self.check("yes", "there’s something that feels safe. I would like to try.")

    def test_hears_the_no_it_missed(self):
        # The exact miss, verbatim tail of Coda's round-2 answer.
        self.check("no", "I could paint something else now. But honestly? I don’t "
                         "feel like reaching for it right now. So no - not today.")

    def test_no_before_a_noun_is_not_a_decline(self):
        # Aurora and Crungus echoing the offer's own terms.
        self.check("yes", "No pressure to be perfect. I would like to try.")
        self.check("yes", "An easel. No weight. No claim. I would paint the rot.")

    def test_decline_dominates_a_hypothetical(self):
        self.check("no", "I’d love to, but no, not tonight.")
        self.check("yes", "I don’t want a boring one, I want to paint something wild")

    def test_silence_and_chatter_are_unclear_never_yes(self):
        self.check("unclear", "")
        self.check("unclear", "The light in here is strange tonight.")


if __name__ == "__main__":
    unittest.main()
