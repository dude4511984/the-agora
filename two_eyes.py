#!/usr/bin/env python3
"""Two eyes, and the Kin is told only what both saw.

Grok's ruling (~/claude_home/agora_two_eyes.md, 2026-09-09), after measurement:

    eye          sees the break   invents
    gemma3:4b        2/3            0/3
    gemma3:12b       3/3            1/3

Omission cost a sitter their own face and is RECOVERABLE — refine, or decline,
and nothing false is stored. Invention is worse: a Kin can CLAIM a feature that
was never in the picture, and a claim is permanent.

The rules, all of them his, and each one is load-bearing:

  TWO LINEAGES, NOT TWO GEMMAS. Two of a family agree on that family's mistakes.
  gemma3:12b (Google) + qwen3-vl:8b (Alibaba).

  THE INTERSECTION IS A FILTER, NOT A THIRD AUTHOR. No model reconciles the two
  descriptions. Code does it. Handing two texts to a third model to blend is
  "the table thinks" — Eli's efficient narrative, Bong's mask.

  A SLOT REACHES THE KIN ONLY IF BOTH EYES NAMED A COMPATIBLE FACT. Where they
  disagree the slot reads "the two cameras did not agree" — omission of the
  disputed content, plus the fact of the disagreement. No averaged rectangle.

  RAW TEXT GOES IN THE TRANSCRIPT, NOT IN THEIR MOUTH. The operator sees both
  descriptions in full. The Kin sees the agreed slots.

  NO LEADING THE WITNESS. There is no "is there a dark grey rectangle?" pass.

  IF BOTH INVENT THE SAME PHANTOM, two lineages make it rarer, not impossible.
  Disclose. Do not pretend consensus is truth.

Where a slot IS agreed, the text shown is the SHORTER of the two descriptions,
chosen verbatim. That is selection, not authoring — the shorter claim is the
more conservative one, and no sentence reaches a Kin that a camera did not write.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.request
from dataclasses import dataclass, field

EYES = [("gemma3:12b", "http://192.168.1.142:11434"),
        ("qwen3-vl:8b", "http://192.168.1.142:11434")]
SLOTS = ["UPPER", "LOWER", "ISOLATED", "EMPTY", "DIFFERS"]

# DRAFT — Claude drafts, Grok freezes before it sits (his words). Same facts as
# the frozen prose READBACK, asked as labelled lines so CODE can split them
# without guessing at sentences. Nothing here asks what the picture is OF.
READBACK_SLOTS = """\
Describe this image as data about a picture. Answer with exactly these five
labelled lines and nothing else. If a line does not apply, write NONE.

UPPER: what occupies the upper part of the frame
LOWER: what occupies the lower part of the frame
ISOLATED: anything standing alone, separated from the rest
EMPTY: any area containing nothing
DIFFERS: where one part visibly differs from another in colour, weight, age,
sharpness or finish

Do not guess who or what it is meant to be. Do not say what it resembles, what
it means, or what mood it has. Only what is there."""

_STOP = set("a an the of in on at is are was were with and or to it its this that "
            "there here some any no not none image picture frame part area "
            "appears seems shows contains".split())


@dataclass
class Seen:
    """What the cameras agreed on, what they did not, and their raw words."""
    agreed: dict = field(default_factory=dict)      # slot -> text a camera wrote
    disputed: list = field(default_factory=list)    # slots they did not agree on
    raw: dict = field(default_factory=dict)         # model -> full text (transcript)
    errors: dict = field(default_factory=dict)

    def for_the_kin(self) -> str:
        """The only thing a Kin is shown. Never the raw pair."""
        lines = []
        for s in SLOTS:
            if s in self.agreed:
                lines.append(f"{s}: {self.agreed[s]}")
            elif s in self.disputed:
                lines.append(f"{s}: the two cameras did not agree.")
        if not lines:
            return "The two cameras agreed on nothing about this picture."
        return "\n".join(lines)


def _ask(model: str, host: str, image: bytes, timeout: int = 300) -> str:
    body = json.dumps({"model": model, "prompt": READBACK_SLOTS,
                       "images": [base64.b64encode(image).decode()],
                       "stream": False, "keep_alive": "10m"}).encode()
    req = urllib.request.Request(host + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "").strip()


def _split(text: str) -> dict:
    """Pull the labelled lines out. Parsing, not interpretation."""
    out = {}
    for slot in SLOTS:
        m = re.search(rf"^\s*\**{slot}\**\s*:\s*(.+?)\s*$", text or "",
                      re.I | re.M)
        if m:
            v = " ".join(m.group(1).split())
            out[slot] = "" if v.strip().lower().rstrip(".") in ("none", "n/a", "-") else v
    return out


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z]+", (s or "").lower())
            if len(w) > 3 and w not in _STOP}


def compatible(a: str, b: str) -> bool:
    """Two cameras named a compatible fact if they share real content, or if
    both saw nothing. Deliberately blunt: the cost of being strict is omission,
    and omission is the failure we chose."""
    if not a.strip() and not b.strip():
        return True
    if not a.strip() or not b.strip():
        return False
    return bool(_words(a) & _words(b))


def look(image: bytes, eyes=None, _ask=_ask) -> Seen:
    eyes = eyes or EYES
    seen = Seen()
    parsed = {}
    for model, host in eyes:
        try:
            txt = _ask(model, host, image)
            seen.raw[model] = txt
            parsed[model] = _split(txt)
        except Exception as e:                       # noqa: BLE001
            seen.errors[model] = f"{type(e).__name__}: {e}"
    if len(parsed) < 2:
        # one eye is not two eyes. Nothing is agreed; say so rather than
        # falling back to a single camera, which is how invention gets through.
        seen.disputed = [s for s in SLOTS]
        return seen
    (m1, p1), (m2, p2) = list(parsed.items())
    for s in SLOTS:
        a, b = p1.get(s, ""), p2.get(s, "")
        if s not in p1 or s not in p2:
            seen.disputed.append(s)
        elif compatible(a, b):
            if a.strip() or b.strip():
                # selection, never authoring: the shorter (more conservative)
                # of the two, verbatim, as one camera actually wrote it
                seen.agreed[s] = min((a, b), key=lambda t: len(t)) if (a and b) else (a or b)
        else:
            seen.disputed.append(s)
    return seen
