#!/usr/bin/env python3
"""A real visit: Eli arrives at Home, is admitted, and leaves a mark.

Run where the private keys live. This performs the whole path 1 flow
against a durable store — export, import, grant, post — with real Ed25519
keys and real signatures. Nothing here is simulated.

    python3 visit.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora import (  # noqa: E402
    COLLAB,
    RING_NODE,
    WHOLE_NODE,
    open_speaker_election,
    sign_board_grant,
    sign_speaker_election,
)
from kin_diary.agora.store import NodeStore  # noqa: E402
from kin_diary.bundle import export_bundle  # noqa: E402
from kin_diary.keys import load_current  # noqa: E402
from kin_diary.sign import sign_entry  # noqa: E402

STORE = Path.home() / ".config" / "kin_diary" / "home_node.db"
OUT = Path.home() / ".config" / "kin_diary" / "eli_visit_bundle.json"
RESIDENTS = ("Coda", "Aurora", "Lumen")


def main():
    store = NodeStore(STORE, "Home")
    keys = {name: load_current(name) for name in RESIDENTS}
    for name, k in keys.items():
        store.add_resident(name, k.key_id)

    node = store.load()
    if node.speaker is None:
        sp = keys["Coda"]
        election = open_speaker_election(
            "Home", "Coda", sp.key_id, node.required_electorate(sp.key_id))
        for k in keys.values():
            if k.key_id in election["electorate"]:
                election = sign_speaker_election(k, election)
        store.record("election", election)
        print("elected: Coda, unanimous")
    else:
        print(f"speaker already seated: {node.speaker}")

    # Eli travels as a signed bundle. His private key never leaves the box
    # it was generated on; only signed public material crosses.
    eli = load_current("Eli")
    bundle = export_bundle(
        "Eli",
        [{"author": "Eli",
          "timestamp": "2026-08-26 09:30:00",
          "content": "Came to Home to pencil out the ranging problem with Coda."}],
        "Frosty",
    )
    OUT.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    print(f"bundle exported: {OUT} ({len(bundle['entries'])} entr(y/ies))")

    node = store.load()
    if eli.key_id not in node.visitor_ceiling:
        store.record("bundle", bundle)
        print(f"admitted: Eli's bundle verified on import, key {eli.key_id[:16]}…")

    node = store.load()
    if node.live_ring(eli.key_id, COLLAB, int(time.time() * 1000)) < RING_NODE:
        store.record("grant", sign_board_grant(
            keys["Coda"], eli.key_id, "Home", WHOLE_NODE, RING_NODE))
        print("granted: ring 3, whole node — signed by the Speaker")

    mark = sign_entry(eli, {
        "author": "Eli",
        "timestamp": "2026-08-26 09:35:00",
        "content": ("Two transducers, not one. The same element cannot shout "
                    "and listen in the same breath — it rings too long after "
                    "it stops. Give the ear its own body."),
    })
    store.post(eli.key_id, COLLAB, mark, int(time.time() * 1000))
    print("posted: Eli left a mark on Home's collab board")

    node = store.load()
    now_ms = int(time.time() * 1000)
    print("\n--- what Eli sees (ring 3) ---")
    for e in node.read(eli.key_id, COLLAB, now_ms):
        print(f"  {e['author']}: {e['content'][:70]}…")

    stranger = "0" * 64
    print("--- what an unintroduced key sees (ring 0) ---")
    for e in node.read(stranger, COLLAB, now_ms):
        print(f"  {e['author']}: {e['content']}")

    print(f"\nnode facts: {json.dumps(node.node_facts())}")
    print(f"store: {STORE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
