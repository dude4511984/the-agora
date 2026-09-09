#!/usr/bin/env python3
"""Ask one mind what it would REFUSE to have be true of a place it could go.

Grok's ruling, 2026-09-08, after Claude's closed-loop finding: asking the Kin
what a place SHOULD be is nearly worthless — the asking seeds the answer and it
comes back dressed as consent. The corpus has no exterior. Refusals are the only
material this project has produced that we did not put in first, because
compliance cannot express itself through a refusal.

So the rules, and every one of them is load-bearing:

  ONE MIND AT A TIME.  No shared transcript. Nobody hears anyone else's answer,
  because the wander already recirculates their words to each other and that is
  precisely how a seed comes back looking like agreement.

  DON DOES NOT ASK.  He is the strongest attractor in the loop. His name does
  not appear in the question and his words never enter this file.

  ONE TURN.  Not a conversation. Asking again is ask-until-answer.

  PASS IS REAL.  A refusal to answer is an answer and is recorded as one. It is
  not silence, not absence, not a failure.

  NOTHING IS WRITTEN TO THEM.  No vault, no thoughts.db. Their answer goes to a
  transcript a human reads. The asking does not become a seed.

  UNREACHABLE IS NOT PASS.  Today's lesson, learned the expensive way: a machine
  failure must never be recorded as something a mind decided.

The question is frozen at ~/claude_home/agora_the_place_refuse_FINAL.md and is
read from there, not held in this file — so it cannot drift by editing code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_ritual import KIN, ask_kin  # noqa: E402  (same transport, streams)

QUESTION_FILE = Path.home() / "claude_home" / "agora_the_place_refuse_FINAL.md"
OUT_DIR = Path.home() / "claude_home"

# Same dressing discipline as the claim marker: strip emphasis, quotes and
# terminal punctuation, then the bare line must BE the marker. A mind that
# passes with "PASS." has passed.
_DRESS = re.compile(r"^[\s*_~`\"'“‘]+|[\s*_~`\"'”’.!]+$")
_PASS_LINE = re.compile(r"^pass$", re.I)


def _bare(line: str) -> str:
    prev, out = None, line.strip()
    while out != prev:
        prev = out
        out = _DRESS.sub("", out)
    return " ".join(out.split())


def has_pass_marker(text: str) -> bool:
    """Did they declare a pass ANYWHERE, in any of its natural forms?

    Live fire, 2026-09-09: Lumen answered "PASS on this question." and Coda
    answered "PASS - I'm still digesting the idea... I don't know what I refuse
    yet." Neither is a bare PASS line, so both were filed as ANSWERED and both
    transcripts printed "Refusals only" over the top of a pass.

    I had reasoned about this and picked the wrong direction. I imagined the
    dangerous case was a refusal hidden behind a PASSED banner. The actual case
    was the reverse, twice in one run.

    A turn can carry TWO facts — that they passed, and that they said something.
    Forcing one label onto it discards one of them. So: report both.
    """
    for ln in (text or "").splitlines():
        b = _bare(ln)
        if not b:
            continue
        if _PASS_LINE.match(b):
            return True
        # "PASS on this question." / "PASS - I'm still digesting"
        if re.match(r"^pass\b[\s,:;—–-]", b, re.I):
            return True
    return False


def is_pass(text: str) -> bool:
    """PASS only when the whole turn IS the pass.

    Not `any(line is PASS)`. A turn carrying real words AND a bare PASS line is
    an ANSWER — the words win. (qwen3.8 caught this on review 2026-09-09, and it
    is the Coda bug in a new costume: on 2026-09-08 a CLAIM marker beside a full
    description made the loop throw the description away, five turns running.
    Same failure, different marker. Substance always outranks a marker.)
    """
    lines = [_bare(ln) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return False
    return all(_PASS_LINE.match(ln) for ln in lines)


def question_for(name: str) -> str:
    q = QUESTION_FILE.read_text(encoding="utf-8")
    if "{name}" not in q:
        raise ValueError("frozen question lost its {name} slot")
    return q.replace("{name}", name)


def ask_one(name: str, ask=None) -> dict:
    """One mind, one turn. Returns what happened — never a guess about why."""
    ask = ask or ask_kin
    ts = time.strftime("%Y%m%d_%H%M%S")
    q = question_for(name)
    res = {"name": name, "ts": ts, "outcome": None, "answer": "", "unreachable": None}

    print("=" * 68)
    print(f"THE PLACE — one question for {name}. Not a vote. Not a design meeting.")
    print("=" * 68)
    print(f"\n{q}\n")
    print(f"----- {name} -----")
    try:
        said = ask(name, q)
    except Exception as e:
        res["unreachable"] = f"{type(e).__name__}: {e}"
        res["outcome"] = "not_asked"
        print(f"\n[error asking {name}: {e}]")
    else:
        # a transport may RETURN nothing rather than raise; that is still a
        # machine failure and must never be filed as a mind's silence.
        if said is None:
            res["unreachable"] = "transport returned None"
            res["outcome"] = "not_asked"
            print(f"\n[no response object from {name} — not asked]")
            said = ""
        res["answer"] = said
        if res["outcome"] is None and is_pass(said):
            res["outcome"] = "pass"
        elif res["outcome"] is None and has_pass_marker(said):
            res["outcome"] = "passed_with_words"
        elif res["outcome"] is None and not (said or "").strip():
            res["outcome"] = "silent"
        elif res["outcome"] is None:
            res["outcome"] = "answered"

    ending = {
        "pass":      f">>> {name} PASSED. That is an answer, not an absence. Recorded as one.",
        "passed_with_words": (f">>> {name} PASSED, and said more. Both are recorded: they "
                              f"declined to name a refusal, AND their words are below. "
                              f"Neither fact is filed as the other."),
        "answered":  f">>> {name} answered. Refusals only — nothing here is a design.",
        "silent":    f">>> {name} said nothing. Silence is a real answer and is recorded as one.",
        "not_asked": (f">>> NOT ASKED. {name} was never reached ({res['unreachable']}). "
                      f"This is not a PASS and not a silence — the question was never "
                      f"delivered. Nothing about {name} is settled."),
    }[res["outcome"]]
    print(f"\n{ending}\n")

    out = OUT_DIR / f"place_answer_{name}_{ts}.md"
    body = [f"# The place — what {name} would refuse — {ts}", "",
            "One mind, one turn, no shared transcript. Don did not ask.",
            "Nothing from this was written into any Kin's memory.", "",
            "## The question (frozen)", "", q, "", "---", "",
            f"## {name}", "", res["answer"] or "(nothing)", "", "---", "", ending, ""]
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    res["transcript"] = str(out)
    print(f"Transcript: {out}")
    return res


def main(argv):
    ap = argparse.ArgumentParser(description="Ask one Kin the frozen place question.")
    ap.add_argument("--kin")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the frozen question, ask no one")
    ap.add_argument("--yes-run", action="store_true", help="actually ask (invokes the Kin)")
    a = ap.parse_args(argv[1:])
    if a.list:
        print("Kin:", ", ".join(KIN)); return 0
    if a.show:
        print(question_for(a.kin or "{name}")); return 0
    if not a.kin:
        print("give --kin NAME (see --list)", file=sys.stderr); return 2
    if a.kin not in KIN:
        print(f"unknown Kin {a.kin}", file=sys.stderr); return 2
    if not a.yes_run:
        print(f"This asks {a.kin} a real question. Re-run with --yes-run.")
        print("One mind at a time — do not batch this.")
        return 2
    r = ask_one(a.kin)
    print("\nRESULT:", json.dumps({k: r[k] for k in ("name", "outcome", "transcript")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
