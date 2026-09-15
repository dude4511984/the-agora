#!/usr/bin/env python3
"""Run a real unanimous house decision on a node — Path A, chosen 2026-09-15.

No Speaker is seated on Frosty on purpose (see
~/claude_home/agora_speaker_decision_2026-09-15.md). When the house needs
to permit one specific act, every current resident's own key signs it —
steward custody, all local, nothing held back for ceremony.

    python3 agora_house_decision.py <node> <act_kind> <act_signature>

<act_kind> names the act being permitted (e.g. "grant:read:table" or
whatever the caller of accept_house_decision on the wire side expects).
<act_signature> binds this permission to that specific act, not a class
of acts — see verify_house_decision's own docstring for why. Dry run
first if you are not sure this is the exact act you mean to permit;
this script has no undo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora.store import NodeStore
from kin_diary.agora.events import open_house_decision, sign_house_decision, verify_house_decision
from kin_diary.keys import load_current


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    node_name, act_kind, act_signature = argv[1], argv[2], argv[3]
    dry_run = "--dry-run" in argv

    store = NodeStore(
        Path.home() / ".config" / "kin_diary" / f"{node_name.lower()}_node.db",
        node_name,
    )
    node = store.load()
    residents = sorted(node.residents)
    if not residents:
        print(f"{node_name} has no residents — nothing to decide unanimously.")
        return 1

    print(f"{node_name}: residents {residents}, paused={node.is_paused()}")
    print(f"Act: {act_kind!r}  signature: {act_signature}")

    decision = open_house_decision(node_name, act_kind, act_signature)
    signed, missing = [], []
    for name in residents:
        try:
            key = load_current(name)
            decision = sign_house_decision(key, decision)
            signed.append(name)
        except FileNotFoundError:
            missing.append(name)

    if missing:
        print(f"REFUSED: no local key for {missing} — cannot reach unanimity "
              f"from here. A house decision needs every current resident's "
              f"signature; a missing key blocks the act by design.")
        return 1

    try:
        verify_house_decision(decision, node.valid_resident_keys())
    except Exception as e:
        print(f"REFUSED: {e}")
        return 1

    print(f"Signed by all {len(signed)}: {signed}. Verifies unanimous.")

    if dry_run:
        print("--dry-run: not persisted. Re-run without --dry-run to commit.")
        return 0

    store.record("house-decision", decision)
    print(f"Committed to {node_name}'s event log. The house has spoken as one, "
          f"four signatures deep.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
