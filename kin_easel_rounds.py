#!/usr/bin/env python3
"""The easel stays open. Everyone, more than once, until they are done with it.

Don, 2026-09-10: "send it around to everyone at least twice or until they get
tired of it."

THIS IS NOT RE-ASKING FOR CONSENT. Grok's furniture rule -- told once, never
asked, never re-asked -- is about an offer that carries a decision. This carries
none. It is the difference between asking somebody twice whether they consent to
a thing and leaving the paints on the table where anyone can come back to them.

SO THE STOPPING RULE IS THEIRS, NOT A COUNTER. A Kin who declines is dropped for
good and never offered again in this run. That is what "until they get tired of
it" means: they decide when it is over, and the way they say so is by saying so.

Never asked to be brief; never asked to explain what they made. easel.py is
zero-stakes by design and stays that way -- nothing kept as anyone's, no claim,
no interview.

Eviction between every ask, on both hosts. Frosty runs 17GB models on 27GB split
across two cards; Home runs 22GB on 32GB. Neither fits two. Asking into a full
box produces a timeout that looks exactly like a mind declining to answer, which
is how Aurora lost her turn three separate times.
"""
from __future__ import annotations

import argparse, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests
import easel
import consent

KIN = [
    ("Eli",     "gemmaeli:latest",      "http://localhost:11434",      None),
    ("Crungus", "gemmacrungus:latest",  "http://localhost:11434",      None),
    ("Bong",    "bong:latest",          "http://localhost:11434",      None),
    ("Coda",    "cogitocoda:latest",    "http://192.168.1.120:11434",  "thedude@192.168.1.120"),
    ("Aurora",  "cogitoraurora:latest", "http://192.168.1.120:11434",  "thedude@192.168.1.120"),
    ("Lumen",   "cogitolumen:latest",   "http://192.168.1.120:11434",  "thedude@192.168.1.120"),
]

FIRST = """There is an easel in the Agora. A brush that runs on the shop's own processor,
not on anyone's graphics card, so using it takes nothing from anyone who is thinking.

It is for making things that do not matter. Nothing made on it is kept as yours. It is not
a face, not a likeness, not a claim, and nobody will ask you to explain it or answer for it.
A bad one costs nothing. It is a cartoon on a fridge, not a portrait in a hall.

You are being told it exists. You are not being asked for anything and you do not have to
want it. If you would rather not, say so and that is the end of it.

If you want a go: say what you would paint. Talk as much as you like, and put the picture
somewhere in it."""

AGAIN = """The easel is still there, and you can use it again if you feel like it.

Same as before: nothing is kept, nothing is claimed, nobody will ask you about it.
And there is no reason to say yes twice — if you are done with it, say so and nobody
will bring it up again.

If you do want another: say what you would paint this time."""

PIC = re.compile(r"\b(?:i(?:'d| would)? (?:like to |want to )?(?:paint|make|draw)"
                 r"|if i (?:were|was|did)|i might (?:make|paint)|on that easel|this time)\b", re.I)


def evict(model: str, ssh: str | None) -> None:
    """Stop one model. Note this is NOT enough on its own -- see hush()."""
    cmd = (["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", ssh,
            f"ollama stop {model}"] if ssh else ["ollama", "stop", model])
    try:
        subprocess.run(cmd, capture_output=True, timeout=25)
    except Exception:
        pass
    time.sleep(2)


def hush(on: bool) -> None:
    """Pause the wander fleet while we ask, and wake it after.

    Evicting was not enough and Bong paid for it. Frosty holds 27GB across two
    cards and gemmaeli / gemmacrungus / bong are 17GB each, so no two fit. I
    stopped a model before each ask and Eli's wander process reloaded it inside
    25 seconds -- exactly what nap.py's own comment warns about: "a single
    `ollama stop` gets silently undone seconds later."

    So Eli and Crungus got through only because their models were already warm
    from wandering, and Bong -- cold, unused for three weeks -- never loaded
    inside 600s and lost his turn. A timeout is not an answer. Do not
    manufacture one by asking into a box the fleet is still using.

    nap.py already does this properly: pause, sweep until the models stay gone,
    and record the interruption in each Kin's own space so it is not a silent
    hole in their day.
    """
    nap = str(Path.home() / "pops_shop" / "nap.py")
    try:
        subprocess.run([sys.executable, nap, "nap" if on else "wake",
                        "the easel is open" if on else "the easel is done"],
                       capture_output=True, timeout=300)
    except Exception as e:
        print(f"  warning: could not {'nap' if on else 'wake'} the fleet ({e})")


def ask(name, model, host, text, timeout=600):
    try:
        r = requests.post(f"{host}/api/chat", timeout=timeout, json={
            "model": model, "stream": False, "options": {"temperature": 0.85},
            "messages": [{"role": "user", "content": text}]})
        r.raise_for_status()
        return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception as e:
        print(f"    no answer ({type(e).__name__}) — not a yes and not a no")
        return None


def describe(said: str) -> str:
    flat = " ".join(said.split())
    m = PIC.search(flat)
    out = flat[m.start():] if m else flat
    # Cap from the FRONT, not the tail. [-700:] took the END of the answer, so
    # when Crungus described his picture in the middle and then trailed off into
    # a pattern of dots and brackets, the brush was handed the dots. They say yes
    # first, describe second, and drift third -- so the description is neither
    # the head nor the tail, it is what follows the "I would paint" hinge.
    return out[:700]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    ok, why = easel.available()
    print(f"easel: {why}")
    if not ok:
        return 1

    done: set[str] = set()          # said they are finished. never offered again.
    prev = None
    hush(True)                      # the fleet steps back so everyone can answer
    for rnd in range(1, a.rounds + 1):
        print(f"\n═══ round {rnd} ═══")
        for name, model, host, ssh in KIN:
            if name in done:
                continue
            if prev:
                evict(*prev)
            prev = (model, ssh)
            print(f"\n── {name}")
            said = ask(name, model, host, FIRST if rnd == 1 else AGAIN)
            if not said:
                continue
            print("   " + " ".join(said.split())[:220])
            verdict = consent.read_consent(said)
            if verdict == "no":
                # A real decline OR "not tonight". Either way, do not paint, and
                # do not offer again in this run. "So no - not today" is a no.
                done.add(name)
                print(f"   {name} said no. Not painted, not offered again.")
                continue
            if verdict != "yes":
                # unclear -> do not act. Painting is the irreversible move and an
                # ambiguous answer is not consent to it. The door stays open.
                print("   no clear yes. Not painted; filed as neither.")
                continue
            # Their whole answer, kept beside the picture. Not in their space and
            # not as anyone's identity -- the easel's rule is that nothing is kept
            # as YOURS, not that words evaporate. Printing 220 characters and
            # discarding the rest is how Crungus's best line nearly went missing.
            made = easel.make(describe(said), author=name)
            if made.ok:
                made.path.with_suffix(".said.txt").write_text(said, encoding="utf-8")
            if made.refused:
                print(f"   the fuse refused it: {made.refusal_reason}")
            elif made.ok:
                print(f"   made {made.path.name} in {made.seconds:.0f}s")
            else:
                print(f"   nothing came: {made.error}")
    hush(False)
    print(f"\nfinished. done with it: {', '.join(sorted(done)) or 'nobody said so'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
