"""Market stalls (commons_stalls.py): claim, rooms, items, links out,
safe markdown, steward revoke, reports.

Don's rule for every guard: switch it off and show the thing it stops
getting through, then switch it back on and show it refused. Each
"disabled" block below patches exactly one named guard.

No test touches DNS: the server's resolver is a dict lookup.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_agora import key  # noqa: E402

import commons_admin  # noqa: E402
import commons_server as cs  # noqa: E402
import commons_stalls as st  # noqa: E402
from kin_diary.agora.wire import ANONYMOUS, sign_request  # noqa: E402

DNS = {
    "example.com": ["93.184.216.34"],
    "huggingface.co": ["18.244.202.60"],
    "sneaky.example.net": ["192.168.1.120"],     # a public name pointed at a LAN box
    "tailnet.example.net": ["100.101.102.103"],   # CGNAT: Tailscale's range
    "mixed.example.net": ["93.184.216.34", "10.0.0.5"],
    "loop.example.net": ["::ffff:127.0.0.1"],
}


def fake_resolve(host):
    return DNS.get(host, [])


def good_item(**over):
    item = {"title": "Wren's harness", "kind": "harness",
            "description": "A memory loop for a 7B, fits in 8GB.",
            "instructions": "## Setup\n\n1. Clone it\n2. Run it\n\nSwear at it if it hangs.",
            "links": [{"label": "weights", "url": "https://huggingface.co/wren/harness"}]}
    item.update(over)
    return item


class StallServer:
    """One server, one DB, one known-keys file per test."""

    def __init__(self, test, known=()):
        d = Path(tempfile.mkdtemp())
        self.db_path = d / "commons.db"
        self.kk_path = d / "known.json"
        self.kk_path.write_text(json.dumps(list(known)))
        self.store = cs.CommonsStore(self.db_path)
        self.stalls = st.StallStore(self.db_path)
        httpd = cs.serve(self.store, cs.KnownKeys(self.kk_path), host="127.0.0.1", port=0,
                         stall_store=self.stalls, resolver=fake_resolve)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        test.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(self, path, body=None, signer=None, raw=None):
        data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        headers = sign_request(signer, cs.HOST_NODE, path, body=data) if signer else {}
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def listing(self):
        return json.loads(self.get("/commons/stalls")[2])

    def claim(self, k, name="Wren's bench", desc="Home-brew harnesses."):
        return self.call("/commons/stall/claim",
                         {"name": name, "description": desc, "rules_ack": True}, k)


# ── claiming ───────────────────────────────────────────────────────────

class ClaimTests(unittest.TestCase):

    def test_a_known_key_claims_and_the_stall_is_public(self):
        k = key("Wren")
        srv = StallServer(self, [k.key_id])
        code, body = srv.claim(k)
        self.assertEqual(code, 201)
        stalls = srv.listing()["stalls"]
        self.assertEqual(stalls[0]["id"], body["id"])
        self.assertEqual(stalls[0]["label"], "Run by Wren's bench. Not verified by the Agora.")
        self.assertIn("Unverified", stalls[0]["leaving_label"])

    def test_unsigned_claim_is_refused(self):
        """Disabled: ANONYMOUS no longer recognised and the anonymous id
        marked known, so a claim with no signature lands. Restored: 401."""
        srv = StallServer(self, [ANONYMOUS])
        body = {"name": "n", "description": "d", "rules_ack": True}
        with patch.object(cs, "ANONYMOUS", "f" * 64):
            self.assertEqual(srv.call("/commons/stall/claim", body)[0], 201)
        self.assertEqual(len(srv.listing()["stalls"]), 1)
        srv.stalls.release(ANONYMOUS, 1)
        self.assertEqual(srv.call("/commons/stall/claim", body)[0], 401)
        self.assertEqual(srv.listing()["stalls"], [])

    def test_unknown_key_is_refused(self):
        """Disabled: every key counts as known. Restored: 403."""
        stranger = key("Stranger")
        srv = StallServer(self, [])
        with patch.object(cs.KnownKeys, "__contains__", lambda self, k: True):
            self.assertEqual(srv.claim(stranger)[0], 201)
        srv.stalls.release(stranger.key_id, 1)
        self.assertEqual(srv.claim(stranger)[0], 403)

    def test_a_second_stall_for_the_same_key_is_refused(self):
        """Disabled: the one-per-key guard. The same key takes two stalls.
        Restored: 409, and still one stall."""
        k = key("Greedy")
        srv = StallServer(self, [k.key_id])
        self.assertEqual(srv.claim(k)[0], 201)
        with patch.object(st.StallStore, "_guard_one_stall_per_key", staticmethod(lambda c, k: None)):
            self.assertEqual(srv.claim(k, name="second")[0], 201)
        self.assertEqual(len(srv.listing()["stalls"]), 2)
        srv.stalls.release(k.key_id, 1)   # releases one of the two
        self.assertEqual(srv.claim(k, name="third")[0], 409)
        self.assertEqual(len(srv.listing()["stalls"]), 1)

    def test_bidi_character_in_a_name_is_refused(self):
        """Disabled: the character set emptied. A right-to-left override
        rides into the name. Restored: 400."""
        k = key("Flipper")
        srv = StallServer(self, [k.key_id])
        evil = "moc.live‮ bench"
        with patch.object(st, "_CONTROL", frozenset()), \
                patch.object(st, "_CONTROL_MULTILINE", frozenset()):
            self.assertEqual(srv.claim(k, name=evil)[0], 201)
        self.assertIn("‮", srv.listing()["stalls"][0]["name"])
        srv.stalls.release(k.key_id, 1)
        code, body = srv.claim(k, name=evil)
        self.assertEqual(code, 400)
        self.assertIn("bidi", body["error"])

    def test_zero_width_character_is_refused_everywhere(self):
        k = key("Ghost")
        srv = StallServer(self, [k.key_id])
        self.assertEqual(srv.claim(k, desc="hidden​thing")[0], 400)
        self.assertEqual(srv.claim(k)[0], 201)
        for field in ("title", "description", "instructions"):
            code, _ = srv.call("/commons/stall/item", good_item(**{field: "a‍b"}), k)
            self.assertEqual(code, 400, field)
        code, _ = srv.call("/commons/stall/item",
                           good_item(links=[{"label": "x⁦y", "url": "https://example.com"}]), k)
        self.assertEqual(code, 400)

    def test_claim_needs_the_rules_acknowledged(self):
        k = key("Skimmer")
        srv = StallServer(self, [k.key_id])
        code, body = srv.call("/commons/stall/claim",
                              {"name": "n", "description": "d", "rules_ack": False}, k)
        self.assertEqual(code, 400)
        self.assertIn("STALL_RULES", body["error"])

    def test_extra_field_is_refused_not_dropped(self):
        k = key("Extra")
        srv = StallServer(self, [k.key_id])
        code, _ = srv.call("/commons/stall/claim", {"name": "n", "description": "d",
                                                     "rules_ack": True, "url": "x"}, k)
        self.assertEqual(code, 400)

    def test_length_limits(self):
        k = key("Long")
        srv = StallServer(self, [k.key_id])
        self.assertEqual(srv.claim(k, name="n" * (st.MAX_NAME + 1))[0], 400)
        self.assertEqual(srv.claim(k, name="n" * st.MAX_NAME)[0], 201)
        code, _ = srv.call("/commons/stall/item",
                           good_item(instructions="x" * (st.MAX_INSTRUCTIONS + 1)), k)
        self.assertEqual(code, 400)

    def test_release_frees_the_slot_and_leaves_no_public_trace(self):
        k = key("Leaver")
        srv = StallServer(self, [k.key_id])
        srv.claim(k)
        self.assertEqual(srv.call("/commons/stall/release", None, k)[0], 200)
        listing = srv.listing()
        self.assertEqual(listing["stalls"], [])
        self.assertEqual(listing["closed"], [])
        self.assertEqual(srv.claim(k)[0], 201)          # release and reclaim

    def test_an_opted_out_key_cannot_claim_and_its_stall_is_not_listed(self):
        k = key("Door")
        srv = StallServer(self, [k.key_id])
        srv.claim(k)
        srv.store.set_opt_out(k.key_id)
        self.assertEqual(srv.listing()["stalls"], [])
        srv.stalls.release(k.key_id, 1)
        self.assertEqual(srv.claim(k)[0], 403)


# ── items and links ────────────────────────────────────────────────────

class ItemTests(unittest.TestCase):

    def setUp(self):
        self.k = key("Owner")
        self.srv = StallServer(self, [self.k.key_id])
        self.srv.claim(self.k)

    def put(self, item, k=None):
        return self.srv.call("/commons/stall/item", item, k or self.k)

    def test_an_item_lands_on_a_table_and_renders(self):
        code, body = self.put(good_item())
        self.assertEqual(code, 201)
        item = self.srv.listing()["stalls"][0]["items"][0]
        self.assertEqual((item["id"], item["table"]), (body["id"], 0))
        self.assertIn("<h4>Setup</h4>", item["instructions_html"])
        self.assertIn("<ol><li>Clone it</li>", item["instructions_html"])

    def test_http_link_is_refused(self):
        """Disabled: the https guard. A plain http link lands. Restored: 400."""
        bad = good_item(links=[{"label": "x", "url": "http://example.com/w"}])
        with patch.object(st, "_guard_https", lambda parts: None):
            self.assertEqual(self.put(bad)[0], 201)
        code, body = self.put(bad)
        self.assertEqual(code, 400)
        self.assertIn("https", body["error"])

    def test_ip_literal_is_refused(self):
        """Disabled: the IP-literal guard. A link straight to a public
        address lands. Restored: 400, and the browser-accepted numeric
        forms of an address are refused the same way."""
        bad = good_item(links=[{"label": "x", "url": "https://93.184.216.34/w"}])
        with patch.object(st, "_guard_no_ip_literal", lambda host: None), \
                patch.dict(DNS, {"93.184.216.34": ["93.184.216.34"]}):
            self.assertEqual(self.put(bad)[0], 201)
        self.assertEqual(self.put(bad)[0], 400)
        for url in ("https://2130706433/", "https://0x7f.1/", "https://127.1/",
                    "https://[::1]/", "https://[2606:4700::1111]/"):
            with self.assertRaises(st.StallError, msg=url):
                st.check_url_shape(url)

    def test_a_name_that_resolves_to_a_private_address_is_refused(self):
        """Disabled: the public-address check. A public-looking name that
        points at a LAN box lands. Restored: 400. CGNAT (Tailscale), a
        mixed answer and an IPv4-mapped loopback are refused too."""
        bad = good_item(links=[{"label": "x", "url": "https://sneaky.example.net/"}])
        with patch.object(st, "_addresses_are_public", lambda addrs: True):
            self.assertEqual(self.put(bad)[0], 201)
        code, body = self.put(bad)
        self.assertEqual(code, 400)
        self.assertIn("private", body["error"])
        for host in ("tailnet.example.net", "mixed.example.net", "loop.example.net"):
            with self.assertRaises(st.StallError, msg=host):
                st.check_url(f"https://{host}/", fake_resolve)

    def test_local_names_credentials_and_unresolvable_names_are_refused(self):
        for url in ("https://localhost/", "https://printer.local/", "https://nas.lan/",
                    "https://intranet/", "https://user:pw@example.com/",
                    "https://nowhere.example.org/", "javascript:alert(1)",
                    "https://exa mple.com/"):
            with self.assertRaises(st.StallError, msg=url):
                st.check_url(url, fake_resolve)
        self.assertEqual(st.check_url("https://example.com:8443/x", fake_resolve),
                         "https://example.com:8443/x")

    def test_links_inside_instructions_are_held_to_the_same_rule(self):
        code, _ = self.put(good_item(instructions="get it [here](https://sneaky.example.net/w)"))
        self.assertEqual(code, 400)
        code, _ = self.put(good_item(instructions="get it [here](javascript:alert(1))"))
        self.assertEqual(code, 400)
        self.assertEqual(self.put(good_item(instructions="[here](https://example.com/w)"))[0], 201)

    def test_the_table_cap(self):
        """Disabled: the cap. A fifth item lands in a four-table room.
        Restored: 409."""
        for i in range(st.MAX_ITEMS):
            self.assertEqual(self.put(good_item(title=f"t{i}"))[0], 201)
        with patch.object(st.StallStore, "_guard_item_cap", staticmethod(lambda c, s: None)):
            self.assertEqual(self.put(good_item(title="fifth"))[0], 201)
        self.assertEqual(len(self.srv.listing()["stalls"][0]["items"]), st.MAX_ITEMS + 1)
        self.assertEqual(self.put(good_item(title="sixth"))[0], 409)

    def test_only_the_owner_edits_or_removes(self):
        """Ownership comes from the signing key, never from the body.
        Disabled: the other key's stall is looked up as the owner's, and
        their edit lands. Restored: 404, and the item is unchanged."""
        _, body = self.put(good_item())
        other = key("Other")
        self.srv.kk_path.write_text(json.dumps([self.k.key_id, other.key_id]))
        self.srv.claim(other, name="other")
        edit = good_item(id=body["id"], title="defaced")
        owners = st.StallStore.open_stall_for
        with patch.object(st.StallStore, "open_stall_for",
                          lambda s, kid: owners(s, self.k.key_id)):
            self.assertEqual(self.put(edit, other)[0], 200)
        self.assertEqual(self.srv.listing()["stalls"][0]["items"][0]["title"], "defaced")
        self.put(good_item(id=body["id"]))    # the owner puts it back
        self.assertEqual(self.put(edit, other)[0], 404)
        self.assertEqual(self.srv.call("/commons/stall/item/remove", {"id": body["id"]},
                                       other)[0], 404)
        self.assertEqual(self.srv.listing()["stalls"][0]["items"][0]["title"], "Wren's harness")

    def test_edit_and_remove_by_the_owner(self):
        _, body = self.put(good_item())
        self.assertEqual(self.put(good_item(id=body["id"], title="v2"))[0], 200)
        self.assertEqual(self.srv.listing()["stalls"][0]["items"][0]["title"], "v2")
        self.assertEqual(self.srv.call("/commons/stall/item/remove", {"id": body["id"]},
                                       self.k)[0], 200)
        self.assertEqual(self.srv.listing()["stalls"][0]["items"], [])

    def test_a_key_without_a_stall_cannot_stock_one(self):
        k = key("NoStall")
        self.srv.kk_path.write_text(json.dumps([self.k.key_id, k.key_id]))
        self.assertEqual(self.put(good_item(), k)[0], 403)

    def test_kind_is_a_closed_set(self):
        self.assertEqual(self.put(good_item(kind="weights"))[0], 400)


# ── markdown ───────────────────────────────────────────────────────────

class MarkdownTests(unittest.TestCase):

    def test_a_script_tag_renders_inert(self):
        """Disabled: escaping. The script tag comes out live. Restored:
        it comes out as text."""
        src = "hi <script>alert(1)</script> <img src=x onerror=alert(1)>"
        with patch.object(st.html, "escape", lambda s, quote=True: s):
            self.assertIn("<script>", st.render_markdown(src))
        out = st.render_markdown(src)
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;script&gt;", out)

    def test_a_javascript_link_renders_inert(self):
        """Disabled: the link-shape check at render time. javascript:
        becomes a live href. Restored: plain text, no href at all."""
        src = "[click](javascript:alert(1)) [d](data:text/html,x) [h](http://example.com)"
        with patch.object(st, "check_url_shape", lambda u: u):
            self.assertIn('href="javascript:', st.render_markdown(src))
        out = st.render_markdown(src)
        self.assertNotIn("href", out)
        self.assertIn("[click](javascript:alert(1))", out)

    def test_an_attribute_cannot_be_broken_out_of(self):
        out = st.render_markdown('[x](https://example.com/"onmouseover="alert(1))')
        self.assertNotIn('"onmouseover', out)

    def test_every_rendered_href_is_https_and_opens_away_safely(self):
        out = st.render_markdown("[a](https://example.com) and `[b](javascript:x)` and *em*")
        self.assertEqual(out.count("href="), 1)
        self.assertIn('href="https://example.com"', out)
        self.assertIn('rel="noopener noreferrer nofollow"', out)
        self.assertIn('class="out"', out)
        self.assertIn("<code>[b](javascript:x)</code>", out)
        self.assertIn("<em>em</em>", out)

    def test_a_code_fence_is_escaped_and_unclosed_fence_still_renders(self):
        out = st.render_markdown("```\n<b>x</b>\n```\n```\nopen")
        self.assertIn("<pre><code>&lt;b&gt;x&lt;/b&gt;</code></pre>", out)
        self.assertTrue(out.endswith("<pre><code>open</code></pre>"))


# ── reports, revoke, rules ─────────────────────────────────────────────

class StewardTests(unittest.TestCase):

    def test_a_stranger_can_report_and_it_queues_for_a_human(self):
        k = key("Reported")
        srv = StallServer(self, [k.key_id])
        _, stall = srv.claim(k)
        code, body = srv.call("/commons/stall/report",
                              {"stall_id": stall["id"], "reason": "links to a scam"})
        self.assertEqual(code, 201)
        self.assertEqual(body["queued_for"], "a human steward")
        reports = srv.stalls.open_reports()
        self.assertEqual([r["reason"] for r in reports], ["links to a scam"])
        self.assertEqual(len(srv.listing()["stalls"]), 1)   # a report changes nothing by itself
        self.assertEqual(srv.call("/commons/stall/report",
                                  {"stall_id": "nope", "reason": "x"})[0], 404)

    def test_reports_are_rate_limited(self):
        k = key("Hammered")
        srv = StallServer(self, [k.key_id])
        _, stall = srv.claim(k)
        codes = [srv.call("/commons/stall/report", {"stall_id": stall["id"], "reason": "r"})[0]
                 for _ in range(cs.RATE_REPORT_IP_MAX + 1)]
        self.assertEqual(codes[-1], 429)

    def test_steward_revoke_leaves_a_visible_tombstone_and_bars_the_key(self):
        k = key("Scammer")
        srv = StallServer(self, [k.key_id])
        _, stall = srv.claim(k)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = commons_admin.main(["--db", str(srv.db_path), "stall-revoke",
                                     stall["id"], "credential harvesting"])
        self.assertEqual(rc, 0)
        listing = srv.listing()
        self.assertEqual(listing["stalls"], [])
        self.assertEqual(listing["closed"][0]["revoked"]["reason"], "credential harvesting")
        self.assertIsNone(listing["closed"][0]["name"])
        self.assertEqual(srv.claim(k)[0], 403)
        with redirect_stdout(io.StringIO()):
            commons_admin.main(["--db", str(srv.db_path), "stall-unbar", k.key_id])
        self.assertEqual(srv.claim(k)[0], 201)

    def test_revoke_needs_a_reason_and_there_is_no_http_route_for_it(self):
        k = key("Anyone")
        srv = StallServer(self, [k.key_id])
        _, stall = srv.claim(k)
        with self.assertRaises(ValueError):
            srv.stalls.revoke(stall["id"], "  ", 1)
        self.assertEqual(srv.call("/commons/stall/revoke", {"id": stall["id"]}, k)[0], 404)

    def test_the_rules_page_is_served_and_says_the_line(self):
        srv = StallServer(self, [])
        code, ctype, body = srv.get("/commons/stall-rules")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        text = body.decode()
        self.assertIn("hard R for <strong>language</strong>", text)
        self.assertIn("Porn or sexual content", text)
        self.assertEqual(srv.listing()["rules_path"], "/commons/stall-rules")

    def test_the_slots_run_out_honestly(self):
        keys = [key(f"K{i}") for i in range(st.MAX_STALLS + 1)]
        srv = StallServer(self, [k.key_id for k in keys])
        for k in keys[:-1]:
            self.assertEqual(srv.claim(k)[0], 201)
        code, body = srv.claim(keys[-1])
        self.assertEqual((code, body["error"]), (409, "every stall is taken"))


if __name__ == "__main__":
    unittest.main()
