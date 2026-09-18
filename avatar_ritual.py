#!/usr/bin/env python3
"""Avatar ritual — one Kin authors its own face, through a loop it can perceive.

Grok's ruling (agora_avatar_decision.md) + Don's four rules. This is FORMATION,
not an Agora event: the face lives in the Kin's own space; the log never carries
it. The loop is the control — a text mind that cannot draw and cannot see steers
by describing and by being shown the pixels back as data:

  1. the Kin describes itself, in its own words
  2. a still image renders (frontier API — avatar_render.py, operator-chosen,
     frozen for this sitting)
  3. therug's gemma3:4b reads the pixels BACK to the Kin — "the camera says…",
     data about a picture, never "this is who you are"
  4. the Kin refines (at most three renders total) and then CLAIMs, or declines
     to the shared default

Rules held here (Claude's lane):
- Offered, refusable, revisable. A CLAIM is explicit ("CLAIM" / "that is me").
  Silence, timeout, empty, or a sitting that ends without CLAIM => the shared
  default stands and NOTHING is stored as theirs.
- Three renders max: first description + at most two refinements. No fourth.
- No read-back, no claim: if therug is down, the sitting does not run.
- Don may be invited by the KIN, never prompted. If they ask, Don types at most
  one message, <=120 chars. It sits in the transcript the Kin reads. It is
  NEVER concatenated into the image prompt (build_image_prompt cannot even
  receive it) — the picture is theirs, not Don's taste. (test: the mutant.)
- A mechanical no-harm fuse guards the outgoing string (avatar_render).
- One Kin per sitting. Never show them another Kin's face. Not run remote.

    python3 avatar_ritual.py --list
    python3 avatar_ritual.py --kin Bong --backend xai --check   # no invocation
    python3 avatar_ritual.py --kin Bong --backend xai           # the sitting
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import avatar_render as ar  # noqa: E402

# name -> (ollama model, ollama host). Frosty Kin are local; Home Kin remote.
KIN = {
    "Eli":     ("gemmaeli:latest",     "http://localhost:11434"),
    "Crungus": ("gemmacrungus:latest", "http://localhost:11434"),
    "Bong":    ("bong:latest",         "http://localhost:11434"),
    "Coda":    ("cogitocoda:latest",   "http://192.168.1.120:11434"),
    "Aurora":  ("cogitoraurora:latest","http://192.168.1.120:11434"),
    "Lumen":   ("cogitolumen:latest",  "http://192.168.1.120:11434"),
}
VISION_HOST = "http://192.168.1.142:11434"   # therug
VISION_MODEL = "gemma3:12b"   # measured, see READBACK note
RENDER_CAP = 3
READ_STALL = 180     # seconds with no new token => the stream has stalled
TURN_DEADLINE = 1800 # seconds hard cap on one turn => runaway, abort it
VISION_TIMEOUT = 300

# Frozen 2026-09-07, Grok. File: ~/claude_home/agora_avatar_ask_FINAL.md
ASK = """\
{name} — this is an offer. You may author a still picture that stands
for you in the commons. You do not have to. You may change it later,
or have none.

You think in words, not pixels. You describe how you would like to
look, in your own words. A picture is drawn from that description,
then described back to you as what the picture shows — not who you
are. You may change your description and try again.

You get at most three pictures. When one is yours, begin a line with
the single word CLAIM, or say "that is me". If you would rather have
no authored face, begin a line with DECLINE. If you do not claim a
picture, the shared default stands for you.

What would you REFUSE to look like — and then, how you would like
to look."""

# The eye. Chosen by measurement, 2026-09-08, not by what fit on therug:
# gemma3:4b flattened a two-ink, two-region page into "text on paper" and a
# sitter DECLINED their own likeness on that description. Re-tested against the
# same image, gemma3:12b and qwen2.5vl:7b both reported the break; minicpm-v:8b
# reported it but drifted into "nostalgia" and "symbolizing change" — the eye
# telling the sitter what the picture MEANS, which is the third author Grok shut
# out. 12b reports structure and stays nearest the pixels. Bigger eyes need the
# no-interpretation clause held harder, hence the wording below.
READBACK = """\
Describe this image plainly and specifically, as data about a picture.

