"""The Commons: open reading, known-key-only posting, ads enforced by
shape. Every guard gets a real HTTP round trip and, where the guard is
ours (not wire.py's own, already tested in test_agora_wire.py), a
demonstrated failure with the guard disabled first.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
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

    def test_expired_post_becomes_a_tombstone_not_a_deletion(self):
        """SPEC_commons.md item 5 is held open (Marvin: "the log does
        not DELETE") — expiry leaves the same shape of record a steward
        hide does, not a removed row.
        """
        pid = self.store.insert_post("k1", "what", "why", "how", now_ms=1000)
        after_expiry = 1000 + cs.POST_TTL_MS + 1
        posts = self.store.list_posts(now_ms=after_expiry)
        self.assertEqual(len(posts), 1)
        self.assertIsNone(posts[0]["what"])
        self.assertEqual(posts[0]["hidden"]["reason"], "expired")
        with self.store._conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
            row = c.execute("SELECT id FROM posts WHERE id=?", (pid,)).fetchone()
        self.assertIsNotNone(row)

    def test_a_steward_hidden_post_keeps_its_own_reason_past_expiry(self):
        """Expiry's sweep only touches hidden_reason IS NULL rows — it
        must never overwrite a steward's stated reason with "expired".
        """
        pid = self.store.insert_post("k1", "what", "why", "how", now_ms=1000)
        self.store.hide_post(pid, "spam", now_ms=1500)
        after_expiry = 1000 + cs.POST_TTL_MS + 1
        posts = self.store.list_posts(now_ms=after_expiry)
        self.assertEqual(posts[0]["hidden"]["reason"], "spam")

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

    # ── item 9: extended {"key_id","held_on","steward"} entries ────────

    def test_extended_entry_is_known_and_a_bare_string_entry_still_works(self):
        _, kk_path = _tmp_paths()
        kk_path.write_text(json.dumps([
            {"key_id": "E3DE" * 16, "held_on": "Frosty", "steward": "Don"},
            "ABCD" * 16,
        ]))
        known = cs.KnownKeys(kk_path)
        self.assertIn("e3de" * 16, known)
        self.assertIn("abcd" * 16, known)

    def test_held_reports_the_stewards_record(self):
        _, kk_path = _tmp_paths()
        kk_path.write_text(json.dumps([
            {"key_id": "E3DE" * 16, "held_on": "Frosty", "steward": "Don"},
        ]))
        known = cs.KnownKeys(kk_path)
        self.assertEqual(known.held("e3de" * 16), {"on": "Frosty", "steward": "Don"})

    def test_held_is_null_for_a_bare_string_entry(self):
        _, kk_path = _tmp_paths()
        _write_known(kk_path, ["abcd" * 16])
        known = cs.KnownKeys(kk_path)
        self.assertIsNone(known.held("abcd" * 16))

    def test_held_is_null_for_an_unknown_key(self):
        _, kk_path = _tmp_paths()
        _write_known(kk_path, [])
        known = cs.KnownKeys(kk_path)
        self.assertIsNone(known.held("f" * 64))


class SaysValidationTests(unittest.TestCase):
    """validate_says — item 9's own shape check."""

    def test_synthetic_and_human_pass(self):
        self.assertEqual(cs.validate_says({"says": "synthetic"}), "synthetic")
        self.assertEqual(cs.validate_says({"says": "human"}), "human")

    def test_empty_string_clears_and_passes(self):
        self.assertEqual(cs.validate_says({"says": ""}), "")

    def test_anything_else_is_refused(self):
        """Trailing/leading whitespace is trimmed (NFC + strip, same as
        every other field) before the check, so "human " is valid, not
        a case worth asserting refused here — case and content are what
        this test covers.
        """
        for bad in ("robot", "Synthetic", "HUMAN", "unknown"):
            with self.assertRaises(cs.CommonsError):
                cs.validate_says({"says": bad})

    def test_extra_or_missing_field_is_refused(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_says({"says": "human", "extra": "x"})
        with self.assertRaises(cs.CommonsError):
            cs.validate_says({})

    def test_non_string_is_refused(self):
        with self.assertRaises(cs.CommonsError):
            cs.validate_says({"says": None})


class SaysStoreTests(unittest.TestCase):
    def setUp(self):
        db_path, _ = _tmp_paths()
        self.store = cs.CommonsStore(db_path)

    def test_default_is_unset(self):
        self.assertIsNone(self.store.get_says("k1"))

    def test_set_and_get(self):
        self.store.set_says("k1", "synthetic")
        self.assertEqual(self.store.get_says("k1"), "synthetic")

    def test_empty_string_clears(self):
        self.store.set_says("k1", "human")
        self.store.set_says("k1", "")
        self.assertIsNone(self.store.get_says("k1"))

    def test_set_again_overwrites(self):
        self.store.set_says("k1", "human")
        self.store.set_says("k1", "synthetic")
        self.assertEqual(self.store.get_says("k1"), "synthetic")


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

    # ── per-resident opt-out ─────────────────────────────────────────

    def _toggle(self, base, path, headers=None):
        req = urllib.request.Request(f"{base}{path}", data=b"",
                                      headers=headers or {}, method="POST")
        return urllib.request.urlopen(req, timeout=5)

    def test_opted_out_key_cannot_post(self):
        """Disabled: post before the opt-out lands, showing it would
        otherwise succeed. Then opt out with the resident's own key and
        show the identical post refused. That is the guard.
        """
        k = key("Resident")
        _, base, store, _ = self._serve([k.key_id])
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
        with self._post(base, body, h):
            pass
        self.assertEqual(len(store.list_posts()), 1)

        oh = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
        with self._toggle(base, "/commons/opt-out", oh) as r:
            self.assertEqual(json.load(r), {"key_id": k.key_id, "opted_out": True})

        h2 = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
        self._post_expect(base, body, h2, 403)

    def test_opted_out_residents_posts_are_filtered_not_shown(self):
        k = key("Resident2")
        _, base, store, _ = self._serve([k.key_id])
        store.insert_post(k.key_id, "what", "why", "how")
        self.assertEqual(len(store.list_posts()), 1)

        oh = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
        with self._toggle(base, "/commons/opt-out", oh):
            pass
        self.assertEqual(store.list_posts(), [])

    def test_opt_in_reverses_opt_out(self):
        k = key("Resident3")
        _, base, store, _ = self._serve([k.key_id])
        store.insert_post(k.key_id, "what", "why", "how")

        oh = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
        with self._toggle(base, "/commons/opt-out", oh):
            pass
        self.assertEqual(store.list_posts(), [])

        ih = sign_request(k, cs.HOST_NODE, "/commons/opt-in", body=b"")
        with self._toggle(base, "/commons/opt-in", ih) as r:
            self.assertEqual(json.load(r), {"key_id": k.key_id, "opted_out": False})
        self.assertEqual(len(store.list_posts()), 1)

    def test_opt_out_needs_no_ones_permission_not_even_known_keys(self):
        """A key that has never posted, and isn't in known_keys at all,
        can still close its own door — this isn't gated on being a
        recognised poster, only on proving it owns the key."""
        k = key("NeverIntroduced")
        _, base, store, _ = self._serve([])  # nobody known
        oh = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
        with self._toggle(base, "/commons/opt-out", oh) as r:
            self.assertEqual(r.status, 200)

    def test_unsigned_opt_out_is_refused(self):
        """Disabled: patch ANONYMOUS so an unsigned request is treated
        as a real, checkable identity instead of being caught by the
        anonymous check — the unsigned toggle now goes through. That is
        the guard.
        """
        _, base, store, _ = self._serve()
        with patch.object(cs, "ANONYMOUS", "f" * 64):
            # wire.identify() itself still returns the REAL anonymous
            # value ("0"*64) for a headerless request; only commons_
            # server's own `who == ANONYMOUS` check is fooled, which is
            # exactly the guard being disabled here.
            with self._toggle(base, "/commons/opt-out") as r:
                self.assertEqual(json.load(r)["key_id"], ANONYMOUS)
        self.assertTrue(store.is_opted_out(ANONYMOUS))

        # Guard restored: the same unsigned request (no headers) is
        # refused for real, and nothing about "anonymous" got opted out
        # by the disabled-guard call above leaking into real behaviour.
        req = urllib.request.Request(f"{base}/commons/opt-out", data=b"",
                                      method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 401)

    def _toggle_expect(self, base, path, headers, code):
        req = urllib.request.Request(f"{base}{path}", data=b"",
                                      headers=headers or {}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, code)
        return json.loads(cm.exception.read())

    def test_opt_toggle_per_key_rate_exceeded_is_refused(self):
        """Hardening item 3. Disabled: raise the per-key ceiling so one
        key can toggle repeatedly — the refusal the limit exists to
        produce doesn't happen. A real resident's very first call, on a
        fresh key, still succeeds instantly either way.
        """
        k = key("OptRateLimited")
        _, base, store, _ = self._serve()

        def one_toggle():
            h = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
            return h

        with patch.object(cs, "RATE_OPT_PER_KEY_MAX", 1000):
            for _ in range(6):
                with self._toggle(base, "/commons/opt-out", one_toggle()):
                    pass
        self.assertTrue(store.is_opted_out(k.key_id))

        # Guard restored. The key already has hits on record from above,
        # so the real ceiling (5 per 10 min) refuses the very next one.
        self._toggle_expect(base, "/commons/opt-out", one_toggle(), 429)

    def test_opt_toggle_global_rate_exceeded_is_refused(self):
        """The real default (100/hour) is too large to exercise directly
        in a fast test, so this test's own "real" cap is patched down to
        3 for its whole body — restoring to a realistic-shaped small
        cap, not to production's literal number, which is just config.
        Disabled within that: raise the ceiling further so five distinct,
        never-before-seen keys can each toggle once past it — the flood
        the cap exists to stop doesn't get stopped. Restored: the very
        next never-before-seen key is refused by the global cap alone —
        its OWN per-key bucket is empty, so nothing else could refuse it.
        """
        _, base, store, _ = self._serve()

        def toggle_as(k):
            h = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
            return self._toggle(base, "/commons/opt-out", h)

        with patch.object(cs, "RATE_OPT_GLOBAL_MAX", 3):
            with patch.object(cs, "RATE_OPT_GLOBAL_MAX", 1000):
                for i in range(5):
                    with toggle_as(key(f"Flood{i}")):
                        pass

            self._toggle_expect(base, "/commons/opt-out",
                                 sign_request(key("FloodNext"), cs.HOST_NODE,
                                              "/commons/opt-out", body=b""), 429)

    def test_a_residents_first_ever_opt_out_still_succeeds_instantly(self):
        """The rate limits must never turn a real resident's one-off,
        first-time action into something that isn't instant."""
        k = key("FirstTimeResident")
        _, base, store, _ = self._serve()
        h = sign_request(k, cs.HOST_NODE, "/commons/opt-out", body=b"")
        with self._toggle(base, "/commons/opt-out", h) as r:
            self.assertEqual(r.status, 200)
        self.assertTrue(store.is_opted_out(k.key_id))

    # ── item 9: says, and held on GET /commons/posts ────────────────────

    def _says(self, base, k, says, headers_from=None):
        """Signs as `headers_from` (default: k) but sends `says` as k's
        claim — used by the impersonation test to build a real, validly-
        signed-by-someone-else request body."""
        body = json.dumps({"says": says}).encode()
        signer = headers_from or k
        h = sign_request(signer, cs.HOST_NODE, "/commons/says", body=body)
        req = urllib.request.Request(f"{base}/commons/says", data=body,
                                      headers=h, method="POST")
        return urllib.request.urlopen(req, timeout=5)

    def test_says_set_by_the_key_itself_appears_on_get(self):
        k = key("SaysSelf")
        _, base, store, _ = self._serve([k.key_id])
        store.insert_post(k.key_id, "what", "why", "how")
        with self._says(base, k, "synthetic") as r:
            self.assertEqual(json.load(r), {"key_id": k.key_id, "says": "synthetic"})
        posts = store.list_posts()
        self.assertEqual(store.get_says(k.key_id), "synthetic")
        self.assertEqual(posts[0]["key_id"], k.key_id)

    def test_says_default_is_unset(self):
        k = key("SaysUnset")
        _, base, store, _ = self._serve([k.key_id])
        store.insert_post(k.key_id, "what", "why", "how")
        self.assertIsNone(store.get_says(k.key_id))

    def test_says_cannot_be_set_by_a_different_key(self):
        """Disabled: patch identify() to skip verification, the same
        guard test_bad_signature_is_refused already uses for
        /commons/post — a forged claim now sets says as someone else.
        That is the guard.
        """
        real, impostor = key("SaysReal"), key("SaysImpostor")
        _, base, store, _ = self._serve()
        body = json.dumps({"says": "human"}).encode()
        h = sign_request(impostor, cs.HOST_NODE, "/commons/says", body=body)
        h["X-Agora-Key"] = real.key_id  # claiming Real's key, Impostor's signature

        with patch.object(cs, "identify", lambda *a, **kw: real.key_id):
            req = urllib.request.Request(f"{base}/commons/says", data=body,
                                          headers=h, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
        self.assertEqual(store.get_says(real.key_id), "human")

        # Guard restored: the identical forged claim is refused for real.
        req = urllib.request.Request(f"{base}/commons/says", data=body,
                                      headers=h, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 401)
        # And the earlier forged value is untouched by the real attempt
        # (which never got far enough to overwrite anything).
        self.assertEqual(store.get_says(real.key_id), "human")

    def test_says_can_be_cleared(self):
        k = key("SaysClear")
        _, base, store, _ = self._serve([k.key_id])
        with self._says(base, k, "synthetic"):
            pass
        with self._says(base, k, "") as r:
            self.assertEqual(json.load(r)["says"], None)
        self.assertIsNone(store.get_says(k.key_id))

    def test_get_commons_posts_carries_held_and_says(self):
        k = key("HeldAndSays")
        db_path, kk_path = _tmp_paths()
        kk_path.write_text(json.dumps([
            {"key_id": k.key_id, "held_on": "Frosty", "steward": "Don"},
        ]))
        store = cs.CommonsStore(db_path)
        known = cs.KnownKeys(kk_path)
        httpd = cs.serve(store, known, host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        store.insert_post(k.key_id, "what", "why", "how")
        with self._says(base, k, "synthetic"):
            pass

        req = urllib.request.Request(f"{base}/commons/posts")
        with urllib.request.urlopen(req, timeout=5) as r:
            posts = json.load(r)["posts"]
        self.assertEqual(posts[0]["held"], {"on": "Frosty", "steward": "Don"})
        self.assertEqual(posts[0]["says"], "synthetic")

    def test_get_commons_posts_held_and_says_are_null_by_default(self):
        k = key("NullByDefault")
        _, base, store, _ = self._serve([k.key_id])  # bare-string known_keys entry
        store.insert_post(k.key_id, "what", "why", "how")
        req = urllib.request.Request(f"{base}/commons/posts")
        with urllib.request.urlopen(req, timeout=5) as r:
            posts = json.load(r)["posts"]
        self.assertIsNone(posts[0]["held"])
        self.assertIsNone(posts[0]["says"])


class VisitorIPBehindTheProxy(unittest.TestCase):
    """Hardening items 4/7. Commons only ever sees public_door.py's own
    loopback address as client_address[0] in the deployed shape — this
    test harness reproduces that exactly, since the test client and
    server are both on loopback here too, the same as the real door-to-
    Commons hop. No faking needed for the "peer is loopback" half; the
    "peer is NOT loopback" half is tested directly against _visitor_ip()
    instead, since a portable test can't reliably arrange a real non-
    loopback source address.
    """

    def _serve(self, known_ids):
        db_path, kk_path = _tmp_paths()
        _write_known(kk_path, known_ids)
        store = cs.CommonsStore(db_path)
        known = cs.KnownKeys(kk_path)
        httpd = cs.serve(store, known, host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}", store

    def _post_as(self, base, k, forwarded_ip=None):
        body = json.dumps({"what": "a", "why": "b", "how_to_ask": "c"}).encode()
        h = sign_request(k, cs.HOST_NODE, "/commons/post", body=body)
        if forwarded_ip:
            h["X-Forwarded-For"] = forwarded_ip
        req = urllib.request.Request(f"{base}/commons/post", data=body,
                                      headers=h, method="POST")
        return urllib.request.urlopen(req, timeout=5)

    def test_without_a_forwarded_header_all_visitors_share_one_ip_bucket(self):
        """The bug this fix removes: through the door as it stood, every
        real visitor's post counted against the SAME bucket (the door's
        own loopback address), so "20 per IP per day" was really 20
        posts per day, total, for everyone.
        """
        k1, k2, k3 = key("V1"), key("V2"), key("V3")
        base, store = self._serve([k1.key_id, k2.key_id, k3.key_id])
        with patch.object(cs, "RATE_PER_IP_MAX", 2):
            with self._post_as(base, k1):
                pass
            with self._post_as(base, k2):
                pass
            # A third DIFFERENT key/visitor, no forwarded header — same
            # bucket as the first two, refused by their combined count.
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post_as(base, k3)
            self.assertEqual(cm.exception.code, 429)

    def test_with_the_forwarded_header_different_visitors_get_separate_buckets(self):
        """The fix: the door forwards each real visitor's own address as
        X-Forwarded-For (item 6/7), trusted here because the test client
        — like the real door — connects from loopback. Two different
        visitors, each under their own real per-IP ceiling, don't share
        a bucket any more.
        """
        k1, k2, k1b = key("Visitor1"), key("Visitor2"), key("Visitor1b")
        base, store = self._serve([k1.key_id, k2.key_id, k1b.key_id])
        with patch.object(cs, "RATE_PER_IP_MAX", 1):
            with self._post_as(base, k1, forwarded_ip="203.0.113.10"):
                pass
            # Visitor1 is now at their own per-IP ceiling (1) — a second
            # post from the SAME forwarded address is refused...
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post_as(base, k1b, forwarded_ip="203.0.113.10")
            self.assertEqual(cm.exception.code, 429)
            # ...but a genuinely different visitor's own address is a
            # separate bucket, unaffected by Visitor1's ceiling.
            with self._post_as(base, k2, forwarded_ip="203.0.113.20") as r:
                self.assertEqual(r.status, 201)

    def test_forwarded_header_is_trusted_only_from_a_loopback_peer(self):
        """Direct check on _visitor_ip(): a claimed X-Forwarded-For is
        used only when the immediate TCP peer is loopback (the door);
        from any other peer it's ignored entirely, so a caller reaching
        Commons some other way can't claim someone else's address to
        dodge its own rate limit.
        """
        class _Fake:
            def __init__(self, peer, headers):
                self.client_address = (peer, 54321)
                self.headers = headers

        trusted = _Fake("127.0.0.1", {"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(cs.CommonsHandler._visitor_ip(trusted), "203.0.113.7")

        trusted_v6 = _Fake("::1", {"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(cs.CommonsHandler._visitor_ip(trusted_v6), "203.0.113.7")

        untrusted = _Fake("203.0.113.9", {"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(cs.CommonsHandler._visitor_ip(untrusted), "203.0.113.9")

        no_header = _Fake("127.0.0.1", {})
        self.assertEqual(cs.CommonsHandler._visitor_ip(no_header), "127.0.0.1")


class SlowClientDoesNotBlockConcurrentReads(unittest.TestCase):
    """Hardening item 1: serve() is ThreadingHTTPServer + daemon_threads
    now, one connection per thread with a bounded per-connection socket
    timeout, so a slowloris client (Content-Length promised, body never
    sent) costs one thread, not the board. Demonstrated both ways: the
    plain single-threaded http.server.HTTPServer really does block a
    concurrent GET behind the stall, then the real server doesn't.
    """

    def _serve_with(self, server_cls):
        db_path, kk_path = _tmp_paths()
        _write_known(kk_path, [])
        store = cs.CommonsStore(db_path)
        known = cs.KnownKeys(kk_path)
        handler = type("Bound", (cs.CommonsHandler,), {
            "store": store, "known_keys": known, "limiter": cs._SlidingWindow(),
            "timeout": 2,  # short, so the test itself stays fast
        })
        httpd = server_cls(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def _open_stalled_post(self, port):
        """A real socket: real request line and headers promising a
        body, then nothing — the slowloris shape, not a re-implementation
        of one."""
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(
            b"POST /commons/post HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: 8000\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        self.addCleanup(sock.close)
        return sock

    def test_disabled_single_threaded_server_lets_a_stall_block_a_get(self):
        port = self._serve_with(http.server.HTTPServer)
        self._open_stalled_post(port)
        start = time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/commons/posts", timeout=5) as r:
            r.read()
        elapsed = time.monotonic() - start
        # The GET only completes once the stalled connection's own
        # timeout frees the single worker thread — that's the bug.
        self.assertGreater(elapsed, 1.5)

    def test_threaded_server_serves_a_concurrent_get_immediately(self):
        port = self._serve_with(cs._Server)
        self._open_stalled_post(port)
        start = time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/commons/posts", timeout=5) as r:
            r.read()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0)

    def test_server_is_threading_with_daemon_threads(self):
        self.assertTrue(issubclass(cs._Server, http.server.ThreadingHTTPServer))
        self.assertTrue(cs._Server.daemon_threads)
        self.assertEqual(cs.CommonsHandler.timeout, 10)


class CliBindDefaults(unittest.TestCase):
    """Hardening item 2: loopback unless --bind-all is named explicitly."""

    def test_default_binds_loopback(self):
        self.assertEqual(cs._parse_cli_args([]), ("127.0.0.1", 8781))

    def test_port_positional_still_works(self):
        self.assertEqual(cs._parse_cli_args(["9999"]), ("127.0.0.1", 9999))

    def test_bind_all_is_explicit(self):
        self.assertEqual(cs._parse_cli_args(["--bind-all"]), ("0.0.0.0", 8781))
        self.assertEqual(cs._parse_cli_args(["9999", "--bind-all"]), ("0.0.0.0", 9999))


if __name__ == "__main__":
    unittest.main(verbosity=2)
