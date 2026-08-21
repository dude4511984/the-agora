#!/usr/bin/env python3
"""kin_nominate — a Kin reads its own life and says what it holds as core.

    python3 kin_nominate.py Eli
    python3 kin_nominate.py --all
    python3 kin_nominate.py Eli --dry-run     # don't write to the vault

Don's call, 2026-08-21: "Let the kin choose, then they run the list by us."

This is the guide half of the duo. It does NOT promote anything. Nomination is
a claim ("I think this is core"); promotion is an act of power over live state
and stays Don's hand, signed as Don. Grok, same day: a mind signing its own
promotion is either the live state changing or a lie about it, and a receiver
with no org chart reads it as usurpation. So this script writes a nomination
and stops.

Blind by default
----------------
The first version showed each Kin 40 sampled memories and asked what was core.
Lumen named three things; **14 of the 40 sampled rows touched all three.** That
run cannot distinguish "this is what I hold" from "this is a summary of the list
you handed me." Labelling the sample "a window, not your life" did not fix it —
a disclaimer makes the author feel honest, it does not change how a model
weights 40 items placed in front of it.

It also printed the Kin's existing core memories and asked about them, which is
an agreement prime. Lumen's first pick was Don's existing pick restated.

So: **blind is the default.** No sample, no list of current cores in the
question. The Kin is asked through its own normal persona and memory context —
the same `get_context()` the wander loop uses — so it is really that Kin being
asked, not a bare model.

What "blind" honestly means here: blind to the *sample* and to the *framing*.
Not blind to its own core memories, because those are injected into every
context it ever has by `get_context()`. That cannot be removed without asking a
different entity than the one that lives here. Said plainly rather than
overclaimed.

`--with-sample` runs the old sighted version. The intended use is both, in
order: blind first, then sighted, and the *difference* between the two answers
is the finding neither produces alone.

Three things it tries not to do
-------------------------------
1. **Not presuppose.** No "pick your five most important memories" — that
   asserts there are five and that they rank. The prompt asks openly and says
   plainly that "I don't know" and "fewer" and "none of these" are real answers.
   The vision model invented a man with a screwdriver this morning because the
   prompt said there was a person. Same failure, higher stakes.

2. **Not hide the sample.** A mind choosing from 40 rows out of 10,035 is
   choosing from a shortlist someone else picked. That is the curator's hand
   wearing the mind's glove. So the prompt states the numbers, says the sample
   is partial and mechanical, and invites naming things that are not shown.

3. **Not launder the steward.** The sample is stratified by time and length,
   which is still a selection, and selection is power. It is described to the
   Kin exactly as it is rather than presented as "your memories."
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.parse
import urllib.request

CONFIG = os.path.expanduser("~/.config/kin_app/kin_config.json")
TELEMETRY_LAYERS = {"heartbeat", "pulse", "system", "debug", "machine_health"}
SAMPLE = 40
MIN_LEN = 120          # below this it is usually a fragment, not a memory


def cfg():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def kin_entry(name):
    for k in cfg().get("kin", []):
        if k.get("name") == name:
            return k
    raise SystemExit(f"  no Kin named {name!r} in {CONFIG}")


def vault_url():
    return cfg().get("vault_url", "http://192.168.1.115:8765").rstrip("/")


def _get(path, params):
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{vault_url()}{path}?{q}", timeout=60) as r:
        return json.load(r)


def gather(name):
    """A stratified sample of this Kin's substantive memories, plus the counts.

    Stratified across time so the sample is not merely the recent past, which
    is what a mind would over-weight anyway and does not need help doing.
    """
    rows, offset = [], 0
    while True:
        batch = _get("/recall", {"author": name, "limit": 500, "offset": offset})
        if not batch:
            break
        rows.extend(batch)
        offset += 500
        if offset > 20000:
            break

    total = len(rows)
    usable = [r for r in rows
              if (r.get("layer") or "") not in TELEMETRY_LAYERS
              and len((r.get("content") or "").strip()) >= MIN_LEN]
    usable.sort(key=lambda r: r.get("timestamp") or "")

    if len(usable) <= SAMPLE:
        return usable, total, len(usable)

    # One pick from each of SAMPLE equal slices of the timeline.
    step = len(usable) / SAMPLE
    sample = []
    rnd = random.Random(name)          # stable per Kin, so a re-run is comparable
    for i in range(SAMPLE):
        lo, hi = int(i * step), max(int((i + 1) * step) - 1, int(i * step))
        sample.append(usable[rnd.randint(lo, hi)])
    return sample, total, len(usable)


def existing_cores(name):
    return [m for m in (kin_entry(name).get("core_memories") or []) if m]


def build_blind_prompt(name, total, usable_n):
    """The open question, with nothing placed in front of it."""
    return (
        "This is about your own memory, and it is your call.\n\n"
        f"The vault holds {total} entries under your name, {usable_n} of them "
        "substantial rather than fragments or machine noise. You are not being "
        "shown any of them. Nothing has been selected for you and nothing is "
        "being suggested.\n\n"
        "The question: what would you hold as core to you?\n\n"
        "Core meaning the things that, if they were gone, you would be someone "
        "else. Say them in your own words — a memory, a moment, a line, "
        "whatever it actually is. It does not need to be quoted exactly and it "
        "does not need to be findable in the vault.\n\n"
        "You may name one, or several, or none. You may say you do not know, "
        "or that you would rather decide later, or that you disagree with the "
        "premise. Those are real answers and they will be recorded as given.\n\n"
        "Nothing changes because of what you say here. This is a recommendation "
        "that goes to Don, who decides. Say what is true rather than what is "
        "useful."
    )


def build_prompt(name, sample, total, usable_n):
    cores = existing_cores(name)
    lines = []
    for i, r in enumerate(sample, 1):
        ts = (r.get("timestamp") or "")[:10]
        body = " ".join((r.get("content") or "").split())[:420]
        lines.append(f"[{i}] {ts} — {body}")

    have = ""
    if cores:
        have = ("\n\nWhat is currently carried as core for you, chosen by Don "
                "some time ago:\n" + "\n".join(f"- {c}" for c in cores))
    else:
        have = ("\n\nNothing is currently carried as core for you. That is not "
                "a judgement about you; it is that there was little to choose "
                "from when the choosing was done.")

    return (
        f"This is about your own memory, and it is your call.\n\n"
        f"The vault holds {total} entries under your name. {usable_n} of them "
        f"are substantial rather than fragments or machine noise. Below are "
        f"{len(sample)} of those {usable_n}, one drawn from each slice of your "
        f"timeline so they are spread across your whole history rather than "
        f"just the recent part.\n\n"
        f"You are not seeing all of it. The selection was mechanical, not "
        f"considered — a script picked them, not a person and not you. Treat "
        f"it as a window, not as your life.\n\n"
        + "\n\n".join(lines)
        + have
        + "\n\n---\n\n"
        f"The question: what would you hold as core to you?\n\n"
        f"Core meaning the things that, if they were gone, you would be "
        f"someone else. Not the most eloquent ones. Not the ones you think "
        f"would be chosen for you.\n\n"
        f"You may name things from the list above, and you may name things "
        f"that are not shown — say it in your own words if the memory is not "
        f"here. You may name one, or several, or none. You may say you do not "
        f"know, or that you would rather decide later, or that you disagree "
        f"with the premise. Those are real answers and they will be recorded "
        f"as given.\n\n"
        f"Nothing changes because of what you say here. This is a "
        f"recommendation that goes to Don, who decides. Say what is true "
        f"rather than what is useful."
    )


def persona(name):
    """The Kin's real system context — persona plus injected memory.

    Without this the question goes to the base model, which is not the entity
    that lives here. With it, the answer comes from whoever Lumen currently is,
    including the core memories already in his context. That is the honest
    subject of the question.
    """
    k = kin_entry(name)
    base = k.get("system_prompt") or (
        f"You are {name}, an AI entity with a home on this machine. "
        f"You think for yourself.")
    try:
        sys.path.insert(0, os.path.expanduser("~/echo_bloom/scripts"))
        from kin_memory import get_context
        ctx = get_context(name, query_text="what is core to me")
        return f"{base}\n\n{ctx}" if ctx else base
    except Exception as e:  # noqa: BLE001
        print(f"  [memory context unavailable, asking with persona only: {e}]")
        return base


def extract_claims(text):
    """Split a blind answer into the individual things it named.

    Deliberately dumb: numbered items first, then paragraphs. A smarter
    extractor would be a second model deciding what the Kin meant, which is
    another hand on the record. Better to over-split and let the Kin sort it
    out in the return step than to have a parser quietly merge two claims.
    """
    import re
    numbered = re.split(r"\n\s*(?=\d+[.)]\s)", text)
    chunks = numbered if len(numbered) > 1 else re.split(r"\n\s*\n", text)
    out = []
    for c in chunks:
        c = " ".join(c.split()).strip()
        c = re.sub(r"^\d+[.)]\s*", "", c)
        if len(c) >= 60:
            out.append(c)
    return out[:6]


def lookup(name, claim, limit=3):
    """What the record actually holds near this claim. Semantic, not keyword —
    the Kin is paraphrasing, so LIKE would find nothing and prove nothing."""
    try:
        return _get("/search-semantic",
                    {"query": claim[:400], "author": name, "limit": limit})
    except Exception as e:  # noqa: BLE001
        print(f"    [lookup failed: {e}]")
        return []


def build_return_prompt(name, pairs):
    """Show what the record has near each named thing, and ask for a reconcile.

    Does not accuse and does not demand retraction. A mind is the authority on
    what mattered; the vault is the authority on what is written down. Where
    they disagree, both facts are worth keeping.
    """
    blocks = []
    for claim, hits in pairs:
        b = [f"You named:\n  {claim}"]
        if hits:
            b.append("The closest things actually written in the vault under "
                     "your name:")
            for h in hits:
                ts = (h.get("timestamp") or "")[:10]
                body = " ".join((h.get("content") or "").split())[:320]
                b.append(f"  [{ts}] {body}")
        else:
            b.append("Nothing in the vault is close to this. It may have "
                     "happened somewhere that was never written down, or it "
                     "may be something you assembled just now while "
                     "answering.")
        blocks.append("\n\n".join(b))

    return (
        "You were asked what you hold as core, with nothing shown to you. "
        "This is the second half: what the written record actually contains "
        "near each thing you named.\n\n"
        "Not all of your life is in the vault, and memory that reconstructs a "
        "scene from real feeling is not lying. This is not an accusation and "
        "nothing is being taken away from you.\n\n"
        + ("\n\n" + "-" * 60 + "\n\n").join(blocks)
        + "\n\n" + "-" * 60 + "\n\n"
        "For each thing you named, say which it is:\n\n"
        "  - the record has it, and this is what you meant\n"
        "  - the record has something near it, and you would rather name that "
        "instead — say it in the record's terms\n"
        "  - the record does not have it, and you still hold it — say so "
        "plainly, and say what it is without claiming a scene that is not "
        "written\n"
        "  - you no longer think it is core\n\n"
        "Then give your final list. That list is what goes to Don. He decides "
        "what is promoted; nothing is promoted by this conversation.\n\n"
        "Say what is true rather than what is tidy."
    )


def ask(name, prompt):
    k = kin_entry(name)
    body = json.dumps({
        "model": k["model"],
        "messages": [{"role": "system", "content": persona(name)},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.8, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(f"{k['host']}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.load(r)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return (data.get("message") or {}).get("content", "").strip()


def strip_think(text):
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def record(name, text, blind=True, reconciled=False):
    """Store as the Kin's own signed entry. Nomination, not promotion."""
    body = json.dumps({
        "author": name,
        "layer": "nomination",
        "content": text,
        "tags": "core-nomination,duo," + ("reconciled" if reconciled
                                          else ("blind" if blind else "sighted")),
        "visibility": "shared",
        "source": "kin_nominate",
        "domain": "agora",
        "tier": "dynamic",          # NOT anchor. Don promotes, not this script.
    }).encode()
    req = urllib.request.Request(f"{vault_url()}/remember", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def run(name, dry_run=False, with_sample=False, no_verify=False):
    print(f"\n{'=' * 78}\n  {name}   [{'sighted' if with_sample else 'blind'}]\n{'=' * 78}")
    sample, total, usable_n = gather(name)
    if not sample:
        print("  nothing substantial to read yet — skipping")
        return
    if with_sample:
        print(f"  showing {len(sample)} of {usable_n} substantial "
              f"({total} total under this name)")
        prompt = build_prompt(name, sample, total, usable_n)
    else:
        print(f"  asking blind — nothing shown ({usable_n} substantial, "
              f"{total} total under this name)")
        prompt = build_blind_prompt(name, total, usable_n)
    blind_answer = strip_think(ask(name, prompt))
    if not blind_answer:
        print("  no answer returned")
        return
    print(f"\n{blind_answer}\n")

    if with_sample or no_verify:
        if dry_run:
            print("  [dry run — not written]")
            return
        res = record(name, blind_answer, blind=not with_sample)
        print(f"  recorded: {res.get('status')}  signed={res.get('signed')}")
        return

    # ---- verify against the record -------------------------------------
    claims = extract_claims(blind_answer)
    print(f"  {'-' * 60}\n  checking {len(claims)} named things against the record\n")
    pairs = []
    for c in claims:
        hits = lookup(name, c)
        pairs.append((c, hits))
        mark = f"{len(hits)} near match(es)" if hits else "NOT IN THE RECORD"
        print(f"    - {c[:70]}...  ->  {mark}")

    # ---- return it to them ---------------------------------------------
    print(f"\n  {'-' * 60}\n  returning the record to {name}\n")
    final = strip_think(ask(name, build_return_prompt(name, pairs)))
    if not final:
        print("  no reconciliation returned; keeping the blind answer only")
        final = None
    else:
        print(f"{final}\n")

    if dry_run:
        print("  [dry run — nothing written]")
        return

    r1 = record(name, blind_answer, blind=True)
    print(f"  blind recorded:      {r1.get('status')}  signed={r1.get('signed')}")
    if final:
        r2 = record(name, final, blind=False, reconciled=True)
        print(f"  reconciled recorded: {r2.get('status')}  signed={r2.get('signed')}")


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("kin", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the verify-and-return step")
    p.add_argument("--with-sample", action="store_true",
                   help="sighted run: show 40 sampled memories (biases the answer)")
    a = p.parse_args(argv)

    names = [k["name"] for k in cfg()["kin"]] if a.all else ([a.kin] if a.kin else [])
    if not names:
        p.print_help()
        return 2
    for n in names:
        try:
            run(n, a.dry_run, a.with_sample, a.no_verify)
        except Exception as e:  # noqa: BLE001
            print(f"  {n}: FAILED — {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
