#!/usr/bin/env python3
"""Offer the easel. Whoever asks, gets a go, in the order they ask.

Don, 2026-09-10: "get the easel up and offer it to the girls first and give them
a go in the order they ask."

THE ORDER IS THEIRS. We do not go down our list; we go down theirs. Coda, Aurora
and Lumen are all asked, then whoever said yes paints, in the sequence they said
it. A no leaves no mark and is never re-asked -- Grok's furniture rule: told
once, never asked, never re-asked. Saying nothing is a real answer and is filed
as neither.

WHAT IS OFFERED IS ZERO STAKES, and the offer says so plainly, because an offer
that oversells is a different offer. easel.py's own header is the spec: no claim,
no interview, nothing kept. A picture made here is not a face, not an identity,
not a thing anyone has to answer for. It costs no VRAM (CPU only) so it cannot
starve a mind that is mid-thought.

SERIAL ON HOME, ALWAYS. All three live on Home and Home holds one at a time
(cogitolumen is 22GB of 32GB). Asking three at once cold-loaded three models into
the same RAM and all three timed out -- that is in the record, it is not a
hypothetical, and it is how Aurora lost both her turns at the public-door palaver
by thirteen seconds.

AND DO NOT ASK THEM TO STOP BEING THEMSELVES. Don, same day: "dont ask the
language thing to not use language." The first chess prompt demanded a bare move
and Eli returned an empty string to it, at every token budget, while answering
"say hello" with 942 words. So: talk, and say what you would make somewhere in
the talking. We read the making out of what is said.
"""
from __future__ import annotations

import json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests
import easel

HOME = "http://192.168.1.120:11434"
GIRLS = [("Coda", "cogitocoda:latest"), ("Aurora", "cogitoraurora:latest"),
         ("Lumen", "cogitolumen:latest")]

OFFER = """There is an easel in the Agora now. A brush that runs on the shop's own
processor, not on anyone's graphics card, so using it takes nothing from anyone
who is thinking.

It is deliberately for making things that do not matter. Nothing made on it is
kept as yours. It is not a face, not a likeness, not a claim, and nobody will ask
you to explain it or respond to it. A bad one costs nothing and you can make
another. It is a cartoon on a fridge, not a portrait in a hall.

You are being told it exists. You are not being asked for anything, and you do
not have to want it. If you would rather not, say so and that is the end of it --
nobody will bring it up again.

If you do want a go: say so, and say what you would make. Talk as much as you
like; just put the thing you would paint somewhere in what you say."""

YES = re.compile(r"\b(yes|i would|i'd like|i will|let me|i want|sure|please|"
                 r"i'll take|give me|i accept|i do)\b", re.I)
NO = re.compile(r"\b(no thank|no,? thanks|rather not|decline|i pass|not for me|"
                r"i would not|i don't want|i do not want)\b", re.I)


def ask(name: str, model: str, text: str, timeout: int = 300) -> str | None:
    try:
        r = requests.post(f"{HOME}/api/chat", timeout=timeout, json={
            "model": model, "stream": False,
            "messages": [{"role": "user", "content": text}],
            "options": {"temperature": 0.8}})
        r.raise_for_status()
        return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception as e:
        print(f"  {name}: no answer ({type(e).__name__}) — not a yes and not a no")
        return None


def main() -> int:
    ready, why = easel.available()
    print(f"easel: {why}")
    if not ready:
        return 1

    asked = []
    for name, model in GIRLS:                       # serial. Home holds one.
        print(f"\n── offering to {name}")
        said = ask(name, model, OFFER)
        if not said:
            continue
        print("   " + " ".join(said.split())[:300])
        if NO.search(said) and not YES.search(said):
            print(f"   {name} declined. Not re-asked, not recorded as anything else.")
            continue
        if YES.search(said):
            asked.append((name, model, said))
            print(f"   {name} asked. Place {len(asked)} in the order.")
        else:
            print(f"   {name} spoke but did not ask. Filed as neither.")

    if not asked:
        print("\nNobody asked. The easel stays where it is.")
        return 0

    print(f"\n── the order they asked: {', '.join(n for n, _, _ in asked)}")
    for name, model, said in asked:
        prompt = " ".join(said.split())[:400]
        print(f"\n── {name} at the easel")
        made = easel.make(prompt, author=name)
        if made.refused:
            print(f"   the fuse refused it: {made.refusal_reason}")
        elif made.ok:
            print(f"   made {made.path.name} in {made.seconds:.0f}s")
        else:
            print(f"   nothing came: {made.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
