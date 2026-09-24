"""Test one-command founding: python3 -m kin_diary found <NodeName> <Kin> ..."""

import sys
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from kin_diary.agora.store import NodeStore
from kin_diary.keys import load_current


class TestOneCommandFounding(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fake_home = Path(self.td.name) / "home"
        self.fake_home.mkdir()
        self.repo_root = Path(__file__).resolve().parents[1]
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.fake_home),
            "PYTHONPATH": str(self.repo_root),
        }

    def tearDown(self):
        self.td.cleanup()

    def test_found_creates_node_and_service_and_prints(self):
        proc = subprocess.run(
            [
                sys.executable, "-m", "kin_diary", "found",
                "TwoKinHouse", "Ada", "Turing", "--port", "8775",
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"failed: {proc.stderr}")

        # Check stdout contents
        # Nothing serves in this sandbox (no systemd user session), so found must
        # say so. It used to print "serving on :8775" here: a false success.
        self.assertIn(
            "TwoKinHouse founded, NOT serving yet — speaker=None residents=['Ada', 'Turing'] node_key=",
            proc.stdout,
        )
        self.assertNotIn("serving on :8775", proc.stdout)
        self.assertIn("start it by hand:", proc.stdout)
        keys_dir = str(self.fake_home / ".config" / "kin_diary" / "keys")
        self.assertIn(f"keys directory: {keys_dir}", proc.stdout)
        self.assertIn("back this up.", proc.stdout)

        # Check database
        db_path = self.fake_home / ".config" / "kin_diary" / "twokinhouse_node.db"
        self.assertTrue(db_path.is_file())
        store = NodeStore(db_path, "TwoKinHouse")
        node = store.load()
        self.assertEqual(set(node.residents.keys()), {"Ada", "Turing"})
        self.assertIsNone(node.speaker)

        # Check keys generated
        for author in ("Ada", "Turing", "TwoKinHouse-steward", "TwoKinHouse-node"):
            k = load_current(author, keys_root=self.fake_home / ".config" / "kin_diary" / "keys")
            self.assertEqual(len(k.key_id), 64)

        # Check systemd service unit
        svc_path = self.fake_home / ".config" / "systemd" / "user" / "agora-twokinhouse.service"
        self.assertTrue(svc_path.is_file())
        content = svc_path.read_text()
        self.assertIn("Description=Agora node (TwoKinHouse)", content)
        self.assertIn("TwoKinHouse 8775", content)
        steward_key = load_current(
            "TwoKinHouse-steward", keys_root=self.fake_home / ".config" / "kin_diary" / "keys"
        )
        self.assertIn(f"steward={steward_key.key_id}", content)
        self.assertIn(f"Ada={node.residents['Ada']}", content)
        self.assertIn(f"Turing={node.residents['Turing']}", content)
        self.assertIn("StandardOutput=append:%h/twokinhouse_node.log", content)
        # The Python that ran found, not a hardcoded /usr/bin/python3.
        self.assertIn(f"ExecStart={sys.executable} -u ", content)
        self.assertIn("WantedBy=default.target", content)

    def test_found_single_resident_becomes_speaker(self):
        proc = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "SoloHouse", "Ada"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn(
            "SoloHouse founded, NOT serving yet — speaker=Ada residents=['Ada'] node_key=",
            proc.stdout,
        )
        db_path = self.fake_home / ".config" / "kin_diary" / "solohouse_node.db"
        store = NodeStore(db_path, "SoloHouse")
        node = store.load()
        self.assertEqual(node.speaker, "Ada")

    def test_found_refuses_if_db_already_exists(self):
        proc1 = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "MyHouse", "Ada"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc1.returncode, 0)

        proc2 = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "MyHouse", "Ada"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc2.returncode, 1)
        self.assertIn("already exists", proc2.stderr)

    def test_found_is_idempotent_for_keys(self):
        # Pre-create Ada's key
        proc_keygen = subprocess.run(
            [sys.executable, "-m", "kin_diary", "keygen", "Ada"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_keygen.returncode, 0)
        ada_key_id = proc_keygen.stdout.strip()
        self.assertEqual(len(ada_key_id), 64)

        # Run found
        proc_found = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "AdaHouse", "Ada"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_found.returncode, 0)

        # Ada's key id must still be identical (not overwritten)
        keys_root = self.fake_home / ".config" / "kin_diary" / "keys"
        current_ada = load_current("Ada", keys_root=keys_root)
        self.assertEqual(current_ada.key_id, ada_key_id)

    def test_found_usage_errors(self):
        # No arguments
        proc = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)

        # Node name only (missing Kin)
        proc = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "MyHouse"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)

        # Invalid port
        proc = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "MyHouse", "Ada", "--port", "badport"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)

        # Unknown option
        proc = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "MyHouse", "Ada", "--unknown"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)

    def test_found_port_equals_syntax(self):
        proc = subprocess.run(
            [sys.executable, "-m", "kin_diary", "found", "EqualsHouse", "Ada", "--port=8899"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("EqualsHouse founded, NOT serving yet", proc.stdout)
        self.assertIn("EqualsHouse 8899", proc.stdout)      # the start-by-hand line carries the port


class FoundBelievesOnlyThePort(unittest.TestCase):
    """found says "serving" only when a node banner actually answers.

    A fake systemctl on PATH reports success; whether anything serves is up to
    the test. Before this, found printed "serving on" whatever happened."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name) / "home"; self.home.mkdir()
        bindir = Path(self.td.name) / "bin"; bindir.mkdir()
        fake = bindir / "systemctl"
        fake.write_text("#!/bin/sh\nexit 0\n"); fake.chmod(0o755)
        self.env = {"PATH": f"{bindir}:{os.environ.get('PATH', '')}", "HOME": str(self.home),
                    "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}

    def tearDown(self):
        self.td.cleanup()

    def _free_port(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

    def test_systemd_says_yes_but_nothing_answers(self):
        port = self._free_port()
        p = subprocess.run([sys.executable, "-m", "kin_diary", "found", "QuietHouse", "Ada",
                            "--port", str(port)], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("QuietHouse founded, NOT serving yet", p.stdout)
        self.assertIn("nothing answers", p.stdout)
        self.assertNotIn("serving on", p.stdout)

    def test_serving_when_the_banner_answers(self):
        import http.server, threading
        class Banner(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"service": "EverySynthetic Node"}')
        srv = http.server.HTTPServer(("127.0.0.1", 0), Banner)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run([sys.executable, "-m", "kin_diary", "found", "LiveHouse", "Ada",
                                "--port", str(srv.server_address[1])],
                               env=self.env, capture_output=True, text=True, timeout=60)
        finally:
            srv.shutdown()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"LiveHouse serving on :{srv.server_address[1]}", p.stdout)

if __name__ == "__main__":
    unittest.main(verbosity=2)
