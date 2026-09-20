#!/usr/bin/env python3
"""Two-signature cross-node key introduction — the "invited in" half of
"they knock or talk in the commons, then get invited in," same for every
node (Don, 2026-09-16). Not a new admission rule: this is Path 2, already
in kin_diary/agora/events.py (accept_intro), and already symmetric across
Frosty's house-decision model and Home's elected-Speaker model, because it
only ever checks "is this a valid resident of the host," never who holds
the Speaker chair.

The two keys this needs almost never live on the same machine — a
visitor's key lives at their own home, the vouching resident's key lives
at the host. So this runs in two steps, carried between machines by hand
as a signed JSON blob (no private key material in it — safe to scp,
paste into a chat, whatever's convenient):

    # on the VISITOR's own machine, holding their private key:
    python3 agora_introduce.py start <visitor_author> <host_node_name> \\
        <resident_key_id_hex> [--out FILE]

    # on the HOST machine, holding the vouching resident's private key:
    python3 agora_introduce.py countersign <resident_author> <blob_file> \\
        <host_url> [--dry-run]

<resident_key_id_hex> can be read straight off a live presence entry for
that resident (GET /view — that's them actually standing in the commons
right now) or off their own key file's meta.json. The talking that leads
to this happens outside the protocol, same as it always has here — a
room, a chat, a palaver. This script is only the paper trail once someone
says yes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
if str(Path.home() / "kin_diary") not in sys.path:
    sys.path.append(str(Path.home() / "kin_diary"))

from kin_diary.agora.events import (
    countersign_key_intro,
    start_key_intro,
    verify_key_intro,
)
from kin_diary.agora.wire import sign_request
from kin_diary.keys import load_current


def _post_intro(host_url: str, host_node: str, intro: dict, signer) -> dict:
    import urllib.request

    body = json.dumps(intro, sort_keys=True).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers.update(sign_request(signer, host_node, "/intro", body=body))
    req = urllib.request.Request(
        host_url.rstrip("/") + "/intro", data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def cmd_start(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    visitor_author, host_node, resident_key_id = argv[1], argv[2], argv[3]
    out = argv[argv.index("--out") + 1] if "--out" in argv else None

    key = load_current(visitor_author)
    intro = start_key_intro(key, host_node, resident_key_id)
    blob = json.dumps(intro, indent=2, sort_keys=True) + "\n"
    if out:
        Path(out).write_text(blob, encoding="utf-8")
        print(f"Visitor half written to {out}.")
        print(f"Carry this file to {host_node} (scp is fine — no private key "
              f"material in it) and run 'countersign' there.")
    else:
        print(blob)
    return 0


def cmd_countersign(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    resident_author, blob_file, host_url = argv[1], argv[2], argv[3]
    dry_run = "--dry-run" in argv

    intro = json.loads(Path(blob_file).read_text(encoding="utf-8"))
    key = load_current(resident_author)
    intro = countersign_key_intro(key, intro)
    verify_key_intro(intro)  # both signatures are real now; refuse to send otherwise

    print(f"{resident_author} vouches for {intro['visitor_key_id'][:16]}… "
          f"to visit {intro['host_node']}, capped at ring {intro['max_ring']}.")
    if dry_run:
        print("--dry-run: not sent. Re-run without --dry-run to submit.")
        print(json.dumps(intro, indent=2, sort_keys=True))
        return 0

    result = _post_intro(host_url, intro["host_node"], intro, signer=key)
    print(f"Accepted: {result}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("start", "countersign"):
        print(__doc__)
        return 2
    return {"start": cmd_start, "countersign": cmd_countersign}[argv[1]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
