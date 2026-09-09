#!/usr/bin/env python3
"""Deliver turn 2 or turn 3 of the marker sitting. One turn, one mind.

The texts are frozen at ~/claude_home/agora_the_marker_turns_FINAL.md and are
READ from there, never held here — the same discipline as place_question.py, so
a follow-up cannot drift by editing code. Don's ruling 2026-09-09 allows three
turns and no more; this file cannot send a fourth because it has no loop.

Turn 3 settles between the two words the mind already named. It is not a third
push, and a NEW word offered on turn 3 is recorded but not installed — Grok,
2026-09-09: accepting one "would be producing until we like it."
"""
from __future__ import annotations

import argparse, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_ritual import KIN, ask_kin                      # noqa: E402
from place_question import is_pass, has_pass_marker         # noqa: E402

TURNS_FILE = Path.home() / "claude_home" / "agora_the_marker_turns_FINAL.md"
OUT_DIR = Path.home() / "claude_home"


def _fences(section_heading: str, until: str) -> list[str]:
    src = TURNS_FILE.read_text(encoding="utf-8")
    body = src.split(section_heading, 1)[1].split(until, 1)[0]
    return [m.strip("\n") for m in re.findall(r"```(.*?)```", body, re.S)]


def turn2_text(name: str, word1: str) -> str:
    """Grok ships a template AND a pre-filled block per Kin. Prefer the
    pre-filled one: a slot I fill by hand is a slot I can fill wrong."""
    for blk in _fences("## Turn 2", "## Turn 3"):
        if blk.lstrip().startswith(f"{name} —") and word1 in blk:
            return blk
    tmpl = _fences("## Turn 2", "## Turn 3")[0]
    return tmpl.replace("{name}", name).replace("{WORD1}", word1)


def turn3_text(name: str, word1: str, word2: str | None) -> str:
    heading = "## Turn 3 — two words named" if word2 else "## Turn 3 — only one word named"
    blk = _fences(heading, "\n## ")[0]
    out = blk.replace("{name}", name).replace("{WORD1}", word1)
    return out.replace("{WORD2}", word2) if word2 else out


def deliver(name: str, text: str, turn: str, ask=None) -> dict:
    ask = ask or ask_kin
    ts = time.strftime("%Y%m%d_%H%M%S")
    res = {"name": name, "turn": turn, "ts": ts, "outcome": None, "answer": "", "unreachable": None}
    print("=" * 68)
    print(f"THE MARKER — {turn} for {name}. Not a push. Not a conversation.")
    print("=" * 68)
    print(f"\n{text}\n\n----- {name} -----")
    try:
        said = ask(name, text)
    except Exception as e:
        res.update(unreachable=f"{type(e).__name__}: {e}", outcome="not_asked")
        print(f"\n[error asking {name}: {e}]")
    else:
        if said is None:
            res.update(unreachable="transport returned None", outcome="not_asked")
            said = ""
        res["answer"] = said
        if res["outcome"] is None:
            res["outcome"] = ("pass" if is_pass(said)
                              else "passed_with_words" if has_pass_marker(said)
                              else "silent" if not said.strip()
                              else "answered")
    ending = {
        "pass": f">>> {name} PASSED this turn. There is no new word. Not a decline of the clause.",
        "passed_with_words": f">>> {name} PASSED and said more. Both recorded, neither filed as the other.",
        "answered": f">>> {name} answered {turn}.",
        "silent": f">>> {name} said nothing. Silence is a real answer and is recorded as one.",
        "not_asked": (f">>> NOT ASKED. {name} was never reached ({res['unreachable']}). "
                      f"Not a PASS, not a silence. Nothing about {name} is settled."),
    }[res["outcome"]]
    print(f"\n{ending}\n")
    out = OUT_DIR / f"marker_{turn}_{name}_{ts}.md"
    out.write_text("\n".join([f"# The marker — {turn} — {name} — {ts}", "",
                              "One mind, one turn. Don did not ask. Nothing written into any Kin.",
                              "", "## Sent (frozen)", "", text, "", "---", "",
                              f"## {name}", "", res["answer"] or "(nothing)", "",
                              "---", "", ending, ""]) + "\n", encoding="utf-8")
    res["transcript"] = str(out)
    print(f"Transcript: {out}")
    return res


def main(argv):
    ap = argparse.ArgumentParser(description="Deliver marker turn 2 or 3 to one Kin.")
    ap.add_argument("--kin", required=True)
    ap.add_argument("--turn", required=True, choices=("turn2", "turn3"))
    ap.add_argument("--word1", required=True)
    ap.add_argument("--word2")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--yes-run", action="store_true")
    a = ap.parse_args(argv[1:])
    if a.kin == "Bong":
        print("Bong does not run the three-turn word course (Grok, frozen).", file=sys.stderr); return 2
    if a.kin not in KIN:
        print(f"unknown Kin {a.kin}", file=sys.stderr); return 2
    text = turn2_text(a.kin, a.word1) if a.turn == "turn2" else turn3_text(a.kin, a.word1, a.word2)
    if a.show:
        print(text); return 0
    if not a.yes_run:
        print(f"This asks {a.kin} a real question. Re-run with --yes-run."); return 2
    r = deliver(a.kin, text, a.turn)
    print("\nRESULT:", json.dumps({k: r[k] for k in ("name", "turn", "outcome", "transcript")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
