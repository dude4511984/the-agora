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

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora.store import NodeStore  # noqa: E402
from kin_diary.agora.wire import serve  # noqa: E402
from kin_diary.keys import generate_keypair, load_current  # noqa: E402


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    name = argv[1]
    port = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 8770

    store = NodeStore(Path.home() / ".config" / "kin_diary" / f"{name.lower()}_node.db", name)
    for arg in argv[3:]:
        if "=" in arg:
            author, key_id = arg.split("=", 1)
            store.add_resident(author, key_id)
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

    node = store.load()
    print(f"{name} serving on :{port} — speaker={node.speaker} "
          f"residents={sorted(node.residents)} node_key={node_key.key_id[:16]}…")
    httpd = serve(store, host="0.0.0.0", port=port, node_key=node_key)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