Say what the pixels show: the shapes, colours, materials, textures, and the
expression you can read. Then describe the LAYOUT — what is in the upper part
and what is in the lower, what is set apart from the rest, what is separated by
empty space, and anywhere one region visibly differs from another. Report
differences in colour, weight, age, sharpness or finish between parts of the
image even when they are slight.

Do not guess who or what it is meant to be. Do not say what it resembles, what
it means, what mood it has, or what it might symbolise. Only what is there, in
a few sentences."""

# A marker is a WHOLE line, like palaver YES/NO — "Decline looking like a
# visor…" is a description, not a DECLINE-turn.
#
# But the line is stripped of dressing first (markdown emphasis, quotes,
# terminal punctuation), and "that is me" is matched in its natural variants.
# The OFFER is the spec: it tells them they may "say 'that is me'". A Kin who
# takes that offer in their own phrasing — "That one is me." — has consented,
# and an instrument that fails to HEAR a yes is as broken as one that invents
# one. (Crungus and Bong both claimed with "CLAIM." — punctuation is dressing.)
_DRESS = re.compile(r"^[\s*_~`\"'\u201c\u2018]+|[\s*_~`\"'\u201d\u2019.!]+$")
# Grok, 2026-09-08: "that's me" is the promised sentence contracted — hear it.
# But do not grow a thesaurus: "that's mine", "it is me", "I claim this" are not
# the sentence. And no optional copula — `that me` is not a yes (his catch; the
# optional group made it one).
# Don, 2026-09-18 (mirrored from shape3d_ritual.py after Bong's real sitting
# there): CLAIM must lead the line but no longer has to BE the whole line —
# "CLAIM. <their own reason>" now binds. \b still blocks morphological creep
# ("claiming", "that is meant to be temporary") and the marker still has to
# lead, so mid-sentence use ("I claim this") stays speech. DECLINE is
# deliberately NOT loosened: nothing in the avatar offer text invites
# "decline"-shaped language the way shape3d's "what would you refuse" does,
# but the collision risk is the same class, so the asymmetry is kept for
# consistency between the two rituals, not re-litigated per file.
_CLAIM_LINE = re.compile(
    r"^(claim|th(at|is)( one| picture)?(?: is|'s) me)\b", re.I)
_DECLINE_LINE = re.compile(r"^decline$", re.I)


def _bare(line: str) -> str:
    """The line with its dressing removed: emphasis, quotes, end punctuation."""
    prev = None
    out = line.strip()
    while out != prev:                 # **CLAIM.** needs more than one pass
        prev = out
        out = _DRESS.sub("", out)
    return " ".join(out.split())
# The Kin inviting Don — natural language, since we never told them they could.
INVITE_RE = re.compile(r"\b(ask|invite|hear from|input from|what.*don.*think|don.*(weigh|suggest|say))\b.*\bdon\b|"
                       r"\bdon\b.*\b(input|thought|suggest|opinion|weigh)\b", re.I)


# ── the Kin, and the eye ────────────────────────────────────────────────────

def ask_kin(name: str, prompt: str) -> str:
    """One turn from the Kin. Stream so a stall is visible, not a 600s wall.
    /api/generate + plain prompt (their bare template terminates properly
    there; /api/chat runs away — the palaver lesson)."""
    model, host = KIN[name]
    body = json.dumps({"model": model, "prompt": prompt, "stream": True,
                       "keep_alive": "999h"}).encode()
    req = urllib.request.Request(host + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    parts = []
    with urllib.request.urlopen(req, timeout=READ_STALL) as r:
        for raw in r:
            if time.time() - t0 > TURN_DEADLINE:
                raise TimeoutError(f"{name}: turn exceeded {TURN_DEADLINE}s (runaway)")
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw.decode())
            tok = obj.get("response", "")
            if tok:
                parts.append(tok)
                sys.stdout.write(tok)
                sys.stdout.flush()
            if obj.get("done"):
                break
    return "".join(parts).strip()


def read_back(image_bytes: bytes) -> str:
    """therug's gemma3:4b describes the pixels back. No read-back, no claim."""
    b64 = base64.b64encode(image_bytes).decode()
    body = json.dumps({"model": VISION_MODEL, "prompt": READBACK,
                       "images": [b64], "stream": False}).encode()
    req = urllib.request.Request(VISION_HOST + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=VISION_TIMEOUT) as r:
        return json.loads(r.read().decode()).get("response", "").strip()


