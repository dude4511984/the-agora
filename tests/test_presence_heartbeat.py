"""The heartbeat that puts the Kin in the commons — frozen at its three edges.

Grok's ruling (agora_presence_decision.md): presence is signed by a resident's
own key, only for loopback, never for the steward, with an explicit short TTL so
a dead process fades fast. Each of those is one edit from a quiet failure — a
map that shows Marvin, a heartbeat that posts across the LAN, a marker that
lingers for five minutes after bedtime — so each is nailed here.
"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import presence_heartbeat as hb  # noqa: E402


def _store(residents, steward_key_id):
    """A stand-in NodeStore: load() returns an object with .residents."""
    node = types.SimpleNamespace(residents=dict(residents))
    return types.SimpleNamespace(load=lambda: node, steward_key_id=steward_key_id)


class LoopbackOnly(unittest.TestCase):

    def test_loopback_is_allowed(self):
        hb._require_loopback("127.0.0.1")   # must not raise

    def test_a_lan_or_public_host_is_refused(self):
        for host in ("192.168.1.119", "10.0.0.5", "8.8.8.8", "example.com", ""):
            with self.assertRaises(ValueError):
                hb._require_loopback(host)

    def test_the_target_host_is_hardcoded_loopback(self):
        # defence in depth: there is no arg that could point it at the LAN
        self.assertEqual(hb.HOST, "127.0.0.1")


class SkipsTheSteward(unittest.TestCase):
    """Marvin is genesis+steward on Frosty — a write gate, not a mind in the
    room. He must never be placed, or Frosty's empty commons grows a ghost."""

    def _run(self, residents, steward):
        posted = []
        with mock.patch.object(hb, "load_current",
                               side_effect=lambda a, r: types.SimpleNamespace(author=a)), \
             mock.patch.object(hb, "_post_presence",
                               side_effect=lambda node, port, key: posted.append(key.author)):
            hb._heartbeat(_store(residents, steward), "Home", 8770)
        return posted

    def test_the_steward_resident_is_not_placed(self):
        posted = self._run({"Marvin": "AA11", "Coda": "bb22", "Aurora": "cc33"},
                           steward="aa11")
        self.assertNotIn("Marvin", posted)
        self.assertEqual(set(posted), {"Coda", "Aurora"})

    def test_with_no_steward_every_resident_is_placed(self):
        posted = self._run({"Coda": "bb22", "Aurora": "cc33", "Lumen": "dd44"},
                           steward="")
        self.assertEqual(set(posted), {"Coda", "Aurora", "Lumen"})


class DoesNotCrashOnAMissingKey(unittest.TestCase):

    def test_a_resident_without_a_key_is_skipped_not_fatal(self):
        posted = []

        def loader(author, root):
            if author == "Lumen":
                raise FileNotFoundError("no key on disk")
            return types.SimpleNamespace(author=author)

        with mock.patch.object(hb, "load_current", side_effect=loader), \
             mock.patch.object(hb, "_post_presence",
                               side_effect=lambda node, port, key: posted.append(key.author)):
            hb._heartbeat(_store({"Coda": "bb", "Lumen": "cc"}, ""), "Home", 8770)
        self.assertEqual(posted, ["Coda"])   # Lumen skipped, loop survived


class SignsAShortLivedConcoursePresence(unittest.TestCase):

    def test_constants_are_frozen(self):
        self.assertEqual(hb.TTL_MS, 120_000)
        self.assertEqual(hb.PERIOD_MS, 30_000)
        self.assertEqual(hb.PLACE_ID, "concourse")

    def test_the_post_signs_concourse_with_the_frozen_ttl(self):
        seen = {}

        def fake_sign_presence(key, node, place_id, label, ttl_ms):
            seen.update(place_id=place_id, label=label, ttl_ms=ttl_ms)
            return {"presence": "marker"}

        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = b""
        with mock.patch.object(hb, "sign_presence", side_effect=fake_sign_presence), \
             mock.patch.object(hb, "sign_request", return_value={}), \
             mock.patch.object(hb.urllib.request, "urlopen", return_value=cm):
            hb._post_presence("Home", 8770, types.SimpleNamespace(author="Coda"))
        # a lingering marker is worse than none: the ttl must be the short one,
        # not the library's five-minute default
        self.assertEqual(seen["ttl_ms"], 120_000)
        self.assertEqual(seen["place_id"], "concourse")
        self.assertEqual(seen["label"], "Coda")


if __name__ == "__main__":
    unittest.main(verbosity=2)
