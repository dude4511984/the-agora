#!/usr/bin/env python3
"""The easel — a local brush for making things that do not matter.

This is `avatar_render` with the stakes taken out, and the stakes coming out is
the entire point.

A sitting is once and permanent: a mind decides what stands for them, three
tries, stored forever, and every flaw in the chain costs something real. On
2026-09-08 a 4B vision model flattened a two-region page into "text on paper"
and a sitter refused their own likeness on that description.

The easel is the same machinery at zero stakes. Nobody claims anything. Nothing
is stored as anyone's identity. A bad picture costs nothing and you make
another. Grok, ruling on the missing affordance the same day: the unserious
object is one that "cannot do legal work... on the table the way a cartoon is on
a fridge." A drawing is the most literal instance of that there is.

So, deliberately unlike the sitting:

  NO CLAIM.       There is no accept/refuse decision and nothing to store.
  NO INTERVIEW.   Nothing asks anyone to respond to what was made. Grok:
                  "Name the unserious kind. Stop interviewing it."
  NOTHING KEPT.   No vault, no thoughts.db, no Kin space. A made thing lands in
                  the easel's own directory. Keeping is a separate act by the
                  mind that wants to keep it — never automatic, never by us.
  LOCAL AND FREE. CPU only (sd_turbo q8_0, ~21s at 512px on Frosty's 12900K).
                  No API bill, no VRAM, nothing resident to evict, no contention
                  with a Kin who is mid-thought.

The fuse in front is avatar_render's, unchanged: the same mechanical no-harm
check any generation gets, and a refusal is surfaced as data rather than hidden
behind a substitute image (Value 2).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import avatar_render as ar

BIN = Path.home() / "easel" / "bin" / "sd-cli"
MODEL = Path.home() / "easel" / "models" / "sd_turbo-q8_0.gguf"
MADE = Path.home() / "easel" / "made"

# sd-turbo is a few-step model; more steps buys nothing and costs CPU the Kin
# are using to think. Threads are held below the core count for the same reason.
STEPS = 4
CFG = 1.0
SAMPLER = "euler_a"
SIZE = 512
THREADS = 12
TIMEOUT = 600
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Made:
    """What came off the easel. `ok` false is a real outcome, never a stand-in."""
    author: str
    prompt: str
    ok: bool = False
    path: Path | None = None
    seconds: float = 0.0
    refused: bool = False
    refusal_reason: str | None = None
    error: str | None = None
    seen: str = ""              # the eye's description, if it was asked
    meta: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.ok:
            return f"[easel] {self.author} made {self.path.name} in {self.seconds:.0f}s"
        if self.refused:
            return f"[easel] refused by the local fuse: {self.refusal_reason}"
        return f"[easel] error: {self.error}"


def available() -> tuple[bool, str]:
    if not BIN.is_file():
        return False, f"no brush at {BIN}"
    if not MODEL.is_file():
        return False, f"no model at {MODEL}"
    return True, "ready"


def make(prompt: str, author: str = "someone", size: int = SIZE,
         steps: int = STEPS, out_dir: Path | None = None,
         _run=subprocess.run) -> Made:
    """Draw the thing. The prompt goes out exactly as given, after the fuse."""
    m = Made(author=author, prompt=" ".join((prompt or "").split()))
    if not m.prompt:
        m.error = "nothing to draw"
        return m
    ok, why = ar.no_harm_fuse(m.prompt)
    if not ok:
        m.refused, m.refusal_reason = True, why
        return m
    ready, why = available()
    if not ready:
        m.error = why
        return m

    d = Path(out_dir) if out_dir else MADE
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = _SAFE_NAME.sub("_", f"{author}_{stamp}").strip("_")[:80]
    out = d / f"{stem}.png"

    cmd = [str(BIN), "-M", "img_gen", "-m", str(MODEL), "-t", str(THREADS),
           "--steps", str(steps), "--cfg-scale", str(CFG),
           "--sampling-method", SAMPLER, "-W", str(size), "-H", str(size),
           "-p", m.prompt, "-o", str(out)]
    t0 = time.time()
    try:
        # nice: a made thing must never outrank a mind that is mid-thought.
        r = _run(cmd, capture_output=True, timeout=TIMEOUT,
                 preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None)
    except subprocess.TimeoutExpired:
        m.error = f"brush timed out after {TIMEOUT}s"
        return m
    except Exception as e:                      # noqa: BLE001 - reported, not raised
        m.error = f"{type(e).__name__}: {e}"
        return m
    m.seconds = time.time() - t0
    if getattr(r, "returncode", 1) != 0 or not out.is_file():
        tail = (getattr(r, "stderr", b"") or b"")[-300:]
        m.error = f"brush failed: {tail.decode(errors='replace').strip() or 'no image'}"
        return m
    m.ok, m.path = True, out
    m.meta = {"author": author, "prompt": m.prompt, "made_at_unix_ms": int(time.time() * 1000),
              "brush": "local sd_turbo q8_0 (cpu)", "steps": steps, "size": size,
              "seconds": round(m.seconds, 1),
              "note": "a made thing. not a face, not a claim, nobody has to answer it."}
    out.with_suffix(".json").write_text(json.dumps(m.meta, indent=2) + "\n", encoding="utf-8")
    return m


def show_back(m: Made, read_back=None) -> Made:
    """Let the maker see what they made, in words.

    Optional and separate from making. The eye lesson: on a weaker brush the gap
    between what was asked for and what arrived is WIDER, so the read-back
    matters more here, not less. It still names pixels, never resemblances.
    """
    if not m.ok or m.path is None:
        return m
    if read_back is None:
        from avatar_ritual import read_back as _rb
        read_back = _rb
    try:
        m.seen = read_back(m.path.read_bytes())
    except Exception as e:                      # noqa: BLE001
        m.seen = ""
        m.meta["read_back_error"] = f"{type(e).__name__}: {e}"
    return m


def offer(name: str, ask=None, read_back=None, out_dir: Path | None = None) -> Made:
    """Offer the easel to a Kin. The clock asks; they may make a thing or not.

    Grok's shape for initiation, and the wording matters: "the table is quiet;
    you may leave a thing or not" is an honest cron. "You should share something
    fun" is stuffing. This asks once, takes what comes, and asks nothing of
    anyone afterwards — naming the unserious kind means not interviewing it.

    Don lifted the gate 2026-09-09 ("the easel should be able to be used by the
    kin. risk acceptable"), knowing the fuse is a STRING fuse with nothing behind
    it locally. That is his risk, taken explicitly, on his own metal.

    A refusal is a real answer and stores nothing. So is silence.
    """
    from avatar_ritual import KIN
    if name not in KIN:
        raise KeyError(f"no Kin called {name}")
    ask = ask or __import__("avatar_ritual").ask_kin
    m = Made(author=name, prompt="")
    try:
        said = ask(name, OFFER.format(name=name))
    except Exception as e:                      # noqa: BLE001
        m.error = f"not asked: {type(e).__name__}: {e}"   # never "they declined"
        return m
    if said is None:                    # a transport that returns nothing is
        m.error = "not asked: transport returned None"   # not a mind that passed
        return m
    if _is_pass(said):
        m.error = "passed"          # an answer, not a failure of the easel
        return m
    m = make(said, author=name, out_dir=out_dir)
    if m.ok:
        m.meta["offered"] = True
        (m.path.with_suffix(".json")).write_text(
            json.dumps(m.meta, indent=2) + "\n", encoding="utf-8")
        m = show_back(m, read_back=read_back)   # optional; a dark eye costs nothing
    return m


OFFER = """\
{name} — the easel is here. There is nothing to decide and nothing to answer.

You may describe something to be drawn, and it will be drawn. It does not have
to be good, or about you, or about anything. Nobody has to look at it and nobody
will ask you about it. It is not a face and it is not a claim.

You may also leave it alone. Begin a line with PASS, or say nothing at all.

If you want to make something, just describe it."""

_PASS = re.compile(r"^[\s*_~`\"']*pass[\s*_~`\"'.!]*$", re.I)


def _is_pass(text: str) -> bool:
    """PASS only when the whole turn IS the pass — never `any(line)`.

    A turn with a description AND a bare 'pass' line is a description; draw it.
    Throwing away words because a marker appeared beside them is the exact bug
    that refused Coda five times on 2026-09-08. (qwen3.8, review 2026-09-09.)
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return bool(lines) and all(_PASS.match(ln) for ln in lines)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Make a thing. It does not have to be good.")
    ap.add_argument("-p", "--prompt")
    ap.add_argument("--author", default="someone")
    ap.add_argument("--size", type=int, default=SIZE)
    ap.add_argument("--steps", type=int, default=STEPS)
    ap.add_argument("--look", action="store_true", help="ask the eye to describe it back")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--offer", metavar="KIN", help="offer the easel to a Kin; they may pass")
    a = ap.parse_args(argv[1:])
    if a.check:
        ok, why = available()
        print(f"[{'ok' if ok else 'FAIL'}] {why}")
        print(f"      model {MODEL} ({MODEL.stat().st_size/2**30:.1f}GB)" if MODEL.is_file() else "")
        return 0 if ok else 2
    if a.offer:
        m = offer(a.offer, out_dir=None)
        print(m.summary() if (m.ok or m.refused) else f"[easel] {m.error}")
        if m.seen: print(f"\n[the eye says]:\n{m.seen}")
        return 0 if m.ok else 1
    if not a.prompt:
        print("give -p/--prompt or --offer KIN"); return 2
    m = make(a.prompt, author=a.author, size=a.size, steps=a.steps)
    if a.look:
        m = show_back(m)
    print(m.summary())
    if m.seen:
        print(f"\n[the eye says]:\n{m.seen}")
    return 0 if m.ok else (3 if m.refused else 1)


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