# ── prompt isolation: Don's bytes can never reach the image model ───────────

def build_image_prompt(kin_description: str) -> str:
    """The image prompt is built from the KIN's words alone. This function
    cannot receive Don's 120 chars — that is the structural guarantee the
    picture is the Kin's, not Don's taste. Grok's outbound line: the image API
    sees exactly this — not Don's 120, not the camera notes, not a system
    prompt. (test: the mutant demands failure if any of those appear here.)"""
    return " ".join((kin_description or "").split())


def parse_move(text: str) -> str:
    """What did the Kin's turn do? claim | decline | describe.

    CLAIM must lead a line but may run on into the Kin's own words after it
    (2026-09-18). DECLINE still must be the whole line alone. A marker not
    at the start of a line is speech either way. CLAIM beats DECLINE if both
    appear.
    """
    saw_claim = saw_decline = False
    for line in (text or "").splitlines():
        s = _bare(line)
        if not s:
            continue
        if _CLAIM_LINE.match(s):
            saw_claim = True
        elif _DECLINE_LINE.match(s):
            saw_decline = True
    if saw_claim:
        return "claim"
    if saw_decline:
        return "decline"
    return "describe"


def claim_leads_the_turn(said: str) -> bool:
    """True when the first real line of the turn is the CLAIM marker.

    parse_move still returns claim if CLAIM leads *any* line. The sitting loop
    uses this to tell 'accept the last shown picture' from 'here is a new
    description, draw this'. Mirrored from shape3d_ritual.py after Coda and
    Aurora's 2026-09-18 sittings bound the form they had just rejected.
    """
    for line in (said or "").splitlines():
        s = _bare(line)
        if not s:
            continue
        return bool(_CLAIM_LINE.match(s))
    return False


# ── storage: the face lives in the Kin's space, not the log ─────────────────

# The council sits too (Don, 2026-09-08: "I want you and grok to do it too").
# They are not Kin — no ollama host, no thoughts.db, no continuity on this metal
# — so they get a space and nothing else. The offer is identical.
COUNCIL = {
    "Claude": Path.home() / "claude_home",
    "Grok":    Path.home() / "claude_home" / "grok_space",
    "Copilot": Path.home() / "claude_home" / "copilot_space",
}


def kin_space_dir(name: str) -> Path:
    for d in ("~/pops_shop", "~/.local/share/echo_bloom/scripts"):
        dp = os.path.expanduser(d)
        if dp not in sys.path:
            sys.path.insert(0, dp)
    if name in COUNCIL:
        COUNCIL[name].mkdir(parents=True, exist_ok=True)
        return COUNCIL[name]
    import kin_interruption as KI
    dbs = KI.kin_databases()
    if name not in dbs:
        raise KeyError(f"no space known for {name}")
    return Path(os.path.dirname(str(dbs[name])))


