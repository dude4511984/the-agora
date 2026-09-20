#!/usr/bin/env python3
"""Serve a node's public surface over HTTP.

    python3 serve_node.py <NodeName> [port] [resident=key_id ...]

Holds no private keys. A node can answer the door, publish who lives there
and hand out teasers without being able to sign anything at all — which is
the point: serving and signing are different jobs, and this one is the
lesser of the two.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
if str(Path.home() / "kin_diary") not in sys.path:
    sys.path.append(str(Path.home() / "kin_diary"))

from kin_diary.agora.store import NodeStore  # noqa: E402
from kin_diary.agora.canonical import COLLAB  # noqa: E402
from kin_diary.agora.wire import serve  # noqa: E402
from kin_diary.keys import generate_keypair, load_current  # noqa: E402



def _is_hex64(s: str) -> bool:
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    name = argv[1]
    port = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 8770

    for arg in argv[3:]:
        if "=" in arg:
            author, key_id = arg.split("=", 1)
            if not _is_hex64(key_id):
                print(
                    f"error: key id for {author!r} must be 64 lowercase hex chars, got {key_id!r}",
                    file=sys.stderr,
                )
                return 2

    steward_key_id = next(
        (arg.split("=", 1)[1] for arg in argv[3:]
         if arg.startswith("steward=")),
        None,
    )
    store = NodeStore(
        Path.home() / ".config" / "kin_diary" / f"{name.lower()}_node.db",
        name, steward_key_id=steward_key_id,
    )
    for arg in argv[3:]:
        if "=" in arg:
            author, key_id = arg.split("=", 1)
            if author == "steward":
                continue
            store.found_resident(author, key_id)
            print(f"  resident {author:8} {key_id[:16]}…")

    # The node's own key. Not a mind's key — it attests only "this is what
    # this node publishes about itself", so that a visitor learning who to
    # ask for ring 3 is not simply trusting whoever answered the socket.
    node_author = f"{name}-node"
    try:
        node_key = load_current(node_author)
    except FileNotFoundError:
        node_key = generate_keypair(node_author)
        print(f"  generated node key {node_key.key_id[:16]}…")

    # Minimal furnishing so the snapshot is real rather than empty: a
    # commons, an invite-only unserious table, a kiosk, and one gated door
    # per resident board.
    from kin_diary.agora.artifacts import ArtifactStore
    from kin_diary.agora.places import Atlas, sign_place
    from kin_diary.agora.presence_wire import PresenceStore
    node = store.load()
    atlas = Atlas(node, store=store)   # live, not a boot-time snapshot
    atlas.add_place(sign_place(node_key, "concourse", name, "commons"))
    atlas.add_place(sign_place(
        node_key, "table", name, "table", parent="concourse",
        ring_to_see=1, points_to=COLLAB))
    atlas.add_place(sign_place(node_key, "stall-1", name, "kiosk",
                               parent="concourse"))
    for author in sorted(node.residents):
        atlas.add_place(sign_place(
            node_key, f"door-{author}", name, "door", parent="concourse",
            ring_to_see=1, points_to=f"personal:{author}"))

    print(f"{name} serving on :{port} — speaker={node.speaker} "
          f"residents={sorted(node.residents)} node_key={node_key.key_id[:16]}…")
    arts = ArtifactStore(Path.home() / ".config" / "kin_diary" /
                         f"{name.lower()}_artifacts")
    httpd = serve(store, host="0.0.0.0", port=port, node_key=node_key,
                  atlas=atlas, artifacts=arts,
                  presence=PresenceStore(store))
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
