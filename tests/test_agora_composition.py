"""Structural guardrails for the single live-permission composer."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


class PermissionCompositionTests(unittest.TestCase):
    def test_action_surfaces_do_not_call_standing_ring_directly(self):
        root = Path(__file__).parents[1]
        package = root / "kin_diary" / "agora"
        offenders = []
        for path in package.glob("*.py"):
            if path.name in {"node.py", "__init__.py"}:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "effective_ring"):
                    offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])

    def test_live_ring_is_the_only_permission_composition_site(self):
        source = (Path(__file__).parents[1] / "kin_diary" / "agora" / "node.py").read_text()
        tree = ast.parse(source)
        live = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Node"
        )
        method = next(
            node for node in live.body
            if isinstance(node, ast.FunctionDef) and node.name == "live_ring"
        )
        self.assertEqual(
            sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "max" for node in ast.walk(method)),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