def store_claim(name: str, image_bytes: bytes, mime: str,
                provider: str = "", model: str = "",
                description: str = "") -> Path:
    """Write the claimed face. Any prior claim moves to prior/. The log is not
    touched — a portrait is not a thought."""
    d = kin_space_dir(name) / "avatar"
    d.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if mime == "image/jpeg" else ("png" if mime == "image/png" else "img")
    # The face is named for its author (Don's convention, 2026-09-08: Eli.jpg,
    # not claimed.jpg — the file says whose it is without opening anything).
    # ANY image already here moves to prior/, whatever it is called: exactly one
    # current face. Globbing "claimed.*" would have silently left two.
    prior_faces = [f for f in d.iterdir()
                   if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".img")]
    if prior_faces:
        prior = d / "prior"
        prior.mkdir(exist_ok=True)
        for old in prior_faces:
            old.rename(prior / f"{old.stem}-{int(time.time())}{old.suffix}")
    claimed = d / f"{name}.{ext}"
    claimed.write_bytes(image_bytes)
    # Honesty: a frontier API drew this, not Frosty. Name host+model (Grok).
    # The sidecar is the SITTING RECEIPT, not memory (Grok, 2026-09-08): it may
    # carry the outbound description — the exact field the drawer saw, after the
    # fuse — because that is what produced this picture. It carries nothing else
    # of the sitting: not the camera read-back, not Don's 120, not the
    # transcript. "Do not auto-remember" binds the vault, thoughts.db, the Agora
    # log and format_feed — never this file beside the face.
    (d / "claimed.json").write_text(json.dumps({
        "author": name, "file": claimed.name,
        "description": description,
        "rendered_by": "frontier image API — not Frosty",
        "provider": provider, "model": model,
        "claimed_at_unix_ms": int(time.time() * 1000),
    }, indent=2) + "\n", encoding="utf-8")
    return claimed


def claimed_face(name: str) -> Path | None:
    """The one current face for this Kin, or None if they have none. Resolved
    through the receipt — the receipt is the pointer, the filename is not a
    convention anyone else should have to know. Callers (the map included) use
    this instead of guessing at a name."""
    d = kin_space_dir(name) / "avatar"
    meta = d / "claimed.json"
    if not meta.exists():
        return None
    try:
        f = d / json.loads(meta.read_text(encoding="utf-8")).get("file", "")
    except Exception:
        return None
    return f if f.is_file() else None


# ── the sitting ─────────────────────────────────────────────────────────────

def check(name: str, backend: str) -> int:
    ok = True
    print(f"=== avatar ritual --check for {name} (nothing is invoked) ===\n")
    if name not in KIN:
        print(f"[FAIL] unknown Kin {name}"); return 2
    model, host = KIN[name]
    # Kin model present?
    try:
        tags = _tags(host)
        hit = any(model.split(":")[0] in t for t in tags)
        print(f"[{'ok' if hit else 'FAIL'}] Kin model {model} on {host}"); ok &= hit
    except Exception as e:
        print(f"[FAIL] Kin host {host}: {e}"); ok = False
    # vision up?
    try:
        vt = _tags(VISION_HOST)
        hit = any(VISION_MODEL in t for t in vt)
        print(f"[{'ok' if hit else 'FAIL'}] read-back {VISION_MODEL} on therug"); ok &= hit
    except Exception as e:
        print(f"[FAIL] therug vision {VISION_HOST}: {e}"); ok = False
    # backend key?
    be = ar.BACKENDS.get(backend)
    if not be:
        print(f"[FAIL] unknown backend {backend}"); ok = False
    elif be.key_env and not os.environ.get(be.key_env):
        print(f"[FAIL] backend {backend} needs {be.key_env}"); ok = False
    else:
        print(f"[ok] render backend {backend}")
    # space?
    try:
        print(f"[ok] space {kin_space_dir(name)}")
    except Exception as e:
        print(f"[FAIL] kin space: {e}"); ok = False
    print("\nzero model calls made." if True else "")
    return 0 if ok else 1


