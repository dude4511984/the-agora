"""The map must start on a machine that isn't Frosty.

It imported kin_talk from ~/pops_shop unconditionally, and that file isn't
in this repo, so on any other machine `import agora_map` died with
ModuleNotFoundError before serving a page. The map is the first thing an
outside steward runs (found 2026-09-24, cloud box). This imports the map in
a fresh interpreter with HOME pointed at an empty directory: no pops_shop.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TheMapStartsWithoutPopsShop(unittest.TestCase):
    def test_import_without_kin_talk(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, PYTHONPATH=str(ROOT))
            code = ("import agora_map; "
                    "assert agora_map.kin_talk is None, 'picked up a kin_talk from somewhere'; "
                    "print('started')")
            r = subprocess.run([sys.executable, "-c", code], env=env, cwd=home,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("started", r.stdout)

    def test_voice_route_refuses_plainly(self):
        src = (ROOT / "agora_map.py").read_text()
        self.assertIn("voice isn't set up on this node", src)


if __name__ == "__main__":
    unittest.main()
