"""Every accepted event must be able to reach the log.

`Node` is an accumulator over signed events and `NodeStore` replays them,
so a state change that cannot be recorded is a state change that does not
survive a restart. Nothing in the type system says so. This says so.

Written 2026-08-26 after finding that `accept_appeal`, `accept_finding`
and `accept_ruling` had never been in REPLAY. All three worked, all three
were tested, and every caller in the tree was a test holding a `Node` in
memory. The eviction was durable; the hearing that reviews it was not.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

try:
    from kin_diary.agora.node import Node
    from kin_diary.agora.store import REPLAY
except ModuleNotFoundError:  # running from inside tests/
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from kin_diary.agora.node import Node
    from kin_diary.agora.store import REPLAY


ROOT = Path(__file__).parents[1]

# A method may sit outside REPLAY only with a reason, in writing, here.
# "We have not got to it yet" is not a reason — that is the bug this file
# exists to catch. Presence is the shape that legitimately belongs here:
# it expires, and an append-only log cannot hold something that expires.
NOT_DURABLE = {
    # name: why it must never be replayed
}


def _node_accept_methods() -> dict[str, ast.FunctionDef]:
    tree = ast.parse((ROOT / "kin_diary" / "agora" / "node.py").read_text())
    node_class = next(n for n in tree.body
                      if isinstance(n, ast.ClassDef) and n.name == "Node")
    return {n.name: n for n in node_class.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("accept_")}


class DurabilityTests(unittest.TestCase):
    def test_every_accept_method_is_replayable_or_declared_ephemeral(self):
        accepts = set(_node_accept_methods())
        replayed = set(REPLAY.values())
        stranded = accepts - replayed - set(NOT_DURABLE)
        self.assertEqual(
            sorted(stranded), [],
            "these change node state but no event kind can carry them, so "
            "they die on restart; add a REPLAY kind or declare why not",
        )

    def test_replay_targets_all_exist(self):
        missing = [m for m in REPLAY.values() if not hasattr(Node, m)]
        self.assertEqual(missing, [], "REPLAY names a method Node does not have")

    def test_replayed_methods_take_exactly_the_payload(self):
        # store.record dispatches as getattr(node, name)(payload). A method
        # needing a second argument cannot be reached that way, and the
        # mismatch is invisible until someone tries to persist one.
        offenders = []
        for kind, method_name in sorted(REPLAY.items()):
            params = list(inspect.signature(
                getattr(Node, method_name)).parameters.values())
            required = [p for p in params[1:]  # drop self
                        if p.default is inspect.Parameter.empty
                        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            if len(required) != 1:
                offenders.append(
                    f"{kind} -> {method_name} needs "
                    f"{[p.name for p in required]}, dispatch passes one payload")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
