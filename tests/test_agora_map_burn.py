"""Process-boundary burn tests for the static Agora map."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# The repo this file sits in, not ~/kin_diary (Don's checkout location): on any
# other machine that path is absent, or worse, an older copy (2026-09-24).
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from kin_diary.agora import (  # noqa: E402
    RING_READ,
    countersign_key_intro,
    open_speaker_election,
    sign_board_evict,
    sign_board_grant,
    sign_speaker_election,
    start_key_intro,
)
from kin_diary.agora.canonical import (
    atlas_view_canonical,
    doors_sha256,
    inventory_sha256,
)
from kin_diary.agora.places import (  # noqa: E402
    Atlas,
    _view_signatures,
    sign_listing,
    sign_place,
    sign_presence,
)
from kin_diary.agora.store import NodeStore  # noqa: E402
from kin_diary.agora.wire import identify  # noqa: E402
from kin_diary.keys import generate_keypair  # noqa: E402
from kin_diary.agora.places import verify_view  # noqa: E402
from kin_diary.agora.projection import project  # noqa: E402


PROOF_FIELDS = {
    "signature", "sig_visitor", "sig_resident", "inventory_sha256",
    "doors_sha256", "content_sha256", "node_key_id", "key_id",
    "seller_key_id", "peer_key_id",
}


def strip_proof(value):
    if isinstance(value, dict):
        return {
            key: strip_proof(item)
            for key, item in value.items()
            if key not in PROOF_FIELDS
        }
    if isinstance(value, list):
        return [strip_proof(item) for item in value]
    return value


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.place_ids = set()
        self.door_ids = set()
        self.occupants = set()
        self.listing_ids = set()

    def handle_starttag(self, _tag, attrs):
        attrs = dict(attrs)
        for name, target in (
            ("data-place-id", self.place_ids),
            ("data-door-id", self.door_ids),
            ("data-occupant", self.occupants),
            ("data-listing-id", self.listing_ids),
        ):
            if name in attrs:
                target.add(html.unescape(attrs[name]))


class LocalViewHandler(BaseHTTPRequestHandler):
    atlas = None
    node_key = None
    node_name = "Home"
    expired_presence = None
    view_now_ms = 1_000_000

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path != "/view":
            self.send_error(404)
            return
        viewer = identify(self.headers, self.node_name, "/view")
        view = self.atlas.signed_view(
            self.node_key, viewer, now_ms=self.view_now_ms
        )

        # Inject a valid but expired signed presence into the served receipt.
        # This is deliberately a valid view: the projection, not the server,
        # must be responsible for excluding it at the supplied projection time.
        view["presence"].append(self.expired_presence)
        view["inventory_sha256"] = inventory_sha256(_view_signatures(view))
        view["doors_sha256"] = doors_sha256(view["peer_doors"])
        view["signature"] = self.node_key.sign(atlas_view_canonical(
            view["node"], view["node_key_id"], view["viewer_key_id"],
            int(view["as_of_unix_ms"]), view["inventory_sha256"],
            view["doors_sha256"],
        ))
        raw = (json.dumps(view, sort_keys=True) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MapBurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.keys_root = self.home / ".config" / "kin_diary" / "keys"
        self.coda = generate_keypair("Coda", keys_root=self.keys_root)
        self.marvin = generate_keypair("Marvin", keys_root=self.keys_root)
        self.node_key = generate_keypair("Home-node", keys_root=self.keys_root)
        self.presence_key = generate_keypair("Ghost", keys_root=self.keys_root)

        self.store = NodeStore(self.tmp / "home.db", "Home")
        self.store.add_resident("Coda", self.coda.key_id)
        election = open_speaker_election(
            "Home", "Coda", self.coda.key_id, {self.coda.key_id}
        )
        self.store.record("election", sign_speaker_election(self.coda, election))

        node = self.store.load()
        self.atlas = Atlas(node, store=self.store)
        self.atlas.add_place(sign_place(
            self.node_key, "concourse", "Home", "commons"
        ))
        self.atlas.add_place(sign_place(
            self.node_key, "stall-1", "Home", "kiosk", parent="concourse"
        ))
        self.atlas.add_place(sign_place(
            self.node_key, "door-Coda", "Home", "door",
            parent="concourse", ring_to_see=RING_READ,
            points_to="personal:Coda",
        ))
        self.atlas.add_listing(sign_listing(
            self.coda, "listing-1", "Home", "stall-1",
            "calculator", b"plain artifact",
        ))
        self.store.pin_peer("Frosty", "f" * 64, "http://frosty:8770")
        self.expired_presence = sign_presence(
            self.presence_key, "Home", "concourse", label="Expired",
            now_ms=0, ttl_ms=1,
        )

        LocalViewHandler.atlas = self.atlas
        LocalViewHandler.node_key = self.node_key
        LocalViewHandler.expired_presence = self.expired_presence
        self.server = HTTPServer(("127.0.0.1", 0), LocalViewHandler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

        intro = countersign_key_intro(
            self.coda,
            start_key_intro(self.marvin, "Home", self.coda.key_id),
        )
        self.store.record("intro", intro)
        self.store.record("grant", sign_board_grant(
            self.coda, self.marvin.key_id, "Home",
            "personal:Coda", RING_READ,
        ))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()

    def run_map(self, output):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = REPO
        subprocess.run(
            [
                sys.executable,
                os.path.join(REPO, "vault", "agora_map.py"),
                "Marvin", "Home", self.url, "--html", str(output),
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return output.read_text(encoding="utf-8")

    def independently_project(self):
        from kin_diary.agora.client import ViewClient
        client = ViewClient(self.marvin, "Home", self.url)
        raw = client.refresh()
        verify_view(
            raw,
            expected_node_key_id=self.node_key.key_id,
            expected_viewer_key_id=self.marvin.key_id,
        )
        return raw, project(raw, int(raw["as_of_unix_ms"]))

    def assert_page_matches(self, page, expected_scene):
        self.maxDiff = None
        parser = IDs()
        parser.feed(page)
        script = re.search(
            r'<script type="application/json" id="scene">(.*?)</script>',
            page,
            re.DOTALL,
        )
        self.assertIsNotNone(script)
        page_scene = json.loads(script.group(1))
        self.assertEqual(page_scene, strip_proof(expected_scene))
        for forbidden in ("signature", "inventory_sha256", "doors_sha256"):
            self.assertNotIn(forbidden, page)

        expected_places = {node["id"] for node in expected_scene["nodes"]}
        expected_doors = {
            edge["door"]["place_id"] for edge in expected_scene["edges"]
        }
        expected_occupants = {
            occupant["key_id"]
            for node in expected_scene["nodes"]
            for occupant in node["occupants"]
        }
        expected_listings = {
            listing["listing_id"]
            for node in expected_scene["nodes"]
            for listing in node["listings"]
        }
        self.assertEqual(parser.place_ids, expected_places)
        self.assertEqual(parser.door_ids, expected_doors)
        self.assertEqual(parser.occupants, expected_occupants)
        self.assertEqual(parser.listing_ids, expected_listings)

    def test_actual_map_is_a_burn_boundary_and_eviction_replaces_it(self):
        first_path = self.tmp / "first.html"
        first_page = self.run_map(first_path)
        first_raw, first_scene = self.independently_project()
        self.assertIn("door-Coda", {
            node["id"] for node in first_scene["nodes"]
        })
        self.assertIn("door-to-Frosty", {
            edge["door"]["place_id"] for edge in first_scene["edges"]
        })
        self.assertNotIn(self.expired_presence["key_id"], {
            occupant["key_id"]
            for node in first_scene["nodes"]
            for occupant in node["occupants"]
        })
        self.assertIn('data-place-id="door-Coda"', first_page)
        self.assert_page_matches(first_page, first_scene)

        self.store.record("evict", sign_board_evict(
            self.coda, self.marvin.key_id, "Home", "glass test"
        ))
        second_path = self.tmp / "second.html"
        second_page = self.run_map(second_path)
        second_raw, second_scene = self.independently_project()
        self.assertNotIn("door-Coda", {
            node["id"] for node in second_scene["nodes"]
        })
        self.assertNotIn('data-place-id="door-Coda"', second_page)
        self.assert_page_matches(second_page, second_scene)
        self.assertNotEqual(first_raw["inventory_sha256"],
                            second_raw["inventory_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
