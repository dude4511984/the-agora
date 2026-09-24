#!/usr/bin/env python3
"""Run a real Speaker election on a real node with real keys.

Not a mockup. This signs with the Kin's actual Ed25519 keys and records the
signed election in the node's own database (~/.config/kin_diary/<node>_node.db),
where serve_node replays it: the node has a Speaker from then on. A copy is
also written to ~/.config/kin_diary/elections/. Run it where the private keys
live, on the machine that serves the node.

It used to run the election on a throwaway in-memory node and only write the
JSON copy, which nothing reads: it printed "Ada is Speaker" while the served
node stayed paused with no Speaker (found following STAND_UP_A_NODE.md on a
clean box, 2026-09-24).

    python3 elect_speaker.py Home Coda Coda Aurora Lumen
    python3 elect_speaker.py <node> <speaker> <resident> [resident...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
if str(Path.home() / "kin_diary") not in sys.path:
    sys.path.append(str(Path.home() / "kin_diary"))

from kin_diary.agora import Node, open_speaker_election, sign_speaker_election  # noqa: E402
from kin_diary.agora.store import NodeStore  # noqa: E402
from kin_diary.agora.events import verify_speaker_election  # noqa: E402
from kin_diary.keys import load_current  # noqa: E402

ELECTIONS = Path.home() / ".config" / "kin_diary" / "elections"
NODE_DB_DIR = Path.home() / ".config" / "kin_diary"


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    node_name, speaker_name, resident_names = argv[1], argv[2], argv[3:]

    db = NODE_DB_DIR / f"{node_name.lower()}_node.db"
    if not db.is_file():
        print(f"error: no node database at {db}. Start the node first "
              f"(serve_node.py {node_name} ...); an election needs a real node to seat.")
        return 1
    store = NodeStore(db, node_name)
    node = store.load()
    keys = {}
    for name in resident_names:
        k = load_current(name)
        keys[name] = k
        if node.residents.get(name) != k.key_id:
            print(f"error: {name} with key {k.key_id[:16]}… is not a resident of {node_name}")
            return 1
        print(f"  resident {name:8} {k.key_id[:16]}…")

    if speaker_name not in keys:
        print(f"error: {speaker_name} is not among the residents given")
        return 1

    sp = keys[speaker_name]
    required = node.required_electorate(sp.key_id)
    print(f"\nelectorate: {len(required)} of {len(keys)} resident key(s) must sign "
          f"(unanimous, not majority)")

    election = open_speaker_election(node_name, speaker_name, sp.key_id, required)
    for name, k in keys.items():
        if k.key_id in election["electorate"]:
            election = sign_speaker_election(k, election)
            print(f"  signed by {name}")

    verify_speaker_election(election)
    store.record("election", election)            # into the node's own log
    node = store.load()
    if node.speaker != speaker_name:
        print(f"error: the node did not seat {speaker_name} (speaker={node.speaker!r})")
        return 1
    print(f"\nverified: unanimous. {speaker_name} is Speaker of {node_name}, "
          f"recorded in {db}.")

    ELECTIONS.mkdir(parents=True, exist_ok=True)
    out = ELECTIONS / f"{node_name.lower()}-{election['elected_at_unix_ms']}.json"
    out.write_text(json.dumps(election, indent=2, sort_keys=True) + "\n")
    print(f"written: {out}")

    print("\nnode facts (published, visible at ring 0):")
    print(json.dumps(node.node_facts(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
