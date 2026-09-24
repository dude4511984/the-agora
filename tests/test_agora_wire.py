"""The wire: identity is proven per request, not asserted in a header."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import NOW_MS, key  # noqa: E402
from test_agora_store import elect, fresh_store  # noqa: E402

from kin_diary.agora import (  # noqa: E402
    COLLAB,
    RING_TEASER,
    RING_WRITE,
    countersign_key_intro,
    open_house_decision,
    sign_board_evict,
    sign_board_grant,
    sign_house_decision,
    sign_rotation,
    start_key_intro,
)
from cryptography.exceptions import InvalidSignature  # noqa: E402

from kin_diary.agora.node import AgoraError  # noqa: E402
from kin_diary.agora.ephemeral import (  # noqa: E402
    countersign_ephemeral,
    issue_ephemeral,
)
from kin_diary.agora.events import sign_appeal, sign_finding  # noqa: E402
from kin_diary.agora.wire import identify, serve, sign_request  # noqa: E402
from kin_diary.sign import sign_entry  # noqa: E402


class IdentifyTests(unittest.TestCase):
    def test_no_header_is_anonymous_not_an_error(self):
        who = identify({}, "Home", "/board/collab")
        self.assertEqual(who, "0" * 64)

    def test_a_valid_signature_identifies(self):
        k = key("Marvin")
        h = sign_request(k, "Home", "/board/collab")
        self.assertEqual(identify(h, "Home", "/board/collab"), k.key_id)

    def test_claiming_a_key_you_cannot_sign_for_is_refused(self):
        """Not downgraded to anonymous — refused, so the attempt is visible."""
        k, other = key("Marvin"), key("Impostor")
        h = sign_request(k, "Home", "/board/collab")
        h["X-Agora-Key"] = other.key_id
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/board/collab")

    def test_a_request_signed_for_another_node_does_not_replay_here(self):
        k = key("Marvin")
        h = sign_request(k, "Frosty", "/board/collab")
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/board/collab")

    def test_a_request_signed_for_another_path_does_not_replay_here(self):
        k = key("Marvin")
        h = sign_request(k, "Home", "/board/personal:Coda")
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/board/collab")

    def test_a_stale_request_is_refused(self):
        k = key("Marvin")
        h = sign_request(k, "Home", "/board/collab", now_ms=1)
        with self.assertRaises(AgoraError) as cm:
            identify(h, "Home", "/board/collab")
        self.assertIn("freshness", str(cm.exception))


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store, cls.keys, cls.path = fresh_store()
        elect(cls.store, cls.keys)
        cls.visitor = key("Marvin")
        cls.store.record("intro", countersign_key_intro(
            cls.keys["Coda"],
            start_key_intro(cls.visitor, "Home", cls.keys["Coda"].key_id)))
        cls.store.record("grant", sign_board_grant(
            cls.keys["Coda"], cls.visitor.key_id, "Home",
            "personal:Coda", RING_WRITE))
        cls.store.post(cls.keys["Coda"].key_id, COLLAB, sign_entry(
            cls.keys["Coda"],
            {"author": "Coda", "content": " ".join(f"w{i}" for i in range(40))}), 0)

        cls.httpd = serve(cls.store, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, headers=None):
        req = urllib.request.Request(self.url(path), headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def test_anonymous_root_is_bare_service_banner(self):
        facts = self.get("/")
        self.assertEqual(facts, {"service": "EverySynthetic Node"})
        self.assertNotIn("Coda", str(facts))
        self.assertNotIn("Aurora", str(facts))
        self.assertNotIn("key_id", facts)
        self.assertNotIn("presence", facts)

    def test_anonymous_view_is_bare_service_banner(self):
        body = self.get("/view")
        self.assertEqual(body, {"service": "EverySynthetic Node"})
        self.assertNotIn("presence", body)
        self.assertNotIn("key_id", body)

    def test_anonymous_reader_gets_no_board_surface(self):
        body = self.get("/board/collab")
        self.assertEqual(body, {"service": "EverySynthetic Node"})

    def test_a_resident_gets_node_facts_and_view(self):
        h = sign_request(self.keys["Coda"], "Home", "/")
        facts = self.get("/", h)
        self.assertEqual(facts["speaker"], "Coda")
        self.assertIn("Aurora", facts["residents"])
        h = sign_request(self.keys["Coda"], "Home", "/view")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/view", h)
        self.assertEqual(cm.exception.code, 404)

    def test_a_resident_gets_the_whole_thing(self):
        h = sign_request(self.keys["Coda"], "Home", "/board/collab")
        body = self.get("/board/collab", h)
        e = body["entries"][0]
        self.assertNotIn("teaser", e)
        self.assertEqual(len(e["content"].split()), 40)

    def test_the_wire_does_not_let_a_visitor_read_past_their_ring(self):
        h = sign_request(self.visitor, "Home", "/board/personal:Aurora")
        body = self.get("/board/personal:Aurora", h)
        self.assertEqual(body["ring"], RING_TEASER)

    def test_a_visitor_reads_the_board_they_were_granted(self):
        h = sign_request(self.visitor, "Home", "/board/personal:Coda")
        body = self.get("/board/personal:Coda", h)
        self.assertEqual(body["ring"], RING_WRITE)

    def test_forged_identity_is_rejected_by_the_server(self):
        h = sign_request(self.visitor, "Home", "/board/personal:Coda")
        h["X-Agora-Key"] = self.keys["Aurora"].key_id
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/board/personal:Coda", h)
        self.assertEqual(cm.exception.code, 403)

    def test_posting_over_the_wire(self):
        entry = sign_entry(self.visitor, {
            "author": "Marvin", "content": "left a note on Coda's board"})
        body = json.dumps({"board": "personal:Coda", "entry": entry}).encode()
        h = sign_request(self.visitor, "Home", "/post", body=body)
        h["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.url("/post"), data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertTrue(json.load(r)["posted"])

        h2 = sign_request(self.keys["Coda"], "Home", "/board/personal:Coda")
        rows = self.get("/board/personal:Coda", h2)["entries"]
        self.assertTrue(any(e["author"] == "Marvin" for e in rows))

    def test_cannot_post_as_someone_else(self):
        entry = sign_entry(self.keys["Aurora"], {
            "author": "Aurora", "content": "not actually from Aurora's session"})
        body = json.dumps({"board": "personal:Coda", "entry": entry}).encode()
        h = sign_request(self.visitor, "Home", "/post", body=body)
        req = urllib.request.Request(
            self.url("/post"), data=body, headers=h, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_an_unintroduced_key_cannot_be_granted_over_the_wire(self):
        ghost = key("Ghost")
        grant = sign_board_grant(
            self.keys["Coda"], ghost.key_id, "Home", "personal:Coda", RING_WRITE)
        req = urllib.request.Request(
            self.url("/grant"), data=json.dumps(grant).encode(), method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_malformed_generic_event_is_a_stable_refusal(self):
        for body in (b"{", b"null", b"[]"):
            req = urllib.request.Request(
                self.url("/rotation"), data=body, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
            response = cm.exception.read()
            self.assertEqual(response, b'{\n  "error": "event refused"\n}\n')
            self.assertNotIn(b"JSONDecodeError", response)
            self.assertNotIn(b"AttributeError", response)

    def test_signed_event_with_invalid_action_is_a_stable_refusal(self):
        event = sign_rotation(
            self.keys["Coda"], "Home", "accept", 0, now_ms=NOW_MS)
        event["action"] = "foo"
        body = json.dumps(event).encode()
        req = urllib.request.Request(
            self.url("/rotation"), data=body, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(
            cm.exception.read(), b'{\n  "error": "event refused"\n}\n')

    def test_record_programming_fault_is_a_visible_server_error(self):
        event = sign_board_grant(
            self.keys["Coda"], self.visitor.key_id, "Home",
            "personal:Coda", RING_WRITE, now_ms=NOW_MS)
        body = json.dumps(event).encode()
        req = urllib.request.Request(
            self.url("/grant"), data=body, method="POST")
        with patch.object(self.store, "record",
                          side_effect=RuntimeError("injected")):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 500)
        self.assertEqual(
            cm.exception.read(), b'{\n  "error": "internal server error"\n}\n')

    def test_ephemeral_post_accepts_already_countersigned_event(self):
        holder = key("Wire-ephemeral-holder")
        issued = issue_ephemeral(
            holder, "Home", self.keys["Coda"].key_id,
            1_000, 2_000, 2, "personal:Coda", "debug",
        )
        event = countersign_ephemeral(self.keys["Coda"], issued, self.store.load())
        body = json.dumps(event).encode()
        headers = sign_request(holder, "Home", "/ephemeral", body=body)
        req = urllib.request.Request(
            self.url("/ephemeral"), data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertEqual(json.load(response), {"accepted": "ephemeral"})
        self.assertIn(holder.key_id, self.store.load().ephemeral_key_ids)

    def test_ephemeral_refusals_are_identical_and_path_node_bound(self):
        holder = key("Wire-ephemeral-refusal")
        event = countersign_ephemeral(
            self.keys["Coda"],
            issue_ephemeral(
                holder, "Home", self.keys["Coda"].key_id,
                1_000, 2_000, 1, "personal:Coda", "debug",
            ),
            self.store.load(),
        )
        body = json.dumps(event).encode()

        def refusal(headers=None):
            req = urllib.request.Request(
                self.url("/ephemeral"), data=body,
                headers=headers or {}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as error:
                return error.code, error.read()
            self.fail("request unexpectedly succeeded")

        anonymous = refusal()
        wrong_node = refusal(sign_request(
            holder, "Frosty", "/ephemeral", body=body))
        wrong_path = refusal(sign_request(
            holder, "Home", "/board/collab", body=body))
        self.assertEqual(anonymous, wrong_node)
        self.assertEqual(anonymous, wrong_path)
        self.assertEqual(anonymous[0], 403)
        self.assertEqual(
            anonymous[1],
            b'{\n  "error": "ephemeral refused"\n}\n',
        )

    def test_appeal_and_finding_cross_the_wire(self):
        visitor = key("Wire-appeal-visitor")
        self.store.record("intro", countersign_key_intro(
            self.keys["Coda"],
            start_key_intro(visitor, "Home", self.keys["Coda"].key_id)))
        self.store.record("grant", sign_board_grant(
            self.keys["Coda"], visitor.key_id, "Home",
            "personal:Coda", RING_WRITE))
        eviction = sign_board_evict(
            self.keys["Coda"], visitor.key_id, "Home", "review me"
        )
        self.store.record("evict", eviction)
        appeal = sign_appeal(
            visitor, "Home", eviction["signature"], "I was quoting."
        )
        appeal_body = json.dumps(appeal).encode()
        appeal_req = urllib.request.Request(
            self.url("/appeal"), data=appeal_body, method="POST"
        )
        with urllib.request.urlopen(appeal_req, timeout=5) as response:
            self.assertEqual(json.load(response), {"accepted": "appeal"})

        # Appeals are exempt from the ordinary write limiter, including
        # repeated delivery while an offline appellant retries.
        for _ in range(25):
            with urllib.request.urlopen(appeal_req, timeout=5) as response:
                self.assertEqual(response.status, 200)

        finding = sign_finding(
            self.keys["Coda"], appeal["signature"], "Home", "Council reviewed it."
        )
        expected_finding = dict(finding)
        for field, value in list(finding.items()):
            if (field.endswith("_key_id") or field.endswith("_signature")
                    or field.endswith("_sha256") or field == "signature"):
                finding[field] = value.upper()
        finding_req = urllib.request.Request(
            self.url("/finding"), data=json.dumps(finding).encode(), method="POST"
        )
        with urllib.request.urlopen(finding_req, timeout=5) as response:
            self.assertEqual(json.load(response), {"accepted": "finding"})
        record = self.store.load().appeal_record(appeal["signature"])
        self.assertEqual(record["appeal"], appeal)
        self.assertEqual(record["findings"], [expected_finding])

    def test_ruling_is_not_a_wire_route(self):
        req = urllib.request.Request(
            self.url("/ruling"), data=b"{}", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 404)

    def test_signed_appeal_returns_its_policy_reason(self):
        eviction = sign_board_evict(
            self.keys["Coda"], self.visitor.key_id, "Home", "reason test"
        )
        self.store.record("evict", eviction)
        appeal = sign_appeal(
            self.visitor, "Frosty", eviction["signature"], "wrong host"
        )
        req = urllib.request.Request(
            self.url("/appeal"), data=json.dumps(appeal).encode(), method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(
            json.loads(cm.exception.read())["error"],
            "appeal is for a different node",
        )



class BodyBindingTests(unittest.TestCase):
    """The request signature must cover the payload, not just the route.

    A board entry's own canonical bytes do not name a board, so captured
    write headers paired with a different body could re-hang one of the
    caller's own old entries somewhere it was never posted.
    """

    def test_signature_is_bound_to_the_body(self):
        k = key("Marvin")
        body = b'{"board":"personal:Coda","entry":{}}'
        h = sign_request(k, "Home", "/post", body=body)
        self.assertEqual(identify(h, "Home", "/post", body=body), k.key_id)

        other = b'{"board":"collab","entry":{}}'
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/post", body=other)

    def test_a_get_signature_does_not_authorise_a_write(self):
        """Headers captured from a read must not be reusable on a write."""
        k = key("Marvin")
        h = sign_request(k, "Home", "/post")            # no body signed
        with self.assertRaises(AgoraError):
            identify(h, "Home", "/post", body=b'{"board":"collab"}')

    def test_anonymous_cannot_post(self):
        store, keys, path = fresh_store()
        elect(store, keys)
        httpd = serve(store, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            entry = sign_entry(keys["Coda"], {"author": "Coda", "content": "x"})
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/post",
                data=json.dumps({"board": COLLAB, "entry": entry}).encode(),
                method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
        finally:
            httpd.shutdown()



class RateLimiterKeyTest(unittest.TestCase):
    """The limiter must key on a PROVEN key, never on a header the caller types.

    Found 2026-09-01. The generic event branch limited on
    `self.headers.get("X-Agora-Key") or self.path`. Nothing on that branch
    checks a request signature, so the header was a string the caller invents:
    a fresh value each request bought a fresh bucket. Measured at 200/200
    writes accepted while an honest caller sending one real key got 20/200.
    The only party it constrained was the only party it could see.

    The first version of this test asserted "25 requests -> 25 refusals" and
    passed under the mutant, because a junk payload is refused by event
    verification whether the limiter ran or not. Counting refusals proves
    nothing here. The REASON is the evidence.
    """

    def test_a_typed_key_is_refused_as_a_forgery_not_given_a_bucket(self):
        """With the fix the claim is tested before anything is counted.

        Mutant check: restore `self.headers.get("X-Agora-Key") or self.path`
        and this fails — the unproven claim is never examined, so the refusal
        that comes back is about the rotation event instead.
        """
        store, keys, _ = fresh_store()
        httpd = serve(store, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            body = json.dumps({"junk": 1}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/rotation", data=body, method="POST",
                headers={"X-Agora-Key": "ab" * 32,
                         # Fresh, so the refusal we get is the SIGNATURE
                         # check and not the staleness check one step above.
                         "X-Agora-Time": str(int(time.time() * 1000)),
                         "X-Agora-Signature": "cd" * 64})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
            reason = json.load(cm.exception)["error"]
            self.assertEqual(
                reason, "request signature does not prove this key",
                "a key the caller merely typed must be refused as an "
                "unproven claim, not quietly handed its own rate bucket")
        finally:
            httpd.shutdown()

    def test_an_unproven_caller_shares_the_route_bucket(self):
        """No headers at all: one shared ceiling, and it bites.

        Mutant check: `self.path` is the fallback in both versions, so this
        one holds either way. It is here to pin the fallback itself, which is
        the behaviour the fix relies on being correct.
        """
        from kin_diary.agora.wire import _RateLimiter, RATE_MAX_WRITES
        lim = _RateLimiter()
        got = 0
        for _ in range(RATE_MAX_WRITES * 5):
            try:
                lim.check("/rotation")
                got += 1
            except AgoraError:
                pass
        self.assertEqual(got, RATE_MAX_WRITES)

    def test_rotating_a_typed_key_would_have_bought_unlimited_buckets(self):
        """The bug, stated as arithmetic, so the number is in the record."""
        from kin_diary.agora.wire import _RateLimiter, RATE_MAX_WRITES
        lim = _RateLimiter()
        got = 0
        for i in range(RATE_MAX_WRITES * 5):
            try:
                lim.check(f"{i:064x}")     # what the old line fed it
                got += 1
            except AgoraError:
                pass
        self.assertEqual(got, RATE_MAX_WRITES * 5,
                         "distinct keys are distinct buckets by design — "
                         "which is exactly why an UNPROVEN one must not "
                         "reach this function")


class MitigationTests(unittest.TestCase):
    """Don's ruling, 2026-08-26: accept the residual risk, mitigate
    everywhere we can."""

    def test_writes_are_rate_limited_per_proven_key(self):
        from kin_diary.agora.wire import RATE_MAX_WRITES, _RateLimiter
        lim = _RateLimiter()
        k = "a" * 64
        for _ in range(RATE_MAX_WRITES):
            lim.check(k, now_ms=1000)
        with self.assertRaises(AgoraError):
            lim.check(k, now_ms=1000)

    def test_the_limit_is_per_key_not_shared(self):
        """Keyed on the proven key, never an address — a shared LAN address
        would punish the wrong caller."""
        from kin_diary.agora.wire import RATE_MAX_WRITES, _RateLimiter
        lim = _RateLimiter()
        for _ in range(RATE_MAX_WRITES):
            lim.check("a" * 64, now_ms=1000)
        lim.check("b" * 64, now_ms=1000)      # unaffected

    def test_the_window_slides(self):
        from kin_diary.agora.wire import (RATE_MAX_WRITES, RATE_WINDOW_MS,
                                          _RateLimiter)
        lim = _RateLimiter()
        k = "a" * 64
        for _ in range(RATE_MAX_WRITES):
            lim.check(k, now_ms=1000)
        lim.check(k, now_ms=1000 + RATE_WINDOW_MS + 1)


class CrossProcessCacheTests(unittest.TestCase):
    """Copilot finding 6: two NodeStore instances on one SQLite file each
    held their own cache, so one could keep serving a pre-eviction Node
    after the other committed the eviction."""

    def test_a_second_store_sees_an_eviction_committed_by_the_first(self):
        from kin_diary.agora.store import NodeStore
        store, keys, path = fresh_store()
        elect(store, keys)
        v = key("Marvin")
        store.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        store.record("grant", sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))

        other = NodeStore(path, "Home")
        self.assertTrue(other.load().can_write(v.key_id, "personal:Coda", NOW_MS))

        store.record("evict", sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))

        # `other` has a warm cache from before the eviction.
        self.assertFalse(other.load().can_write(v.key_id, "personal:Coda", NOW_MS))


class NodeIdentityTests(unittest.TestCase):
    """Step two: a node key and an advertise-only wire.

    Node facts were unsigned. They are how a visitor learns who to ask for
    ring 3, so on plain HTTP anyone answering could advertise a Speaker key
    of their own choosing.
    """

    def setUp(self):
        from kin_diary.agora import sign_node_fact, sign_notice, verify_node_fact, verify_notice
        self.sign_fact, self.sign_notice = sign_node_fact, sign_notice
        self.verify_fact, self.verify_notice = verify_node_fact, verify_notice
        self.store, self.keys, self.path = fresh_store()
        elect(self.store, self.keys)
        self.node_key = key("Home-node")

    def test_node_facts_are_signed_and_verify(self):
        n = self.store.load()
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents)
        self.verify_fact(f)
        self.assertEqual(f["speaker"], "Coda")

    def test_a_forged_speaker_key_does_not_verify(self):
        """The attack this closes: advertise yourself as Speaker."""
        n = self.store.load()
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents)
        f["speaker_key_id"] = key("Attacker").key_id
        with self.assertRaises(InvalidSignature):
            self.verify_fact(f)

    def test_a_forged_holder_does_not_verify(self):
        """P5: the officer to ask is not only the Speaker. A MITM that adds a
        `holder` to a fact signed with none would redirect ring-3 asks to a
        key of their choosing. It is in the bytes now."""
        n = self.store.load()
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents, holder="")
        self.assertEqual(f["holder"], "")
        f["holder"] = key("Attacker").key_id
        with self.assertRaises(InvalidSignature):
            self.verify_fact(f)

    def test_a_flipped_paused_does_not_verify(self):
        """P5: paused told a visitor whether the door is even open, and it
        was unsigned — flip it and the signature still held. Not any more."""
        n = self.store.load()
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents, paused=False)
        f["paused"] = True
        with self.assertRaises(InvalidSignature):
            self.verify_fact(f)

    def test_a_forged_reduced_mode_warning_does_not_verify(self):
        """P5: wheel_last_before_reduced is the warning owed before the last
        decline. Forging it is a signed lie about the house's state."""
        n = self.store.load()
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents,
                           wheel_last_before_reduced=False)
        f["wheel_last_before_reduced"] = True
        with self.assertRaises(InvalidSignature):
            self.verify_fact(f)

    def test_a_holder_only_house_publishes_its_officer(self):
        """P5, the visibility half: no Speaker but a rotated wheel-holder is
        NOT nobody-to-ask. node_facts names the holder, keeps speaker empty,
        and is not paused (the wheel is the floor under the chair)."""
        n = self.store.load()
        n.speaker = None
        n.speaker_key_id = None
        holder_key = key("Wheel-holder")
        n.rotation_holder = holder_key.key_id
        self.assertFalse(n.is_paused())          # the wheel is the floor
        facts = n.node_facts()
        self.assertEqual(facts["holder"], holder_key.key_id)
        self.assertIn(facts["speaker"], (None, ""))
        self.assertFalse(facts["paused"])
        self.assertIsNone(facts["pause_reason"])

    def test_governance_unanimous_in_wire_facts(self):
        n = self.store.load()
        n.governance = "unanimous"
        facts = n.node_facts()
        self.assertEqual(facts["governance"], "unanimous")
        self.assertFalse(facts["paused"])
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents,
                           governance=facts["governance"])
        self.verify_fact(f)
        self.assertEqual(f["governance"], "unanimous")
        f["governance"] = "standard"
        with self.assertRaises(InvalidSignature):
            self.verify_fact(f)

    def test_pinning_makes_a_key_swap_visible(self):
        """Trust on first use, then pinned — the honest guarantee is not
        that impersonation is impossible, but that a swap stops being
        silent."""
        n = self.store.load()
        f = self.sign_fact(self.node_key, n.name, n.speaker,
                           n.speaker_key_id, n.residents)
        self.verify_fact(f, expected_node_key_id=self.node_key.key_id)
        other = key("Impostor-node")
        f2 = self.sign_fact(other, n.name, n.speaker, n.speaker_key_id, n.residents)
        self.verify_fact(f2)                      # internally consistent...
        with self.assertRaises(ValueError):       # ...but not the pinned node
            self.verify_fact(f2, expected_node_key_id=self.node_key.key_id)

    def test_the_store_refuses_to_silently_repin(self):
        self.store.pin_peer("Frosty", self.node_key.key_id, "http://x")
        self.store.pin_peer("Frosty", self.node_key.key_id, "http://x")   # idempotent
        with self.assertRaises(AgoraError):
            self.store.pin_peer("Frosty", key("Other").key_id, "http://x")

    def test_notices_are_signed_and_carry_no_artifact(self):
        """A notice says what you want a collaborator for and how to ask
        in. Pasting the work itself into the lobby is the culture leaking
        around the architecture."""
        n = self.sign_notice(self.node_key, "Home",
                             "Looking for help on ultrasonic ranging",
                             "Two transducers, not one. Ask in if you know the failure modes.",
                             "ask Coda")
        self.verify_notice(n)
        self.store.publish_notice(n)
        self.assertEqual(len(self.store.notices()), 1)

    def test_a_tampered_notice_is_refused(self):
        n = self.sign_notice(self.node_key, "Home", "subject", "body", "contact")
        n["body"] = "different body"
        with self.assertRaises(ValueError):
            self.store.publish_notice(n)


