"""The suite is allowed to be red. It is not allowed to be absent.

Twice now, tests in this directory have gone missing without going red:

  98ac00f  three files carried `if __name__ == "__main__": unittest.main()`
           in the MIDDLE. Every class appended below it — the obvious place to
           add a test — was defined after main() had already exited. 67 tests.
           Not failing. Never collected.

  and test_agora_resident_pause.py, which had two TestCase classes, thirteen
  tests, and no main block at all. `python3 tests/test_agora_resident_pause.py`
  printed nothing and exited 0. A file that runs nothing reports success.

Both survived because the suite was run one file at a time. That is the habit
this guards, and the habit is fine — `run_tests.py` uses discovery and is
immune to both bugs, but nobody reaches for it when they are iterating on one
file, and a stranded class is invisible exactly then.

A third instance of the same family lives one layer out, in the mutation
harness, and this file cannot see it: `.mut/run.sh` built its scratch tree
from tracked files only, so an untracked killing test — the normal state of a
test written just before its own commit — was absent, and the mutant "survived"
against a killer that was never in the room (P3 verify, 2026-09-04). Same shape:
a test absent without going red. The harness now includes untracked files and
aborts on a real-tree/scratch count mismatch. See `.mut/README.md`.

So the invariant is: a test file, run as a script, executes every test it
defines. Structurally that means one `if __name__ == "__main__"` block, and it
is the LAST top-level statement in the file. Anything after it is dead on a
script run and alive under discovery, which is the worst of both — it passes
locally and its coverage is a lie.

Checked statically, because the alternative is 20 subprocesses. Mutation
check: append a class below a main block, or delete a main block, and this
must go red. Verified both ways 2026-09-02.
"""

import ast
import os
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _test_files():
    return sorted(
        f for f in os.listdir(TESTS_DIR)
        if f.startswith("test") and f.endswith(".py")
    )


def _main_guard_index(tree):
    """Index of the `if __name__ == "__main__":` statement, or None."""
    for i, node in enumerate(tree.body):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare) and \
                isinstance(test.left, ast.Name) and \
                test.left.id == "__name__":
            return i
    return None


def _defines_tests(tree):
    return any(
        isinstance(node, ast.ClassDef) and any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name.startswith("test")
            for item in node.body
        )
        for node in tree.body
    )


class SuiteIntegrity(unittest.TestCase):

    def test_every_test_file_runs_everything_it_defines_as_a_script(self):
        no_main, stranded = [], []

        for fname in _test_files():
            with open(os.path.join(TESTS_DIR, fname)) as fh:
                tree = ast.parse(fh.read(), filename=fname)
            if not _defines_tests(tree):
                continue

            idx = _main_guard_index(tree)
            if idx is None:
                no_main.append(fname)
                continue

            after = [
                n for n in tree.body[idx + 1:]
                if isinstance(n, (ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
            ]
            if after:
                stranded.append(
                    (fname, tree.body[idx].lineno,
                     [(n.name, n.lineno) for n in after])
                )

        msg = []
        if no_main:
            msg.append(
                "These files define tests and have no "
                '`if __name__ == "__main__": unittest.main()`. Run one as a '
                "script and it prints nothing and exits 0:\n"
                + "\n".join(f"    {f}" for f in no_main)
            )
        if stranded:
            msg.append(
                "These files define things AFTER their main block. On a "
                "script run they never execute; under discovery they do. "
                "Move the main block to the end of the file:\n"
                + "\n".join(
                    f"    {f}: main at line {ln}, then "
                    + ", ".join(f"{n} (line {l})" for n, l in items)
                    for f, ln, items in stranded
                )
            )

        self.assertEqual([], msg, "\n\n" + "\n\n".join(msg) + "\n")

    def test_the_scan_actually_looked_at_the_files(self):
        # A guard on the guard: if the listing silently returns nothing, the
        # check above passes vacuously and this whole file is theatre.
        self.assertGreater(len(_test_files()), 15)


if __name__ == "__main__":
    unittest.main()
