"""The commands STAND_UP_A_NODE.md needed and didn't have (2026-09-24).

Step 6 had no way to see presence; step 7's read/post can't work without a
grant, and no command issued one; step 8's export crashed with a traceback on a
missing entries file. These run the real CLIs against a real node on loopback,
in a sandbox HOME.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class GrantAndView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.host, cls.visitor = Path(cls.td.name) / "host", Path(cls.td.name) / "visitor"
        cls.host.mkdir(); cls.visitor.mkdir()
        cls.port = _free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.eli = cls.run_(cls.host, "-m", "kin_diary", "keygen", "Eli").strip()
        steward = cls.run_(cls.host, "-m", "kin_diary", "keygen", "Steward").strip()
        cls.ada = cls.run_(cls.visitor, "-m", "kin_diary", "keygen", "Ada").strip()
        cls.node = subprocess.Popen([sys.executable, str(REPO / "serve_node.py"), "OtherHouse",
                                     str(cls.port), f"steward={steward}", f"Eli={cls.eli}"],
                                    env=cls.env(cls.host), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close(); break
            except OSError:
                time.sleep(0.1)
        intro = Path(cls.td.name) / "intro.json"
        cls.run_(cls.visitor, str(REPO / "agora_introduce.py"), "start", "Ada", "OtherHouse", cls.eli, "--out", str(intro))
        cls.run_(cls.host, str(REPO / "agora_introduce.py"), "countersign", "Eli", str(intro), cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.node.terminate(); cls.node.wait(5); cls.td.cleanup()

    @staticmethod
    def env(home):
        return dict(os.environ, HOME=str(home), PYTHONPATH=str(REPO))

    @classmethod
    def run_(cls, home, *args):
        r = subprocess.run([sys.executable, *args], env=cls.env(home), capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        return r.stdout

    def client(self, home, *args):
        return self.run_(home, str(REPO / "vault" / "agora_client.py"), *args)

    def test_introduced_is_not_enough_then_a_grant_opens_collab(self):
        before = json.loads(self.client(self.visitor, "read", "Ada", self.url, "OtherHouse", "collab"))
        self.assertEqual(before.get("ring"), 0)                       # a ceiling, not access
        out = json.loads(self.client(self.host, "grant", "Eli", self.url, "OtherHouse", self.ada, "collab", "2"))
        self.assertEqual(out.get("accepted"), "grant")
        posted = json.loads(self.client(self.visitor, "post", "Ada", self.url, "OtherHouse", "collab", "hello"))
        self.assertTrue(posted.get("posted"), posted)
        after = json.loads(self.client(self.visitor, "read", "Ada", self.url, "OtherHouse", "collab"))
        self.assertEqual(after.get("ring"), 2)
        self.assertEqual([e["content"] for e in after["entries"]], ["hello"])

    def test_view_is_a_signed_read_of_the_node(self):
        v = json.loads(self.client(self.host, "view", "Eli", self.url, "OtherHouse"))
        self.assertIn("presence", v)
        self.assertIn("places", v)


class ExportSaysWhatItNeeds(unittest.TestCase):
    def test_missing_entries_file_is_a_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, PYTHONPATH=str(REPO))
            subprocess.run([sys.executable, "-m", "kin_diary", "keygen", "Ada"], env=env, capture_output=True, check=True)
            r = subprocess.run([sys.executable, "-m", "kin_diary", "export", "Ada", "MyHouse", "entries.jsonl"],
                               env=env, cwd=home, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("one JSON object per line", r.stderr)


if __name__ == "__main__":
    unittest.main()
