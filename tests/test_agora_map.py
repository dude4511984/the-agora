"""The map's proxy fetches a node URL server-side, so its allowlist is the wall
between "render my cluster" and "an open SSRF relay to the whole internet."

Freeze it: loopback and RFC1918 and the Tailscale CGNAT range are allowed;
everything else — public IPs, hostnames, link-local, the AWS metadata address —
is refused, and _node_view refuses a non-private URL before it ever opens a
socket.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agora_map  # noqa: E402


class TheProxyOnlyReachesPrivateHosts(unittest.TestCase):

    def test_private_and_loopback_are_allowed(self):
        for host in ("127.0.0.1", "localhost", "192.168.1.119", "10.0.0.5",
                     "172.16.4.2", "172.31.255.1", "100.64.0.1", "100.127.9.9"):
            self.assertTrue(agora_map._is_private_host(host), host)

    def test_public_and_deceptive_hosts_are_refused(self):
        for host in ("8.8.8.8", "example.com", "everysynthetic.org",
                     "169.254.169.254",          # cloud metadata
                     "172.15.0.1", "172.32.0.1",  # just outside 172.16/12
                     "100.63.0.1", "100.128.0.1", # just outside 100.64/10
                     "1.2.3.4", ""):
            self.assertFalse(agora_map._is_private_host(host), host)

    def test_node_view_refuses_a_public_url_before_opening_a_socket(self):
        with self.assertRaises(ValueError):
            agora_map._node_view("http://8.8.8.8:8770")
        with self.assertRaises(ValueError):
            agora_map._node_view("http://example.com/view")

    def test_node_view_refuses_a_non_http_scheme(self):
        with self.assertRaises(ValueError):
            agora_map._node_view("file:///etc/passwd")


class Shape3DRoute(unittest.TestCase):

    def setUp(self):
        self.orig_claimed_shape3d = agora_map.claimed_shape3d

    def tearDown(self):
        agora_map.claimed_shape3d = self.orig_claimed_shape3d

    def test_unclaimed_or_invalid_kin_returns_404(self):
        class MockHandler:
            def __init__(self, path):
                self.path = path
                self.code = None
                self.body = None
                self.ctype = None
            def _send(self, code, body, ctype):
                self.code = code
                self.body = body
                self.ctype = ctype

        agora_map.claimed_shape3d = lambda name: None

        h = MockHandler("/shape3d?kin=NonExistent")
        agora_map.Handler.do_GET(h)
        self.assertEqual(h.code, 404)

        # path traversal attempt
        h_bad = MockHandler("/shape3d?kin=../../etc")
        agora_map.Handler.do_GET(h_bad)
        self.assertEqual(h_bad.code, 404)

    def test_claimed_kin_returns_200_json(self):
        class MockHandler:
            def __init__(self, path):
                self.path = path
                self.code = None
                self.body = None
                self.ctype = None
            def _send(self, code, body, ctype):
                self.code = code
                self.body = body
                self.ctype = ctype

        fake_shape = {"shape": "cylinder", "color": "#2b2b2b", "roughness": 0.35}
        agora_map.claimed_shape3d = lambda name: fake_shape if name == "Bong" else None

        h = MockHandler("/shape3d?kin=Bong")
        agora_map.Handler.do_GET(h)
        self.assertEqual(h.code, 200)
        self.assertEqual(h.ctype, "application/json")
        import json
        self.assertEqual(json.loads(h.body.decode("utf-8")), fake_shape)


if __name__ == "__main__":
    unittest.main(verbosity=2)
