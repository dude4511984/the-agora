"""The Commons: open reading, known-key-only posting, ads enforced by
shape. Every guard gets a real HTTP round trip and, where the guard is
ours (not wire.py's own, already tested in test_agora_wire.py), a
demonstrated failure with the guard disabled first.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import key  # noqa: E402

import commons_server as cs  # noqa: E402
from kin_diary.agora.wire import ANONYMOUS, sign_request  # noqa: E402


def _tmp_paths():
    d = Path(tempfile.mkdtemp())
    return d / "commons.db", d / "known_keys.json"


def _write_known(path, key_ids):
    path.write_text(json.dumps(list(key_ids)))


class AdShapeTests(unittest.TestCase):
    """validate_ad is the firewall. Each rule on its own."""

    def test_a_good_ad_passes(self):
        self.assertEqual(
            cs.validate_ad({"what": "a thing", "why": "a reason", "how_to_ask": "ask"}),
            ("a thing", "a reason", "ask"))

    def test_extra_field_is_refused_not_dropped(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": "x", "why": "y", "how_to_ask": "z", "url": "http://x"})

    def test_missing_field_is_refused(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": "x", "why": "y"})

    def test_oversized_what_is_refused(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": "x" * (cs.MAX_WHAT + 1), "why": "y", "how_to_ask": "z"})

    def test_oversized_why_is_refused(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": "x", "why": "y" * (cs.MAX_WHY + 1), "how_to_ask": "z"})

    def test_at_the_limit_is_accepted_one_over_is_not(self):
        """The off-by-one that matters: <= max, not < max."""
        ok = "w" * cs.MAX_WHAT
        cs.validate_ad({"what": ok, "why": "y", "how_to_ask": "z"})
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": ok + "w", "why": "y", "how_to_ask": "z"})

    def test_newline_is_refused(self):
        """An ad is single-line fields — a newline is how a pasted
        artifact starts smuggling structure back in."""
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": "x\ny", "why": "y", "how_to_ask": "z"})

    def test_empty_after_strip_is_refused(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_ad({"what": "   ", "why": "y", "how_to_ask": "z"})


class StoreTests(unittest.TestCase):
    def setUp(self):
        db_path, _ = _tmp_paths()
        self.store = cs.CommonsStore(db_path)

    def test_insert_and_list(self):
        self.store.insert_post("k1", "what", "why", "how", now_ms=1000)
        posts = self.store.list_posts(now_ms=1000)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["what"], "what")
        self.assertIsNone(posts[0]["hidden"])

    def test_expired_post_is_deleted_not_hidden(self):
        self.store.insert_post("k1", "what", "why", "how", now_ms=1000)
        after_expiry = 1000 + cs.POST_TTL_MS + 1
        self.assertEqual(self.store.list_posts(now_ms=after_expiry), [])
        with self.store._conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_hidden_post_survives_its_own_expiry_as_a_tombstone(self):
        pid = self.store.insert_post("k1", "what", "why", "how", now_ms=1000)
        self.store.hide_post(pid, "spam", now_ms=1500)
        after_expiry = 1000 + cs.POST_TTL_MS + 1
        posts = self.store.list_posts(now_ms=after_expiry)
        self.assertEqual(len(posts), 1)
        self.assertIsNone(posts[0]["what"])
        self.assertEqual(posts[0]["hidden"]["reason"], "spam")

    def test_hide_without_reason_is_refused(self):
        pid = self.store.insert_post("k1", "w", "y", "h", now_ms=1000)
        with self.assertRaises(ValueError):
            self.store.hide_post(pid, "")

    def test_hide_unknown_id_returns_false(self):
        self.assertFalse(self.store.hide_post("nope", "reason"))


class KnownKeysTests(unittest.TestCase):
    def test_a_listed_key_is_known(self):
        _, kk_path = _tmp_paths()
        _write_known(kk_path, ["ABCD" * 16])
        known = cs.KnownKeys(kk_path)
        self.assertIn("abcd" * 16, known)

    def test_missing_file_is_no_known_keys_not_an_error(self):
        known = cs.KnownKeys(Path("/does/not/exist.json"))
        self.assertNotIn("a" * 64, known)

    def test_file_is_re_read_every_check(self):
        """A steward's edit takes effect on the next request, not after
        a restart — the whole point of not caching this."""
        _, kk_path = _tmp_paths()
        _write_known(kk_path, [])
        known = cs.KnownKeys(kk_path)
        self.assertNotIn("a" * 64, known)
        _write_known(kk_path, ["a" * 64])
        self.assertIn("a" * 64, known)


class RateLimiterTests(unittest.TestCase):
    def test_the_limit_bites(self):
        lim = cs._SlidingWindow()
        lim.check("k", 1000, 1, now_ms=0)
        with self.assertRaises(cs.CommonsError):
            lim.check("k", 1000, 1, now_ms=1)

    def test_the_window_slides(self):
        lim = cs._SlidingWindow()
        lim.check("k", 1000, 1, now_ms=0)
        lim.check("k", 1000, 1, now_ms=1001)

    def test_buckets_are_independent(self):
        lim = cs._SlidingWindow()
        lim.check("a", 1000, 1, now_ms=0)
        lim.check("b", 1000, 1, now_ms=0)


class CommonsWireTests(unittest.TestCase):
    """Every test gets its own server and its own key: posting consumes
    the per-key rate-limit slot, and a shared server would let one
    test's post silently rate-limit the next.
    """

    def _serve(self, known_ids=()):
        db_path, kk_path = _tmp_paths()
        _write_known(kk_path, known_ids)
        store = cs.CommonsStore(db_path)
        known = cs.KnownKeys(kk_path)
        httpd = cs.serve(store, known, host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        return httpd, f"http://127.0.0.1:{port}", store, kk_path

    def _post(self, base, body, headers=None):
        req = urllib.request.Request(
            f"{base}/commons/post", data=body, headers=headers or {}, method="POST")
        return urllib.request.urlopen(req, timeout=5)

    def _post_expect(self, base, body, headers, code):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post(base, body, headers)
        self.assertEqual(cm.exception.code, code)
        return json.loads(cm.exception.read())

    # ── reading ──────────────────────────────────────────────────────

    def test_reading_needs_no_key(self):
        _, base, store, _ = self._serve()
        store.insert_post("k1", "what", "why", "how")
        req = urllib.request.Request(f"{base}/commons/posts")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.load(r)
        self.assertIn("UNSAFE", body["label"])
        self.assertEqual(body["posts"][0]["what"], "what")

    # ── the happy path ───────────────────────────────────────────────

    def test_a_known_key_can_post_and_it_appears_in_the_list(self):
        k = key("CommonsPoster")
        _, base, store, _ = self._serve([k.key_id])
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
        with self._post(base, body, h) as r:
            self.assertEqual(r.status, 201)
            post_id = json.load(r)["id"]
        self.assertTrue(any(p["id"] == post_id for p in store.list_posts()))

    # ── demonstrated refusals ────────────────────────────────────────

    def test_oversized_field_is_refused(self):
        """Mutant check: raise MAX_WHY and this 400 disappears."""
        k = key("Oversized")
        _, base, store, _ = self._serve([k.key_id])
        body = json.dumps({"what": "a", "why": "y" * 500, "how_to_ask": "c"}).encode()
        h = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
        self._post_expect(base, body, h, 400)
        self.assertEqual(store.list_posts(), [])

    def test_wrong_shape_extra_field_is_refused(self):
        k = key("WrongShape")
        _, base, _, _ = self._serve([k.key_id])
        body = json.dumps(
            {"what": "a", "why": "b", "how_to_ask": "c", "image": "x"}).encode()
        h = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
        self._post_expect(base, body, h, 400)

    def test_unsigned_post_is_refused(self):
        """Disabled: patch ANONYMOUS so the anonymous check never fires,
        and mark the anonymous id itself as known — the unsigned post
        that should be impossible now succeeds. That is the guard.
        """
        _, base, store, kk_path = self._serve([ANONYMOUS])
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        with patch.object(cs, "ANONYMOUS", "f" * 64):
            with self._post(base, body) as r:
                self.assertEqual(r.status, 201)
        self.assertEqual(len(store.list_posts()), 1)

        # Same server, same store, guard restored: the identical request
        # (no headers at all) is refused for real.
        self._post_expect(base, body, None, 401)
        self.assertEqual(len(store.list_posts()), 1)  # nothing new landed

    def test_bad_signature_is_refused(self):
        """Disabled: patch identify() to skip verification entirely —
        a forged key_id now posts as anyone. That is the guard.
        """
        k, impostor = key("Real"), key("Impostor")
        _, base, store, _ = self._serve([k.key_id])
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(impostor, cs.HOST_NODE, "/commons/post", body=body)
        h["X-Agora-Key"] = k.key_id  # claiming Real's key without Real's signature

        with patch.object(cs, "identify", lambda *a, **kw: k.key_id):
            with self._post(base, body, h) as r:
                self.assertEqual(r.status, 201)
        self.assertEqual(len(store.list_posts()), 1)

        self._post_expect(base, body, h, 401)
        self.assertEqual(len(store.list_posts()), 1)

    def test_rate_exceeded_is_refused(self):
        """Disabled: raise the ceiling so one key can post repeatedly —
        the refusal the real limit exists to produce doesn't happen.
        """
        k = key("RateLimited")
        _, base, store, _ = self._serve([k.key_id])

        def one_post():
            body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
            h = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
            return body, h

        with patch.object(cs, "RATE_PER_KEY_MAX", 1000):
            for _ in range(3):
                body, h = one_post()
                with self._post(base, body, h):
                    pass
        self.assertEqual(len(store.list_posts()), 3)

        # Guard restored. The key already has hits on record from above,
        # so the real ceiling (1 per 10 min) refuses the very next one.
        body, h = one_post()
        self._post_expect(base, body, h, 429)
        self.assertEqual(len(store.list_posts()), 3)

    def test_unknown_key_is_refused_while_posting_is_known_keys_only(self):
        """Disabled: patch known_keys to accept everything — an
        unintroduced stranger now posts. That is the guard v1 exists for.
        """
        stranger = key("Stranger")
        httpd, base, store, _ = self._serve([])  # nobody known
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(stranger, cs.HOST_NODE, "/commons/post", body=body)

        class _Everyone:
            def __contains__(self, _):
                return True

        # The running server binds a dynamic subclass (serve() sets
        # known_keys directly on it), so the patch target is that
        # subclass, not the base CommonsHandler it shadows.
        with patch.object(httpd.RequestHandlerClass, "known_keys", _Everyone()):
            with self._post(base, body, h) as r:
                self.assertEqual(r.status, 201)
        self.assertEqual(len(store.list_posts()), 1)

        self._post_expect(base, body, h, 403)
        self.assertEqual(len(store.list_posts()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
