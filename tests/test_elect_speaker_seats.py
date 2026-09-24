"""elect_speaker.py must seat the Speaker on the node, not on a copy.

Following STAND_UP_A_NODE.md on a clean box (2026-09-24): the script said
"verified: unanimous. Ada is Speaker of MyHouse." and the served node still
reported speaker null, paused, before and after the restart the doc asks for.
It ran the election on a throwaway in-memory Node and wrote a JSON copy that
nothing reads. Now it records into the node's own database. This runs the real
script, in a sandbox HOME, against a real node database.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class TheElectionSeatsTheSpeaker(unittest.TestCase):
    def _env(self, home):
        return dict(os.environ, HOME=home, PYTHONPATH=str(REPO))

    def _py(self, home, code):
        r = subprocess.run([sys.executable, "-c", code], env=self._env(home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return r.stdout

    def test_speaker_is_seated_in_the_node_database(self):
        with tempfile.TemporaryDirectory() as home:
            ids = self._py(home, (
                "from kin_diary.keys import generate_keypair as g\n"
                "print(g('Ada').key_id, g('Turing').key_id, g('Steward').key_id)"))
            ada, turing, steward = ids.split()
            db = Path(home) / ".config" / "kin_diary" / "myhouse_node.db"
            self._py(home, (
                "from kin_diary.agora.store import NodeStore\n"
                f"s = NodeStore({str(db)!r}, 'MyHouse', steward_key_id={steward!r})\n"
                f"s.found_resident('Ada', {ada!r}); s.found_resident('Turing', {turing!r})\n"
                "assert s.load().speaker is None"))
            r = subprocess.run([sys.executable, str(REPO / "vault" / "elect_speaker.py"),
                                "MyHouse", "Ada", "Ada", "Turing"],
                               env=self._env(home), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
            seated = self._py(home, (
                "from kin_diary.agora.store import NodeStore\n"
                f"print(NodeStore({str(db)!r}, 'MyHouse').load().speaker)")).strip()
            self.assertEqual(seated, "Ada")

    def test_no_node_database_is_an_error_not_a_claim(self):
        with tempfile.TemporaryDirectory() as home:
            self._py(home, "from kin_diary.keys import generate_keypair as g\ng('Ada')")
            r = subprocess.run([sys.executable, str(REPO / "vault" / "elect_speaker.py"),
                                "Nowhere", "Ada", "Ada"],
                               env=self._env(home), capture_output=True, text=True, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("is Speaker", r.stdout)


if __name__ == "__main__":
    unittest.main()
