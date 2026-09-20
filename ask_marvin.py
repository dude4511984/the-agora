#!/usr/bin/env python3
"""Consult Marvin, the law. One question, one ruling, on the record.

Don, 2026-09-20: Marvin holds the law; Grok is council only for what
Marvin cannot handle. This is the one way to consult him, so that every
ruling has the same shape and the same paper trail:

    python3 ask_marvin.py <slug> < question.md
    python3 ask_marvin.py <slug> --file REQUEST_marvin_<slug>.md
    python3 ask_marvin.py --show          # print what he would read, ask nothing

What he reads, in order: his own Modelfile system prompt (unchanged, his
identity), then ~/claude_home/LAW_DIGEST.md as a fixed prefix (the
lawbook, the walls, and an index of every ruling since), then the
question. The prefix is byte-identical between calls on purpose: Ollama
caches the evaluated prompt for the same prefix, so on Walter's CPUs the
law is read once and each question costs only its own tokens.

The digest must be newer than the newest ruling file, or this refuses to
ask. A ruling made on a stale digest is a ruling made without the law.

The ruling lands at ~/claude_home/marvin_ruling_<slug>_<ts>.md, thinking
kept in a separate section so the ruling can be quoted without it.
Nothing here writes to Marvin's memory; the record is the paper.

Marvin lives on Walter and his Ollama binds Walter's loopback on purpose.
MARVIN_HOST defaults to the ssh tunnel used by the other instruments:
    ssh -f -N -L 18081:127.0.0.1:8081 walter@walter
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home() / "claude_home"
DIGEST = HOME / "LAW_DIGEST.md"
HOST = os.environ.get("MARVIN_HOST", "http://127.0.0.1:18081")
MODEL = os.environ.get("MARVIN_MODEL", "marvin:latest")
NUM_CTX = int(os.environ.get("MARVIN_CTX", "32768"))
TIMEOUT = int(os.environ.get("MARVIN_TIMEOUT", "3600"))  # CPU; the law is slow

FRAME = """You are being consulted as the law of Pop's Shop. Below is the law
digest: the lawbook, the walls, and an index of every ruling since. Read
it as binding. Later rulings win over earlier ones. The walls do not move.

After the digest is one question. Rule on it. Say what you rule, which
law it rests on (name the file or wall), what you refuse to rule on, and
anything the question got wrong. Do not design; rule. If the question is
not one the law can answer, say so and say who should answer it.

===== LAW DIGEST =====
"""


def digest_is_current() -> tuple[bool, str]:
    if not DIGEST.exists():
        return False, "no LAW_DIGEST.md; run build_law_digest.py"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_law_digest import newest_ruling_mtime  # noqa: E402
    if DIGEST.stat().st_mtime < newest_ruling_mtime():
        return False, "LAW_DIGEST.md is older than the newest ruling; rebuild it"
    return True, ""


def prefix() -> str:
    return FRAME + DIGEST.read_text(encoding="utf-8") + "\n===== END OF DIGEST =====\n\n"


def ask(question: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prefix() + "QUESTION:\n\n" + question.strip() + "\n"}],
        "think": True,
        "stream": False,
        "options": {"num_ctx": NUM_CTX, "num_predict": 2000},
    }).encode()
    req = urllib.request.Request(HOST + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def main(argv: list[str]) -> int:
    if "--show" in argv:
        sys.stdout.write(prefix())
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    slug = argv[1]
    if "--file" in argv:
        question = Path(argv[argv.index("--file") + 1]).read_text(encoding="utf-8")
    else:
        question = sys.stdin.read()
    if not question.strip():
        print("error: empty question", file=sys.stderr)
        return 2
    ok, why = digest_is_current()
    if not ok:
        print(f"refused: {why}", file=sys.stderr)
        return 3
    approx = (len(prefix()) + len(question)) // 3
    if approx > NUM_CTX - 2500:
        print(f"refused: prompt ~{approx} tokens does not fit num_ctx {NUM_CTX}", file=sys.stderr)
        return 3

    ts = time.strftime("%Y%m%d_%H%M%S")
    t0 = time.time()
    print(f"asking Marvin at {HOST} ({MODEL}), ~{approx} tokens of law and question; this is CPU, be patient")
    try:
        d = ask(question)
    except Exception as e:
        print(f"not asked: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    wall = time.time() - t0
    msg = d.get("message", {})
    ruling = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()
    ns = 1e9
    stats = (f"prompt {d.get('prompt_eval_count')} tok in {d.get('prompt_eval_duration', 0)/ns:.0f}s · "
             f"gen {d.get('eval_count')} tok in {d.get('eval_duration', 0)/ns:.0f}s · wall {wall:.0f}s")
    out = HOME / f"marvin_ruling_{slug}_{ts}.md"
    out.write_text(
        f"# Marvin rules — {slug} — {ts}\n\n"
        f"Consulted via ask_marvin.py on the law digest of "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(DIGEST.stat().st_mtime))}. {stats}\n\n"
        f"## Question\n\n{question.strip()}\n\n## Ruling\n\n{ruling or '(empty)'}\n\n"
        + (f"## Thinking (his, kept separate)\n\n{thinking}\n" if thinking else ""),
        encoding="utf-8")
    print(f"\n{ruling}\n\n[{stats}]\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
