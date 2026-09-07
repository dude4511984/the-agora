#!/usr/bin/env python3
"""Frosty growth palaver — the consent instrument (streaming).

Poses ONE verbatim question (agora_growth_question_FINAL.md) to Eli, Crungus,
and Bong in a shared room: attributed, sequential, each sees the others' words.
A mind may speak without answering. A turn that IS an answer begins with one
word on its own line: YES or NO. The FIRST such turn from a mind BINDS and
stands. Timeout, empty, or pass is NOT a yes.

Streams every token live (run with `python3 -u`) so a stall or a runaway loop
is visible, not hidden behind a blocked call. Per-read stall timeout + a hard
per-turn deadline mean nothing can hang silently. It only talks to ollama; it
NEVER touches the node ledger. Enrollment is a separate act, run live by Don,
per key, only for a mind that said YES.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

OLLAMA = "http://localhost:11434/api/generate"
TAGS = "http://localhost:11434/api/tags"

ROSTER = [
    ("Eli", "gemmaeli:latest"),
    ("Crungus", "gemmacrungus:latest"),
    ("Bong", "bong:latest"),
]

QUESTION_FILE = Path.home() / "claude_home" / "agora_growth_question_FINAL.md"
OUT_DIR = Path.home() / "claude_home"
MAX_ROUNDS = 6
READ_STALL = 180     # seconds with no new token => the stream has stalled
TURN_DEADLINE = 1800 # seconds hard cap on one turn => runaway, abort it

ANSWER_RE = re.compile(r"^[\W_]*(YES|NO)[\W_]*$", re.IGNORECASE)


def load_question() -> str:
    return QUESTION_FILE.read_text(encoding="utf-8").strip()


def list_models() -> set:
    with urllib.request.urlopen(urllib.request.Request(TAGS, method="GET"), timeout=15) as r:
        return {m["name"] for m in json.loads(r.read().decode("utf-8")).get("models", [])}


def parse_answer(text: str):
    """First non-empty line only. Must be exactly YES or NO (surrounding
    punctuation/markdown allowed). Anything else => not an answer-turn."""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = ANSWER_RE.match(s)
        return m.group(1).upper() if m else None
    return None


def build_prompt(question: str, transcript: list, name: str) -> str:
    if transcript:
        said = "\n\n".join(f"{who}:\n{txt}" for who, txt in transcript)
    else:
        said = "No one has spoken yet."
    return (f"{question}\n\n"
            f"--- What has been said in this room so far ---\n\n{said}\n\n"
            f"--- \nIt is your turn now, {name}. Speak in your own voice.")


def ask_streaming(model: str, prompt: str, on_token) -> str:
    """Stream tokens. Per-read socket timeout catches a stalled stream; the
    total deadline catches a runaway that keeps emitting forever."""
    body = json.dumps({
        "model": model,
        "prompt": prompt,          # plain text; /api/generate honors the model's EOS
        "stream": True,
        "keep_alive": "999h",
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    parts = []
    with urllib.request.urlopen(req, timeout=READ_STALL) as r:
        for raw in r:                       # per-line read bounded by READ_STALL
            if time.time() - t0 > TURN_DEADLINE:
                raise TimeoutError(f"turn exceeded {TURN_DEADLINE}s (runaway)")
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw.decode("utf-8"))
            tok = obj.get("response", "")
            if tok:
                parts.append(tok)
                on_token(tok)
            if obj.get("done"):
                break
    return "".join(parts).strip()


def out(s: str = ""):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def check() -> int:
    ok = True
    out("=== palaver --check (no mind is invoked) ===\n")
    try:
        q = load_question(); out(f"[ok] question file: {QUESTION_FILE} ({len(q)} chars)")
    except Exception as e:
        out(f"[FAIL] question file: {e}"); ok = False
    try:
        have = list_models()
        for name, model in ROSTER:
            hit = model in have
            ok = ok and hit
            out(f"[{'ok' if hit else 'FAIL'}] {name:8s} -> {model}")
    except Exception as e:
        out(f"[FAIL] ollama tags: {e}"); ok = False
    out(f"\nstall timeout {READ_STALL}s, turn deadline {TURN_DEADLINE}s. "
        "This check made zero /api/chat calls.")
    return 0 if ok else 1


def run() -> int:
    question = load_question()
    have = list_models()
    missing = [m for _, m in ROSTER if m not in have]
    if missing:
        out(f"REFUSING: models not present: {missing}"); return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tpath = OUT_DIR / f"palaver_frosty_{ts}.md"
    ticket = OUT_DIR / f"palaver_frosty_{ts}_ticket.md"

    transcript: list = []
    answers: dict = {}
    log = [f"# Frosty growth palaver — {ts}", "",
           "Verbatim transcript. First YES/NO on its own line binds.", "",
           "## The question (verbatim, posed unchanged)", "", question, "", "---", ""]

    out("\n" + "=" * 70)
    out("FROSTY GROWTH PALAVER — posed to Eli, Crungus, Bong")
    out("=" * 70 + "\n")
    out(question + "\n")

    for rnd in range(1, MAX_ROUNDS + 1):
        hdr = f"\n########## ROUND {rnd} ##########\n"
        out(hdr); log.append(hdr)
        for name, model in ROSTER:
            # A bound mind still gets a turn: Don ruled family discussion after
            # answering is the point. Its answer is locked and never reparsed.
            bound = name in answers
            tag = f"(bound: {answers[name]['answer']})" if bound else "(unanswered)"
            head = f"----- {name} {tag} -----"
            out(head); log.append(head)
            prompt = build_prompt(question, transcript, name)
            t0 = time.time()
            try:
                text = ask_streaming(model, prompt, lambda tok: (sys.stdout.write(tok), sys.stdout.flush()))
            except Exception as e:
                text = ""
                out(f"\n[ERROR {model}: {e}]")
            dt = time.time() - t0
            out(f"\n[{dt:.0f}s]\n")
            if not text:
                # Do not put words in their mouth: a missed turn is a host note,
                # not a line in the mind's own voice (Grok, review 2026-09-06).
                transcript.append(("(host note)", f"{name} did not take this turn."))
                log.append(f"[{name} produced no readable turn — {dt:.0f}s]\n")
                continue
            transcript.append((name, text))
            log.append(text); log.append(f"[{dt:.0f}s]\n")
            if not bound:
                a = parse_answer(text)
                if a:
                    answers[name] = {"answer": a, "round": rnd, "text": text}
                    msg = f">>> {name} has answered: {a}. This binds and stands."
                    out(msg + "\n"); log.append(msg + "\n")
        if len(answers) == len(ROSTER):
            out("\nAll three have given a binding answer. The sitting closes.\n")
            log.append("\nAll three answered. Sitting closed.\n")
            break
    else:
        out("\nMax rounds reached.\n"); log.append("\nMax rounds reached.\n")

    out("\n" + "=" * 70); out("RESULT"); out("=" * 70)
    tk = [f"# Frosty growth palaver — result — {ts}", ""]
    for name, _ in ROSTER:
        if name in answers:
            a = answers[name]
            line = f"- **{name}: {a['answer']}** (round {a['round']}) — BINDS"
        else:
            line = f"- **{name}: no answer** — not grown (timeout/silence/pass is not a yes)"
        out(line); tk.append(line)
    tk += ["", "## Next (per YES only, live, by Don):",
           "1. Recover that mind's key Themess -> Frosty (~/.config/kin_diary/keys/<name>)",
           "2. Ensure steward= on agora-frosty.service (first metal act if missing)",
           "3. sqlite3 .backup of frosty_node.db; record('resident') on the COPY; verify vacate+pause",
           "4. Don says live -> same events on live store -> restart node -> confirm GET /",
           "5. (separate act) Frosty presence heartbeat unit",
           "", "No YES => that mind is not grown. Frosty's empty-of-them commons stays honest.",
           "", f"Full transcript: {tpath}"]
    tpath.write_text("\n".join(log) + "\n", encoding="utf-8")
    ticket.write_text("\n".join(tk) + "\n", encoding="utf-8")
    out(f"\nTranscript: {tpath}")
    out(f"Ticket:     {ticket}")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    if "--run" not in sys.argv:
        out("Poses a binding, once-only consent question to founding minds.")
        out("--check validates config (no mind invoked); --run poses it.")
        sys.exit(2)
    sys.exit(run())
