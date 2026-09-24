"""The market's shops: the page's promises and the map's relay.

Walked headless end to end (claude-room rigs/agora/stalls.py, 2026-09-24):
street -> "Enter Wren's bench (E)" -> room -> table -> reading panel with a
<script> tag shown as text and a javascript: link left dead -> a real link
-> "You're leaving the Agora. This stall is run by Wren's bench, not by us.
Unverified." -> window.open(url, '_blank', 'noopener,noreferrer') -> out
the door and back on the street. These hold what that walk showed.
"""
import json
import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one

import agora_map  # noqa: E402
import agora_market  # noqa: E402
import commons_stalls  # noqa: E402

PAGE = agora_market.MARKET_PAGE


class ShopPromises(unittest.TestCase):

    def test_the_room_has_one_table_per_item_the_server_allows(self):
        tables = re.search(r"const TABLES = (\[.*?\]);", PAGE).group(1)
        self.assertEqual(len(json.loads(tables)), commons_stalls.MAX_ITEMS)

    def test_the_header_never_says_zero_when_the_shops_did_not_load(self):
        self.assertIn('"shops couldn\'t load"', PAGE)
        self.assertIn("!j.unavailable", PAGE)

    def test_links_leave_only_through_the_confirmation_and_noopener(self):
        self.assertIn("window.open(url, '_blank', 'noopener,noreferrer')", PAGE)
        self.assertEqual(PAGE.count("window.open("), 1)
        self.assertIn("inShop.leaving_label", PAGE)

    def test_the_panel_has_a_second_lock_on_the_markup(self):
        self.assertIn("function sanitize(root)", PAGE)
        self.assertIn("sanitize(body);", PAGE)
        tags = re.search(r"const SAFE_TAGS = new Set\((\[.*?\])\);", PAGE).group(1)
        self.assertNotIn("SCRIPT", tags)
        self.assertNotIn("IMG", tags)
        # Every other piece of owner text goes in as text, never as markup.
        for field in ("p-title", "p-desc", "p-kind", "room-name", "room-label", "leave-url"):
            self.assertIn(f"getElementById('{field}').textContent", PAGE)

    def test_the_room_says_who_runs_it_and_links_the_rules_and_reports(self):
        self.assertIn("report this stall", PAGE)
        self.assertIn('id="room-rules"', PAGE)
        self.assertIn("fetch('/commons/stall/report'", PAGE)
        self.assertIn("Nothing was sent.", PAGE)

    def test_empty_stalls_say_how_to_claim(self):
        self.assertIn("commons_stall.py claim", PAGE)

    def test_phones_get_a_button_for_the_interact_key(self):
        self.assertIn('<button id="act"', PAGE)
        self.assertIn("actBtn.addEventListener('click', interact)", PAGE)


class MapRelay(unittest.TestCase):

    def _get(self, path):
        class H:
            def __init__(self, path):
                self.path = path
            def _send(self, code, body, ctype):
                self.code, self.body, self.ctype = code, body, ctype
        h = H(path)
        agora_map.Handler.do_GET(h)
        return h

    def test_public_stalls_relays_the_listing(self):
        fake = {"label": "x", "stalls": [{"id": "s"}]}
        with patch.object(agora_map, "_public_stalls", lambda: fake):
            h = self._get("/public-stalls")
        self.assertEqual((h.code, json.loads(h.body)), (200, fake))

    def test_a_dead_commons_says_unavailable_not_empty(self):
        with patch.object(agora_map, "PUBLIC_COMMONS_URL", "http://127.0.0.1:9"):
            data = agora_map._public_stalls()
        self.assertTrue(data["unavailable"])
        self.assertEqual(data["stalls"], [])

    def test_rules_relay_is_html_or_an_honest_502(self):
        with patch.object(agora_map, "_public_stall_rules", lambda: b"<p>rules</p>"):
            h = self._get("/public-stall-rules")
        self.assertEqual((h.code, h.ctype), (200, "text/html; charset=utf-8"))
        with patch.object(agora_map, "_public_stall_rules", lambda: None):
            h = self._get("/public-stall-rules")
        self.assertEqual(h.code, 502)


if __name__ == "__main__":
    unittest.main()