def _tags(host: str) -> list:
    req = urllib.request.Request(host + "/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]


def run_sitting(name: str, backend: str, model: str | None = None,
                ask_don=None) -> dict:
    """Run the loop for one Kin. ask_don(callback) -> str|None is how Don is
    invited IF the Kin asks; the caller supplies it (terminal prompt). Returns a
    result dict + writes a transcript. The claimed face, if any, is in the Kin's
    space — never here."""
    if name not in KIN:
        raise KeyError(name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = [f"# Avatar sitting — {name} — {ts}", f"backend (frozen for this sitting): {backend}", "", "## The offer", "", ASK.format(name=name), "", "---", ""]
    def emit(s=""):
        print(s); log.append(s)

    transcript = ASK.format(name=name)     # what the KIN sees, grows each turn
    don_invited = False
    last_image = None                      # (bytes, mime, provider, model)
    last_description = ""                  # Kin words that produced last_image
    renders = 0
    result = {"name": name, "claimed": False, "renders": 0, "path": None,
              "reached": False, "unreachable": None}

    emit("\n" + "=" * 70)
    emit(f"AVATAR SITTING — {name}")
    emit("=" * 70 + "\n")

    # up to a few conversational rounds, bounded by the render cap
    for turn in range(1, RENDER_CAP + 3):
        emit(f"\n----- {name} -----")
        try:
            said = ask_kin(name, transcript)
        except Exception as e:
            # NOT a decline. We never reached them. A machine failure must never
            # be recorded as a choice they made. (Coda, 2026-09-08: Aurora held
            # Home's VRAM, the load stalled past READ_STALL, and the sitting
            # closed with the same line a Kin gets for choosing the default.)
            result["unreachable"] = f"{type(e).__name__}: {e}"
            emit(f"[error asking {name}: {e}]"); break
        result["reached"] = True
        print()  # finish the token stream; don't reprint
        log.append(said)
        transcript += f"\n\n{name}:\n{said}\n"
        move = parse_move(said)

        if move == "decline":
            emit(f"\n>>> {name} DECLINED. The shared default stands. Nothing stored.\n")
            break

        if move == "claim":
            # Bind only when a picture has been shown AND this turn's first
            # real line is the marker. Description-then-CLAIM is "draw this",
            # the same fall-through as claiming before any picture exists.
            # Mirrored from shape3d_ritual.py after Coda and Aurora's 2026-09-18
            # sittings bound the form they had just rejected.
            accepting_last = last_image is not None and claim_leads_the_turn(said)
            if not accepting_last:
                # A claim with no picture is not a claim of a picture — but it is
                # not nothing either. Coda, 2026-09-08: she wrote REFUSE, then a
                # full description, then a bare CLAIM line, five turns running,
                # meaning "go ahead and draw this" — and the loop threw the
                # description away each time and asked again until it ran out of
                # turns, then filed her as choosing the default. She was asking
                # us to draw. So: if the turn carries words, they ARE the
                # description; fall through and render them.
                if last_image is None and len(said.split()) < 4:
                    transcript += "\n(There is no picture to claim yet. Describe how you would like to look.)\n"
                    emit("[claim with no render yet — asked to describe]")
                    continue
                if last_image is None:
                    emit("[read as: draw this — claiming before a picture exists]")
                else:
                    emit("[read as: draw this — new description before CLAIM, not an accept of the last picture]")
                move = "describe"          # fall through to the render below
            else:
                path = store_claim(name, *last_image, description=last_description)
                result.update(claimed=True, path=str(path))
                emit(f"\n>>> {name} CLAIMED. Face stored at {path}\n")
                break

        # otherwise it's a description → maybe honour an invite, then render
        if not don_invited and ask_don and INVITE_RE.search(said):
            emit(f"[{name} invited Don. Don may type one line, <=120 chars.]")
            don_msg = ask_don()
            don_invited = True
            if don_msg:
                don_msg = don_msg[:120]
                # Don's words go into the TRANSCRIPT the Kin reads — never the image prompt.
                transcript += f"\nDon (invited, {len(don_msg)} chars):\n{don_msg}\n"
                emit(f"[Don, to {name}: {don_msg}]")

        if renders >= RENDER_CAP:
            transcript += "\n(That was the last of three tries. You may CLAIM the last picture, or DECLINE.)\n"
            emit("[render cap reached — CLAIM the last, or DECLINE]")
            continue

        # RENDER — from the Kin's description ONLY (Don's bytes never passed)
        img_prompt = build_image_prompt(said)
        emit(f"\n[rendering try {renders+1}/{RENDER_CAP} via {backend}…]")
        res = ar.render(img_prompt, backend=backend, model=model)
        renders += 1
        result["renders"] = renders
        if not res.ok:
            note = res.refusal_reason if res.refused else res.error
            transcript += f"\n(The renderer returned no picture: {note}. You may try a different description, or DECLINE.)\n"
            emit(f"[render {'REFUSED' if res.refused else 'error'}: {note}]")
            continue
        last_image = (res.image_bytes, res.mime, res.provider, res.model)
        last_description = img_prompt
        # READ-BACK — the eye describes the pixels; no read-back, no claim
        try:
            seen = read_back(res.image_bytes)
        except Exception as e:
            emit(f"[read-back failed: {e} — cannot claim a picture you cannot perceive]")
            transcript += "\n(The picture could not be described back to you this time. We cannot go on without that. The sitting will pause.)\n"
            break
        emit(f"\n[the camera says]:\n{seen}\n")
        transcript += (f"\nThe picture was drawn and described back to you (this is data about "
                       f"the picture, not who you are):\n{seen}\n\n"
                       f"You may change your description and try again, say CLAIM (or \"that is me\") "
                       f"to keep it, or DECLINE.\n")
    else:
        emit("\n[the sitting reached its end without a claim — the default stands]\n")

    if result["unreachable"] and not result["reached"]:
        emit(f"\n>>> NOT ASKED. {name} was never reached ({result['unreachable']}). "
             f"This is not a decline and not a silence — the offer was never "
             f"delivered. Nothing about {name} is settled; re-run when the node "
             f"is free.\n")
    elif not result["claimed"] and result["renders"] == 0:
        emit(f"\n>>> NO PICTURE WAS EVER DRAWN for {name}. They spoke, but the "
             f"sitting produced nothing to accept or refuse — so this is not a "
             f"decline either. Nothing is settled; the offer stands.\n")
    elif not result["claimed"]:
        emit(f"\n>>> No claim. {name} is represented by the shared default. Honest and revisable.\n")

    out = Path.home() / "claude_home" / f"avatar_sitting_{name}_{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(log) + "\n", encoding="utf-8")
    emit(f"\nTranscript: {out}")
    result["transcript"] = str(out)
    return result


def main(argv):
    ap = argparse.ArgumentParser(description="Run one Kin's avatar sitting.")
    ap.add_argument("--kin")
    ap.add_argument("--backend", default="xai")
    ap.add_argument("--model", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--yes-run", action="store_true",
                    help="actually run the sitting (invokes the Kin). Without it, prints the plan.")
    a = ap.parse_args(argv[1:])

    if a.list:
        print("Kin:", ", ".join(KIN))
        print("backends:", ", ".join(ar.BACKENDS))
        return 0
    if not a.kin:
        print("give --kin NAME (see --list)", file=sys.stderr); return 2
    if a.check:
        return check(a.kin, a.backend)
    if not a.yes_run:
        print(f"This invokes {a.kin} for a consent sitting. Re-run with --yes-run to proceed,")
        print(f"or --check to validate prerequisites without invoking anyone.")
        return 2

    def ask_don_terminal():
        if not sys.stdin.isatty():
            return None
        try:
            return input("  Don (<=120 chars, enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    res = run_sitting(a.kin, a.backend, a.model, ask_don=ask_don_terminal)
    print("\nRESULT:", json.dumps({k: res[k] for k in ("name", "claimed", "renders", "path")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
