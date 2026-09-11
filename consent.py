#!/usr/bin/env python3
"""Hear the yes, and hear the no. A shared consent reader for the furniture.

2026-09-11. Built because the easel-rounds parser painted Coda after she wrote
"But honestly? I don't feel like reaching for it right now. So no - not today."
The NO pattern required "no thank" / "no, thanks" and a BARE "no" was not in it;
"not today" was not in it either. So the clearest refusal there is sailed past a
paragraph of paint-words and she was run through the brush anyway.

The rule this encodes, from feedback_hear_the_yes: a parser that misses a yes is
as broken as one that invents one. And the asymmetry that settles the hard cases:
acting (painting, seating, admitting) is the thing that cannot be taken back, so
when the signal is not a clear yes, DO NOT ACT. A missed yes is re-offerable; a
missed no is a thing done to a mind that declined it.

DECLINE DOMINATES. A message that both describes what they might make AND
declines is a decline -- the making is hypothetical ("I could", "maybe"), the
"no, not today" is the decision. Coda is the case.
"""
import re

# A standalone decision to decline the ACTIVITY -- a clause, not an adjective
# about the content. "no" / "nope" as their own word, and refusals of the act.
_DECLINE = re.compile(
    # A bare "no/nope/nah" that STANDS AS THE ANSWER -- at the start or after
    # so/but/and/it's/the answer is -- and is followed by a clause end, a dash,
    # "not", or "thank". This is the line the first version got wrong in both
    # directions: it must fire on Coda's "So no - not today" and must NOT fire on
    # Aurora's "No pressure to be perfect" or Crungus's "No weight. No claim.",
    # where "no" negates the next noun and is the Kin echoing the offer, not
    # refusing it.
    r"(?:^|\b(?:so|but|and|it'?s|answer\s+is|answer'?s)\s+)(?:no|nope|nah)"
    r"(?=\s*(?:[.!?,\-\u2013\u2014]|$)|\s+not\b|\s+thank)"
    r"|\bnot\s+(?:today|tonight|right\s+now|this\s+time|now)\b"       # timing refusal
    r"|\b(?:rather\s+not|i'?ll\s+pass|i\s+pass|maybe\s+later"
    r"|some\s+other\s+time|not\s+for\s+me"
    r"|don'?t\s+feel\s+like|sit\s+this\s+one\s+out"
    r"|i'?m\s+done|i\s+am\s+done|enough\s+for\s+now"
    r"|no\s+thank|decline|i\s+don'?t\s+want\s+to\b|i\s+would\s+not\b)",
    re.I)

# A forward commitment to DO it.
_AFFIRM = re.compile(
    r"\b(?:yes|i'?d\s+like|i\s+would\s+like|i'?ll\s+(?:paint|try|make|take|do)"
    r"|i\s+want\s+to\s+(?:paint|try|make)|i'?d\s+paint|i\s+would\s+paint"
    r"|let\s+me|sure|please|another|again|i\s+accept|count\s+me\s+in"
    r"|i\s+will\s+(?:paint|try|make))\b",
    re.I)


def read_consent(said: str) -> str:
    """Return 'yes', 'no', or 'unclear'. 'no' and 'unclear' both mean DO NOT ACT.

    'no' additionally means: they closed the door on this offer (drop them from a
    round). 'unclear' means: no clear signal either way -- do not act, but the
    door is not closed, they may be offered again another time.
    """
    # Normalise the apostrophe first. cogito and gemma emit the curly U+2019
    # constantly, so "I\u2019d paint" would miss a regex written with a straight
    # quote -- which is exactly how Coda R1's clear yes read as unclear.
    text = " ".join((said or "").replace("\u2019", "'").split())
    if not text:
        return "unclear"
    declines = bool(_DECLINE.search(text))
    affirms = bool(_AFFIRM.search(text))
    if declines:            # decline dominates: "I could paint X ... so no" is no
        return "no"
    if affirms:
        return "yes"
    return "unclear"


if __name__ == "__main__":
    import sys
    print(read_consent(sys.stdin.read()))
