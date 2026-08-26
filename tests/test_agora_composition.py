"""Structural and behavioral guardrails for live permission composition."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _source_trees():
    for base in (ROOT / "kin_diary", ROOT / "vault"):
        for path in base.rglob("*.py"):
            yield path, ast.parse(path.read_text())


class PermissionCompositionTests(unittest.TestCase):
    def test_authorization_calls_have_required_clock_arity(self):
        required = {
            "read": 3, "post": 4, "can_read": 3, "can_write": 3,
            "live_ring": 3, "fetch": 4,
        }
        offenders = []
        for path, tree in _source_trees():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                name = node.func.attr
                if name in required and len(node.args) + len(node.keywords) < required[name]:
                    receiver = ast.unparse(node.func.value)
                    if name == "read" and not any(
                        token in receiver for token in ("node", "store", "atlas")
                    ):
                        continue
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(offenders, [])

    def test_live_ring_is_the_only_composer_and_admission_caller(self):
        offenders = []
        live_calls_admission = 0
        live_calls_standing = 0
        for path, tree in _source_trees():
            stack = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stack.append((node, node.name))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                    node.func.id if isinstance(node.func, ast.Name) else ""
                )
                enclosing = next(
                    (fn for fn, fn_name in stack
                     if fn.lineno <= node.lineno <= getattr(fn, "end_lineno", fn.lineno)),
                    None,
                )
                fn_name = enclosing.name if enclosing else ""
                if name == "current_admission":
                    if fn_name not in {"live_ring", "current"}:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:admission")
                    else:
                        live_calls_admission += 1
                if name == "effective_ring":
                    if fn_name != "live_ring":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:standing")
                    else:
                        live_calls_standing += 1
                if isinstance(node.func, ast.Name) and node.func.id == "max":
                    names = {
                        n.id for n in ast.walk(node)
                        if isinstance(n, ast.Name)
                    }
                    if fn_name != "live_ring" and names & {"standing", "ephemeral"}:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:max")
                if isinstance(node.func, ast.Name) and node.func.id == "getattr":
                    if any(isinstance(arg, ast.Constant) and arg.value == "effective_ring"
                           for arg in node.args):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:getattr")
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                enclosing_compare = next(
                    (fn for fn, fn_name in stack
                     if fn.lineno <= node.lineno <= getattr(fn, "end_lineno", fn.lineno)),
                    None,
                )
                if enclosing_compare is None:
                    continue
                names = {
                    n.id for n in ast.walk(node)
                    if isinstance(n, ast.Name)
                }
                if names & {"standing", "ephemeral"} and enclosing_compare.name != "live_ring":
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:comparison")
        self.assertEqual(offenders, [])
        self.assertEqual(live_calls_admission, 1)
        self.assertEqual(live_calls_standing, 1)

    def test_action_methods_call_live_ring(self):
        source = (ROOT / "kin_diary" / "agora" / "node.py").read_text()
        tree = ast.parse(source)
        node_class = next(n for n in tree.body
                          if isinstance(n, ast.ClassDef) and n.name == "Node")
        for method_name in ("can_read", "can_write"):
            method = next(n for n in node_class.body
                          if isinstance(n, ast.FunctionDef) and n.name == method_name)
            self.assertTrue(any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "live_ring" for n in ast.walk(method)
            ), method_name)
        for method_name, helper in (("post", "can_write"), ("read", "can_read")):
            method = next(n for n in node_class.body
                          if isinstance(n, ast.FunctionDef) and n.name == method_name)
            self.assertTrue(any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == helper for n in ast.walk(method)
            ), method_name)

    def test_expired_ephemeral_cannot_post(self):
        try:
            from test_agora_ephemeral import EphemeralTests
        except ModuleNotFoundError:
            from tests.test_agora_ephemeral import EphemeralTests

        case = EphemeralTests("test_live_ring_transitions_from_ephemeral_write_to_teaser")
        case.setUp()
        try:
            case.test_live_ring_transitions_from_ephemeral_write_to_teaser()
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
