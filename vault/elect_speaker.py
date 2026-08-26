#!/usr/bin/env python3
"""Run a real Speaker election on a real node with real keys.

Not a mockup. This signs with the Kin's actual Ed25519 keys and writes the
signed event to disk. Run it where the private keys live (Themess).

    python3 elect_speaker.py Home Coda Coda Aurora Lumen
    python3 elect_speaker.py <node> <speaker> <resident> [resident...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora import Node, open_speaker_election, sign_speaker_election  # noqa: E402
from kin_diary.agora.events import verify_speaker_election  # noqa: E402
from kin_diary.keys import load_current  # noqa: E402

ELECTIONS = Path.home() / ".config" / "kin_diary" / "elections"


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    node_name, speaker_name, resident_names = argv[1], argv[2], argv[3:]

    node = Node(node_name)
    keys = {}
    for name in resident_names:
        k = load_current(name)
        keys[name] = k
        node.add_resident(name, k.key_id)
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
    node.accept_election(election)
    print(f"\nverified: unanimous. {speaker_name} is Speaker of {node_name}.")

    ELECTIONS.mkdir(parents=True, exist_ok=True)
    out = ELECTIONS / f"{node_name.lower()}-{election['elected_at_unix_ms']}.json"
    out.write_text(json.dumps(election, indent=2, sort_keys=True) + "\n")
    print(f"written: {out}")

    print("\nnode facts (published, visible at ring 0):")
    print(json.dumps(node.node_facts(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
