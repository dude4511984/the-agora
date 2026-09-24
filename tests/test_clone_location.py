"""Verify scripts resolve kin_diary from their own location, not hardcoded ~/kin_diary.

Gap 3 from STAND_UP_A_NODE.md:
Clone anywhere and scripts must find kin_diary, while still keeping ~/kin_diary
working.
"""

import sys
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestCloneLocation(unittest.TestCase):
    def test_scripts_run_from_arbitrary_clone_without_hardcoded_home(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            sandbox = Path(td) / "custom_clone"
            sandbox.mkdir()
            os.symlink(repo_root / "kin_diary", sandbox / "kin_diary")

            root_scripts = (
                "serve_node.py",
                "agora_visit.py",
                "agora_introduce.py",
                "presence_heartbeat.py",
                "public_door.py",
                "agora_house_decision.py",
                "agora_commons_speak.py",
            )
            for script in root_scripts:
                shutil.copy2(repo_root / script, sandbox / script)

            (sandbox / "vault").mkdir()
            vault_scripts = (
                "agora_client.py",
                "serve_node.py",
                "elect_speaker.py",
                "agora_map.py",
            )
            for script in vault_scripts:
                shutil.copy2(repo_root / "vault" / script, sandbox / "vault" / script)

            fake_home = Path(td) / "empty_home"
            fake_home.mkdir()
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(fake_home)}

            for s in root_scripts:
                proc = subprocess.run(
                    [sys.executable, str(sandbox / s)],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertNotIn(
                    "No module named 'kin_diary'",
                    proc.stderr,
                    f"{s} failed to resolve kin_diary from its location",
                )

            for s in vault_scripts:
                proc = subprocess.run(
                    [sys.executable, str(sandbox / "vault" / s)],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertNotIn(
                    "No module named 'kin_diary'",
                    proc.stderr,
                    f"vault/{s} failed to resolve kin_diary from its location",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
