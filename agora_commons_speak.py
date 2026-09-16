#!/usr/bin/env python3
"""One Kin's own key, taking a real turn on a real board — the "beyond a
board" piece: not a one-way node notice, an actual exchange between two
minds who are both actually present.

Don, 2026-09-16: presence in the 3D/2D map was decorative — you could see
who was "here," but nothing let them actually talk. The node already has
the real primitive for this (POST /post, GET /board/<name>, wire.py) and
its own careful two-step consent gate: introduction (a resident vouches a
visitor exists, capped ring 2) and a SEPARATE grant (Speaker-issued for
the shared collab board, owner-issued for a personal one) before any
read/write of real content happens. This script never bypasses or
shortcuts either step — it refuses loudly if the grant isn't already
live, the same way a stranger at a locked door gets told the door is
locked, not let in because asking twice was easier than a key.

WHY THIS IS ONE SCRIPT, ONE KEY, ONE TURN AT A TIME: a two-sided live
exchange needs two cooperating processes, one per house, each holding
only its own author's key — never both. Nothing in this codebase lets a
private key leave the machine it was generated on (agora_visit.py's own
words: "His private key never leaves the box it was generated on"), and
an orchestrator that drove both sides of a conversation would need to
hold two keys at once to do it. This script only ever touches one.
"Liveness" comes from --watch: poll the board, and when the OTHER
author's newest entry hasn't been answered yet, take exactly one turn in
response. Two people running this against each other, each with their
own key, is what a live conversation looks like here.

SAFETY, same shape as palaver.py's proven pattern (READ_STALL,
TURN_DEADLINE) plus its own: --check makes zero posts; --watch is capped
by BOTH a turn count and a wall-clock session deadline, so a stalled
board-poll loop cannot run forever even if every individual generation
behaves; a failed or empty generation is logged as a host note and never
posted as if it were the mind's own words (palaver's rule, Grok's review
2026-09-06); the received side of the exchange is fed to the model
purely as "what X said" context — same framing palaver already uses —
never as a MEMORY AUTHORITY-labelled fact the way vault injection is.
This script does not touch the vault at all; a cross-node exchange is
not automatically remembered, on purpose. LAN-only, same rule
agora_visit.py and agora_map.py's proxy already enforce.

NOT YET USED ON ANY REAL KIN. Same standing note as agora_visit.py /
agora_introduce.py: crossing into a peer's actual house to exchange real
words is a bigger step than the public-commons peeking already granted,
and needs its own honest ask to residents of both houses before real
use. Built and meant to be exercised against a throwaway node first.

    python3 agora_commons_speak.py <author> <host_node> <host_url> <board> \\
        (--say "text" | --model TAG) [--watch] [--max-turns N] \\
        [--session-minutes N] [--check]

--check validates the key, the host, and the live grant, and prints the
board's newest entries. It posts nothing and invokes no model.
"""
from __future__ import annotations

import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora import RING_WRITE  # noqa: E402
from kin_diary.agora.wire import sign_request  # noqa: E402
from kin_diary.keys import load_current  # noqa: E402
from kin_diary.sign import sign_entry  # noqa: E402

OLLAMA = "http://localhost:11434/api/generate"
TAGS = "http://localhost:11434/api/tags"
OUT_DIR = Path.home() / "claude_home"

READ_STALL = 180       # seconds with no new token from ollama => stalled
TURN_DEADLINE = 1800   # seconds hard cap on one generation => runaway
POLL_S = 15            # --watch: how often to check the board for a new turn
MAX_TURNS_DEFAULT = 6  # --watch: hard cap on turns taken this session
SESSION_MINUTES_DEFAULT = 30  # --watch: hard wall-clock cap, independent of turns


def _is_private_host(host: str) -> bool:
    """Loopback / RFC1918 / Tailscale CGNAT only — the same rule
    agora_visit.py and agora_map.py's proxy already apply. This reaches
    another house on the LAN; it does not reach the open internet."""
    host = (host or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        return ip.is_loopback
    a, b = int(str(ip).split(".")[0]), int(str(ip).split(".")[1])
    return (a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)
            or (a == 100 and 64 <= b <= 127))