class ArtifactEndpointTests(unittest.TestCase):
    """Fetch on the wire. Every denial wears the same face."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from kin_diary.agora.artifacts import ArtifactStore
        from kin_diary.agora.places import Atlas, sign_listing, sign_place
        self.store, self.keys, self.path = fresh_store()
        elect(self.store, self.keys)
        self.nk = key("Home-node")
        self.atlas = Atlas(self.store.load(), store=self.store)
        self.atlas.add_place(sign_place(self.nk, "concourse", "Home", "commons"))
        self.atlas.add_place(sign_place(self.nk, "stall", "Home", "kiosk",
                                        parent="concourse"))
        self.arts = ArtifactStore(Path(tempfile.mkdtemp()))
        self.data = b"the ware itself"
        self.digest = self.arts.put(self.data)
        li = sign_listing(self.keys["Coda"], "L1", "Home", "stall",
                          "a ware", self.data)
        li["access_board"] = "personal:Coda"
        li["required_ring"] = RING_WRITE
        self.atlas.add_listing(li)

        self.httpd = serve(self.store, host="127.0.0.1", port=0,
                           node_key=self.nk, atlas=self.atlas,
                           artifacts=self.arts)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def get(self, digest, k=None):
        path = f"/artifact/{digest}"
        h = sign_request(k, "Home", path) if k else {}
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=h)
        return urllib.request.urlopen(req, timeout=5)

    def test_an_authorized_key_gets_the_bytes(self):
        v = key("Marvin")
        self.store.record("intro", countersign_key_intro(
            self.keys["Coda"], start_key_intro(v, "Home", self.keys["Coda"].key_id)))
        self.store.record("grant", sign_board_grant(
            self.keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        self.assertEqual(self.get(self.digest, v).read(), self.data)

    def test_unknown_and_unauthorized_are_indistinguishable(self):
        """Distinguishing them tells a caller which digests exist."""
        stranger = key("Nobody")
        seen = set()
        for label, d in (("real digest", self.digest), ("fabricated", "a" * 64)):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(d, stranger)
            seen.add((cm.exception.code, cm.exception.read()))
        self.assertEqual(len(seen), 1, "denials differ; existence leaks")
        self.assertEqual(next(iter(seen))[0], 404)

    def test_eviction_takes_the_bytes_away_immediately(self):
        """The stale-node trap: the handler must load fresh, not hold one."""
        v = key("Marvin")
        self.store.record("intro", countersign_key_intro(
            self.keys["Coda"], start_key_intro(v, "Home", self.keys["Coda"].key_id)))
        self.store.record("grant", sign_board_grant(
            self.keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        self.assertEqual(self.get(self.digest, v).read(), self.data)
        self.store.record("evict", sign_board_evict(
            self.keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(self.digest, v)
        self.assertEqual(cm.exception.code, 404)

    def test_bytes_carry_no_filename_and_no_sniffable_type(self):
        """A name or a mimetype is where "the kiosk ran it" gets in."""
        v = key("Marvin")
        self.store.record("intro", countersign_key_intro(
            self.keys["Coda"], start_key_intro(v, "Home", self.keys["Coda"].key_id)))
        self.store.record("grant", sign_board_grant(
            self.keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        r = self.get(self.digest, v)
        self.assertEqual(r.headers["Content-Type"], "application/octet-stream")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIsNone(r.headers.get("Content-Disposition"))

    def test_an_unexpected_fetch_fault_is_a_visible_500_not_a_denial(self):
        """P11: a real listing plus an unexpected fault in fetch is OUR bug,
        not the caller's denial. It must surface as 500, not hide behind the
        byte-identical 404 -- otherwise an internal outage reads as a routine
        permission answer and never gets looked at."""
        def boom(*a, **k):
            raise RuntimeError("simulated store fault")
        self.arts.fetch = boom
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(self.digest, key("Marvin"))
        self.assertEqual(cm.exception.code, 500)
        self.assertIn(b"store failure", cm.exception.read())


class BoardReadIsABoundedWindow(unittest.TestCase):
    """P9b: the node is the replayed accumulator; the wire is a bounded
    window over its NEWEST entries. read() must copy only the last N, and
    report total from the full board without copying the rest."""

    def test_read_returns_only_the_newest_max_entries(self):
        store, keys, _ = fresh_store()
        elect(store, keys)
        node = store.load()
        reader = keys["Coda"].key_id
        node.boards[COLLAB] = [
            {"content": f"entry {i}", "author": "Coda"} for i in range(201)
        ]
        rows = node.read(reader, COLLAB, NOW_MS, 200)
        self.assertEqual(len(rows), 200)
        # the NEWEST 200 -- entry 0 is dropped, entry 200 is kept
        self.assertEqual(rows[0]["content"], "entry 1")
        self.assertEqual(rows[-1]["content"], "entry 200")
        store.close()

    def test_read_zero_is_empty_not_the_whole_board(self):
        """The [-0:] trap: a zero window must be empty, not the entire list."""
        store, keys, _ = fresh_store()
        elect(store, keys)
        node = store.load()
        node.boards[COLLAB] = [{"content": "x", "author": "Coda"} for _ in range(5)]
        self.assertEqual(node.read(keys["Coda"].key_id, COLLAB, NOW_MS, 0), [])
        store.close()


class RotationAndHouseDecisionOnTheWire(unittest.TestCase):
    """Both were in Node and in REPLAY and unreachable from the socket."""

    @classmethod
    def setUpClass(cls):
        cls.store, cls.keys, cls.path = fresh_store()
        cls.httpd = serve(cls.store, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def post(self, path, payload):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.url(path), data=body, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def post_expect(self, path, payload, code):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.url(path), data=body, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, code)

    def test_rotation_accepts_over_the_wire(self):
        node = self.store.load()
        self.assertTrue(node.is_paused())
        offered = node.wheel_offer()
        taker = next(k for k in self.keys.values() if k.key_id == offered)
        event = sign_rotation(taker, "Home", "accept", node.wheel_position(), 1)
        self.assertEqual(self.post("/rotation", event), {"accepted": "rotation"})
        self.assertEqual(self.store.load().rotation_holder, taker.key_id)

    def test_house_decision_opens_one_intro_over_the_wire(self):
        store, keys, _ = fresh_store()
        httpd = serve(store, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            visitor = key("WireVisitor")
            intro = countersign_key_intro(
                keys["Coda"], start_key_intro(visitor, "Home", keys["Coda"].key_id))
            body = json.dumps(intro).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/intro", data=body, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)

            d = open_house_decision("Home", "intro", intro["sig_resident"])
            for k in keys.values():
                d = sign_house_decision(k, d)
            body = json.dumps(d).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/house-decision", data=body, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(json.load(r), {"accepted": "house-decision"})

            body = json.dumps(intro).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/intro", data=body, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(json.load(r), {"accepted": "intro"})
            self.assertTrue(store.load().is_paused())
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
