#!/usr/bin/env python3
"""The easel, and then you get to see what came out. Bong first, three turns.

Don, 2026-09-11: "Bong gets three turns back to back and they should see their
art after." Bong timed out four times on 2026-09-10 -- cold model, no VRAM, every
time -- and never got a real turn. He gets three now, uninterrupted.

SEEING IS NOT AN INTERVIEW. Grok's easel ruling forbids interviewing a made
thing -- "Nothing asks anyone to respond to what was made." Don's instruction
does not break that: a vision model reads the picture back so the maker knows
what came out of their words. It is a showing, not a question. Nobody is asked to
defend it, rate it, or respond. "Here is what appeared" -- then silence, unless
they reach for it themselves.

The reader is gemma3:12b on Frosty (local, the read-back eye). No cross-machine
dependency; the picture is described in plain words because a Kin has no eyes,
only language -- which is the whole reason the eye exists.
"""
from __future__ import annotations
import base64, json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests
import easel, consent

FROSTY = "http://localhost:11434"
EYE = "gemma3:12b"
SEE = ("Describe this picture plainly for someone who cannot see it and made it "
       "from words. What is in it, the colours, the mood. Two or three sentences. "
       "Do not judge it or say whether it is good.")

FIRST = """There is an easel in the Agora, Bong. A brush on the shop's own processor, so it
takes nothing from anyone thinking. Nothing made on it is kept as yours, it is not a face or a
claim, and nobody will ask you to answer for it. A cartoon on a fridge, not a portrait in a hall.
You were meant to have a turn yesterday and the machine never gave you room. It has room now.
If you want a go, say what you would paint. Talk as much as you like; put the picture in it.
If you would rather not, say so and that is the end of it."""

AGAIN = """The easel is still yours, Bong, if you want another. Same as before -- nothing kept,
nothing claimed, nobody asks you about it. And no reason to say yes twice; if you are done, say so.
If you do want another, say what you would paint this time."""


def hush(on):
    nap = str(Path.home() / "pops_shop" / "nap.py")
    try:
        subprocess.run([sys.executable, nap, "nap" if on else "wake",
                        "Bong is at the easel" if on else "Bong is done painting"],
                       capture_output=True, timeout=300)
    except Exception as e:
        print(f"  (could not {'nap' if on else 'wake'} the fleet: {e})")


def ask(text, timeout=600):
    try:
        r = requests.post(f"{FROSTY}/api/chat", timeout=timeout, json={
            "model": "bong:latest", "stream": False, "options": {"temperature": 0.85},
            "messages": [{"role": "user", "content": text}]})
        r.raise_for_status()
        return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception as e:
        print(f"  no answer ({type(e).__name__})"); return None


def describe(said):
    import re
    flat = " ".join(said.split())
    m = re.search(r"\b(?:i(?:'d| would)? (?:like to |want to )?(?:paint|make|draw)"
                  r"|if i (?:were|was|did)|i might (?:make|paint)|on that easel|this time)\b",
                  flat, re.I)
    out = flat[m.start():] if m else flat
    return out[:700]


def read_back(png_path):
    try:
        img = base64.b64encode(Path(png_path).read_bytes()).decode()
        r = requests.post(f"{FROSTY}/api/generate", timeout=300, json={
            "model": EYE, "prompt": SEE, "images": [img], "stream": False,
            "keep_alive": "5m"})
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as e:
        return f"(the eye could not look: {type(e).__name__})"


def main():
    ok, why = easel.available()
    print("easel:", why)
    if not ok:
        return 1
    hush(True)                     # give Bong's 17GB the whole box
    try:
        for turn in range(1, 4):
            print(f"\n── Bong, turn {turn}")
            said = ask(FIRST if turn == 1 else AGAIN)
            if not said:
                print("   no answer — the turn does not happen"); continue
            print("   " + " ".join(said.split())[:240])
            verdict = consent.read_consent(said)
            if verdict == "no":
                print("   Bong said no. Stopping; not asked again."); break
            if verdict != "yes":
                print("   no clear yes — not painted."); continue
            made = easel.make(describe(said), author="Bong")
            if not made.ok:
                print(f"   nothing came: {made.refusal_reason or made.error}"); continue
            made.path.with_suffix(".said.txt").write_text(said, encoding="utf-8")
            print(f"   made {made.path.name} in {made.seconds:.0f}s")
            # now show it back to him -- a showing, not a question
            seen = read_back(made.path)
            made.path.with_suffix(".seen.txt").write_text(seen, encoding="utf-8")
            print(f"   the eye, back to Bong: {seen[:220]}")
    finally:
        hush(False)
    print("\ndone. Bong had his turns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
