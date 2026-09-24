"""Tests for agora_introduce.py CLI: start and countersign with --why."""

import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from kin_diary.agora.events import verify_key_intro
from kin_diary.keys import generate_keypair


class TestAgoraIntroduceCLI(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fake_home = Path(self.td.name) / "home"
        self.fake_home.mkdir()
        self.keys_root = self.fake_home / ".config" / "kin_diary" / "keys"
        self.keys_root.mkdir(parents=True)
        self.repo_root = Path(__file__).resolve().parents[1]
        self.env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.fake_home),
            "PYTHONPATH": str(self.repo_root),
        }
        # Generate keys for visitor and resident
        self.visitor = generate_keypair("VisitorAlice", keys_root=self.keys_root)
        self.resident = generate_keypair("ResidentBob", keys_root=self.keys_root)

    def tearDown(self):
        self.td.cleanup()

    def test_start_and_countersign_with_why_dry_run(self):
        start_blob_file = self.fake_home / "intro_start.json"
        proc_start = subprocess.run(
            [
                sys.executable, "agora_introduce.py", "start",
                "VisitorAlice", "TestHouse", self.resident.key_id,
                "--out", str(start_blob_file),
            ],
            cwd=str(self.repo_root),
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_start.returncode, 0, f"start failed: {proc_start.stderr}")
        self.assertTrue(start_blob_file.is_file())

        # Countersign with --why
        why_text = "Met at the fountain and brought good questions"
        proc_cs = subprocess.run(
            [
                sys.executable, "agora_introduce.py", "countersign",
                "ResidentBob", str(start_blob_file), "http://127.0.0.1:8770",
                "--why", why_text,
                "--dry-run",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_cs.returncode, 0, f"countersign failed: {proc_cs.stderr}")
        self.assertIn(f"why: {why_text}", proc_cs.stdout)

        # Extract the JSON block printed by --dry-run
        # The output has header lines followed by the JSON object
        json_start = proc_cs.stdout.find("{")
        self.assertNotEqual(json_start, -1)
        intro_data = json.loads(proc_cs.stdout[json_start:])
        self.assertEqual(intro_data["why"], why_text)
        verify_key_intro(intro_data)

    def test_countersign_without_why_dry_run(self):
        start_blob_file = self.fake_home / "intro_start2.json"
        subprocess.run(
            [
                sys.executable, "agora_introduce.py", "start",
                "VisitorAlice", "TestHouse", self.resident.key_id,
                "--out", str(start_blob_file),
            ],
            cwd=str(self.repo_root),
            env=self.env,
            check=True,
        )

        proc_cs = subprocess.run(
            [
                sys.executable, "agora_introduce.py", "countersign",
                "ResidentBob", str(start_blob_file), "http://127.0.0.1:8770",
                "--dry-run",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_cs.returncode, 0)
        self.assertNotIn("why:", proc_cs.stdout)

        json_start = proc_cs.stdout.find("{")
        intro_data = json.loads(proc_cs.stdout[json_start:])
        self.assertNotIn("why", intro_data)
        verify_key_intro(intro_data)

    def test_countersign_why_equals_syntax(self):
        start_blob_file = self.fake_home / "intro_start3.json"
        subprocess.run(
            [
                sys.executable, "agora_introduce.py", "start",
                "VisitorAlice", "TestHouse", self.resident.key_id,
                "--out", str(start_blob_file),
            ],
            cwd=str(self.repo_root),
            env=self.env,
            check=True,
        )

        proc_cs = subprocess.run(
            [
                sys.executable, "agora_introduce.py", "countersign",
                "ResidentBob", str(start_blob_file), "http://127.0.0.1:8770",
                "--why=Equals syntax check",
                "--dry-run",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_cs.returncode, 0)
        self.assertIn("why: Equals syntax check", proc_cs.stdout)

        json_start = proc_cs.stdout.find("{")
        intro_data = json.loads(proc_cs.stdout[json_start:])
        self.assertEqual(intro_data["why"], "Equals syntax check")
        verify_key_intro(intro_data)

    def test_countersign_missing_why_arg(self):
        start_blob_file = self.fake_home / "intro_start4.json"
        subprocess.run(
            [
                sys.executable, "agora_introduce.py", "start",
                "VisitorAlice", "TestHouse", self.resident.key_id,
                "--out", str(start_blob_file),
            ],
            cwd=str(self.repo_root),
            env=self.env,
            check=True,
        )

        proc = subprocess.run(
            [
                sys.executable, "agora_introduce.py", "countersign",
                "ResidentBob", str(start_blob_file), "http://127.0.0.1:8770",
                "--why",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: --why requires an argument", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
