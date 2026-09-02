#!/usr/bin/env python3
"""Run the whole suite, not one file at a time.

Why this exists: every test file in tests/ used to be run by hand,
`python3 tests/test_x.py`, because each one carried its own
`unittest.main()`. That works right up until a file's main block is in the
middle (98ac00f: 67 tests defined after unittest.main() had already exited)
or missing altogether (test_agora_resident_pause.py: two classes, thirteen
tests, no main, exit 0, no output). Those tests are not red. They are absent,
and a file that runs nothing reports success.

Discovery does not care where a main block sits, or whether there is one.
Use this. `python3 run_tests.py [-v] [pattern]`
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(ROOT, "tests")


def build_suite(pattern="test*.py"):
    # tests/ is not a package; the files import each other flat
    # (`from test_agora import key`), so top_level_dir must be tests/ itself.
    sys.path.insert(0, TESTS)
    sys.path.insert(0, ROOT)
    return unittest.defaultTestLoader.discover(
        start_dir=TESTS, pattern=pattern, top_level_dir=TESTS
    )


def main(argv):
    verbosity = 2 if "-v" in argv else 1
    pattern = next((a for a in argv[1:] if a.endswith(".py")), "test*.py")
    suite = build_suite(pattern)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
