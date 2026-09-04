"""Identity membership against a lowercase-keyed store uses one lowered form.

The 10b escalation (agora item 10b, commit 1c59983): an evicted key re-vouched
its way back in under a different case because `accept_intro` compared a RAW
event field, `key in self.evicted`, while self.evicted is keyed lowercase. The
ban held on shipping code only because verify_* had normalized the field first
— a single normalize, and no structural guarantee that the next accept_*
method wouldn't grow another raw membership.

Grok's ruling (agora_hex_normalize_finding / butter P2): harden each membership
to one lowered form, then FREEZE it structurally so the next raw membership
cannot land silently. A behavioral test per site would be furniture — with both
verify's normalize and the accept-side .lower(), no single mutant kills it
(masking, the same trap that killed the item-3 reopen assertion). This AST test
is the guard instead: it reads node.py and fails if any membership / .get /
.pop against a lowercase-keyed identity store uses a key that is not lowered.

Mutant: revert any `.lower()` on those sites (or add a new raw one). This test
goes red, naming the function and line.

Named limitation (Grok, P2 review): taint follows a name assigned from a raw
SUBSCRIPT (`key = intro["visitor_key_id"]`), not from `.get()`
(`key = intro.get("visitor_key_id")`). The latter would slip past unflagged.
This is deliberate — a real taint engine is not worth its false positives here,
and the shipping code uses subscripts. Equality against `self.speaker_key_id`
is also out of scope: it is denial-shaped, not the walk-around-the-ban shape.
Do not widen this freeze to close either without a ruling.
"""

import ast
import os
import unittest

NODE_PY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "kin_diary", "agora", "node.py",
)

# self-attributes keyed by lowercased hex identity. A key looked up here must
# already be lowered, or an upper/mixed-case identity silently misses the set.
LOWERCASE_IDENTITY_STORES = frozenset({
    "evicted", "evicted_at", "quarantined_keys", "evictions", "grants",
    "ephemeral_key_ids",
})

# access shapes we check the key operand of
_MEMBERSHIP_METHODS = frozenset({"get", "pop"})


def _is_self_store(node):
    """`self.<store>` where <store> is a lowercase identity store -> its name."""
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in LOWERCASE_IDENTITY_STORES):
        return node.attr
    return None


def _ends_in_lower(node):
    """`<anything>.lower()` — the operand is self-evidently lowered."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "lower")


def _raw_subscript(node):
    """`something["field"]` — a raw dict access (an unnormalized event field)."""
    return isinstance(node, ast.Subscript)


def _tainted_and_clean_locals(func):
    """Within one function: names assigned from a raw subscript (tainted) vs
    from a `.lower()` expression (clean). A name that is ever lowered is clean;
    a name only ever a raw subscript is tainted."""
    clean, tainted = set(), set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Name):
                continue
            if _ends_in_lower(node.value):
                clean.add(tgt.id)
            elif _raw_subscript(node.value):
                tainted.add(tgt.id)
    return tainted - clean, clean


def _operand_is_unsafe(operand, tainted):
    """A key operand is unsafe if it is a raw dict subscript, or a local that
    was assigned a raw event field and never lowered."""
    if _ends_in_lower(operand):
        return False
    if _raw_subscript(operand):
        return True
    if isinstance(operand, ast.Name) and operand.id in tainted:
        return True
    return False


class IdentityMembershipIsLowered(unittest.TestCase):

    def test_no_raw_identity_membership_in_node_py(self):
        with open(os.path.normpath(NODE_PY)) as fh:
            tree = ast.parse(fh.read(), filename="node.py")

        violations = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            tainted, _clean = _tainted_and_clean_locals(func)

            for node in ast.walk(func):
                # `x in self.<store>` / `x not in self.<store>`
                if isinstance(node, ast.Compare):
                    for op, right in zip(node.ops, node.comparators):
                        if isinstance(op, (ast.In, ast.NotIn)) and _is_self_store(right):
                            if _operand_is_unsafe(node.left, tainted):
                                violations.append(
                                    (func.name, node.lineno, right.attr, "in"))
                # `self.<store>.get(x)` / `self.<store>.pop(x)`
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _MEMBERSHIP_METHODS
                        and _is_self_store(node.func.value)
                        and node.args
                        and _operand_is_unsafe(node.args[0], tainted)):
                    violations.append(
                        (func.name, node.lineno, node.func.value.attr,
                         node.func.attr))

        self.assertEqual(
            violations, [],
            "\n\nRaw (unlowered) identity membership against a lowercase-keyed "
            "store. A mixed-case key silently misses the set — this is the 10b "
            "escalation shape. Lower the key operand:\n"
            + "\n".join(f"    {fn}() line {ln}: self.{store} .{how}"
                        for fn, ln, store, how in violations)
            + "\n")

    def test_the_scan_sees_every_frozen_store(self):
        # guard on the guard: every store in the freeze must actually exist in
        # node.py, so dropping one from LOWERCASE_IDENTITY_STORES (which would
        # silently stop guarding it) fails here. Loop the set, not a sample.
        with open(os.path.normpath(NODE_PY)) as fh:
            src = fh.read()
        for store in LOWERCASE_IDENTITY_STORES:
            self.assertIn(
                f"self.{store}", src,
                f"self.{store} is in the freeze but not in node.py — the freeze "
                f"is guarding a name that no longer exists (or was dropped)")

        # And the floor: dropping one of the escalation-critical stores FROM
        # the freeze (which would silently stop guarding it) must fail here.
        # The set is its own source of truth, so name the ones that must be in
        # it. These are the walk-around-the-ban stores from 10b and its kin.
        for required in ("evicted", "quarantined_keys", "evictions", "grants"):
            self.assertIn(
                required, LOWERCASE_IDENTITY_STORES,
                f"{required} was dropped from the identity-membership freeze — "
                f"raw lookups against it are no longer guarded")


if __name__ == "__main__":
    unittest.main()