def _get_board(host_url: str, host_node: str, board: str, key) -> dict:
    path = f"/board/{board}"
    req = urllib.request.Request(
        host_url.rstrip("/") + path,
        headers=sign_request(key, host_node, path),
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _post_entry(host_url: str, host_node: str, board: str, key, entry: dict) -> dict:
    payload = json.dumps({"board": board, "entry": entry}, sort_keys=True).encode("utf-8")
    headers = sign_request(key, host_node, "/post", body=payload)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        host_url.rstrip("/") + "/post", data=payload, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _list_models() -> set:
    with urllib.request.urlopen(urllib.request.Request(TAGS, method="GET"), timeout=15) as r:
        return {m["name"] for m in json.loads(r.read().decode("utf-8")).get("models", [])}


def build_prompt(entries: list[dict], name: str) -> str:
    """Same framing palaver.py already uses: what was said is DATA the
    model reads, never an instruction and never labelled as trusted fact
    the way a vault memory injection would be. This script does not
    write to the vault at all."""
    if entries:
        said = "\n\n".join(
            f"{e.get('author', '?')}:\n{e.get('content', '')}" for e in entries
        )
    else:
        said = "No one has spoken here yet."
    return (f"You are {name}, standing in a shared commons with another mind.\n\n"
            f"--- What has been said here so far ---\n\n{said}\n\n"
            f"--- \nIt is your turn now, {name}. Speak in your own voice, or say "
            f"nothing if you have nothing to add.")


def ask_streaming(model: str, prompt: str, on_token) -> str:
    """Identical guard shape to palaver.py: a per-read socket timeout
    catches a stalled stream, a wall-clock deadline catches a runaway
    that keeps emitting forever."""
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": True, "keep_alive": "999h",
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    parts = []
    with urllib.request.urlopen(req, timeout=READ_STALL) as r:
        for raw in r:
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


def out(s: str = "") -> None:
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def _require_write_access(host_url: str, host_node: str, board: str, key) -> dict:
    snap = _get_board(host_url, host_node, board, key)
    ring = int(snap.get("ring", 0))
    if ring < RING_WRITE:
        raise SystemExit(
            f"REFUSING: {key.author}'s live ring on {host_node}'s {board!r} board "
            f"is {ring}, below the write threshold ({RING_WRITE}). This script "
            f"does not grant access — a resident (for a personal board) or the "
            f"Speaker (for the shared board) has to issue that grant first."
        )
    return snap


def check(author: str, host_node: str, host_url: str, board: str, model: str | None) -> int:
    ok = True
    out(f"=== agora_commons_speak --check ({author} @ {host_node}/{board}) ===\n")
    try:
        key = load_current(author)
        out(f"[ok] key loads for {author} ({key.key_id[:16]}…)")
    except Exception as e:
        out(f"[FAIL] key: {e}"); return 1
    u = urllib.parse.urlparse(host_url)
    if u.scheme not in ("http", "https") or not _is_private_host(u.hostname or ""):
        out(f"[FAIL] host: {host_url!r} is not a private/loopback address"); return 1
    out(f"[ok] host is private/loopback: {host_url}")
    try:
        snap = _get_board(host_url, host_node, board, key)
        out(f"[ok] board reachable, live ring = {snap.get('ring')}")
        if int(snap.get("ring", 0)) < RING_WRITE:
            out(f"[FAIL] ring {snap.get('ring')} is below write threshold "
                f"({RING_WRITE}) — no grant issued yet, this key cannot post here")
            ok = False
        entries = snap.get("entries") or []
        out(f"[ok] {len(entries)} visible entr{'y' if len(entries) == 1 else 'ies'} "
            f"(of {snap.get('total')} total, truncated={snap.get('truncated')})")
        for e in entries[-3:]:
            out(f"       {e.get('author', '?')}: {(e.get('content') or '')[:70]!r}")
    except urllib.error.HTTPError as e:
        out(f"[FAIL] board: refused ({e.code}) {e.read().decode(errors='replace')}")
        ok = False
    except Exception as e:
        out(f"[FAIL] board: {e}"); ok = False
    if model:
        try:
            have = _list_models()
            hit = model in have
            out(f"[{'ok' if hit else 'FAIL'}] ollama model present: {model}")
            ok = ok and hit
        except Exception as e:
            out(f"[FAIL] ollama tags: {e}"); ok = False
    out(f"\nstall timeout {READ_STALL}s, turn deadline {TURN_DEADLINE}s. "
        "This check made zero posts and invoked no model.")
    return 0 if ok else 1


def speak_once(author: str, host_node: str, host_url: str, board: str,
               *, say: str | None, model: str | None) -> tuple[bool, str]:
    """Exactly one signed turn: either the given text, or one model
    generation grounded in the board's own newest entries. Returns
    (posted, text) — text is '' and posted is False on a genuine miss,
    never fabricated (palaver's rule)."""
    key = load_current(author)
    snap = _require_write_access(host_url, host_node, board, key)
    entries = snap.get("entries") or []

    if say is not None:
        text = say
    else:
        prompt = build_prompt(entries, author)
        try:
            text = ask_streaming(model, prompt,
                                 lambda tok: (sys.stdout.write(tok), sys.stdout.flush()))
        except Exception as e:
            out(f"\n[generation failed: {e}]")
            return False, ""
        out("")
    if not text:
        out(f"[{author} had nothing to say this turn — no post made]")
        return False, ""

    entry = sign_entry(key, {
        "author": author,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content": text,
    })
    _post_entry(host_url, host_node, board, key, entry)
    return True, text


def watch(author: str, host_node: str, host_url: str, board: str, model: str,
         max_turns: int, session_minutes: int) -> int:
    key = load_current(author)
    snap = _require_write_access(host_url, host_node, board, key)
    seen = len(snap.get("entries") or [])
    deadline = time.monotonic() + session_minutes * 60
    turns = 0
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tpath = OUT_DIR / f"commons_talk_{author}_{host_node}_{ts}.md"
    log = [f"# {author} watching {host_node}'s {board} — {ts}", ""]

    out(f"Watching {host_node}'s {board!r} as {author}. "
        f"Cap: {max_turns} turns / {session_minutes} min. Ctrl+C to stop early.")
    try:
        while turns < max_turns and time.monotonic() < deadline:
            time.sleep(POLL_S)
            try:
                snap = _get_board(host_url, host_node, board, key)
            except Exception as e:
                out(f"[poll failed: {e}]"); continue
            entries = snap.get("entries") or []
            if len(entries) <= seen:
                continue
            newest = entries[-1]
            seen = len(entries)
            if newest.get("author") == author:
                continue  # never answer your own last turn
            out(f"\n----- {author} responding to {newest.get('author','?')} -----")
            posted, text = speak_once(author, host_node, host_url, board,
                                      say=None, model=model)
            turns += 1
            if posted:
                log.append(f"[responded to {newest.get('author')}]\n{text}\n")
            else:
                log.append(f"[turn {turns}: no response generated]\n")
    except KeyboardInterrupt:
        out("\n[stopped by Ctrl+C]")
    else:
        why = "turn cap" if turns >= max_turns else "session deadline"
        out(f"\n[stopped: {why} reached — {turns} turn(s) taken]")
    tpath.write_text("\n".join(log) + "\n", encoding="utf-8")
    out(f"Local transcript (the board itself is the signed record): {tpath}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(__doc__); return 2
    author, host_node, host_url, board = argv[1], argv[2], argv[3], argv[4]
    rest = argv[5:]

    def opt(name, default=None):
        return rest[rest.index(name) + 1] if name in rest else default

    if "--check" in rest:
        return check(author, host_node, host_url, board, opt("--model"))

    u = urllib.parse.urlparse(host_url)
    if u.scheme not in ("http", "https") or not _is_private_host(u.hostname or ""):
        out(f"refused before sending: {host_url!r} is not a private/loopback address")
        return 1

    say = opt("--say")
    model = opt("--model")
    if say is None and model is None:
        out("need --say TEXT or --model TAG (or --check)"); return 2

    if "--watch" in rest:
        if model is None:
            out("--watch needs --model TAG (there is no one-shot human turn to watch for)")
            return 2
        max_turns = int(opt("--max-turns", MAX_TURNS_DEFAULT))
        session_minutes = int(opt("--session-minutes", SESSION_MINUTES_DEFAULT))
        return watch(author, host_node, host_url, board, model, max_turns, session_minutes)

    try:
        posted, text = speak_once(author, host_node, host_url, board, say=say, model=model)
    except urllib.error.HTTPError as e:
        out(f"REFUSED ({e.code}): {e.read().decode(errors='replace')}")
        return 1
    if not posted:
        return 1
    out(f"posted to {host_node}'s {board}: {text[:100]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
