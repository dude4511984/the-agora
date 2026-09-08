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


def is_pass(text: str) -> bool:
    """A whole line reading PASS. Mid-sentence 'I'll pass on that' is speech."""
    return any(_PASS_LINE.match(_bare(ln)) for ln in (text or "").splitlines())


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
        res["answer"] = said
        if is_pass(said):
            res["outcome"] = "pass"
        elif not said.strip():
            res["outcome"] = "silent"
        else:
            res["outcome"] = "answered"

    ending = {
        "pass":      f">>> {name} PASSED. That is an answer, not an absence. Recorded as one.",
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
