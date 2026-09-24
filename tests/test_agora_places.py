"""Places and presence: the map must never be able to decide."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.exceptions import InvalidSignature  # noqa: E402

from test_agora import countersign_key_intro, home_node, key, seat, start_key_intro  # noqa: E402

from kin_diary.agora import (  # noqa: E402
    COLLAB,
    RING_READ,
    RING_TEASER,
    RING_WRITE,
    AgoraError,
    sign_board_evict,
    sign_board_grant,
)
from kin_diary.agora.places import (  # noqa: E402
    Atlas,
    sign_listing,
    sign_place,
    sign_presence,
    verify_place,
    verify_presence,
)

NOW_MS = int(time.time() * 1000)


def furnished():
    node, keys = home_node()
    seat(node, keys, "Coda")
    nk = key("Home-node")
    atlas = Atlas(node)
    atlas.add_place(sign_place(nk, "concourse", "Home", "commons"))
    atlas.add_place(sign_place(nk, "codas-door", "Home", "door",
                               parent="concourse", ring_to_see=RING_READ,
                               points_to="personal:Coda"))
    atlas.add_place(sign_place(nk, "stall-1", "Home", "kiosk",
                               parent="concourse"))
    return node, keys, nk, atlas


class PlaceTests(unittest.TestCase):
    def test_places_are_signed_by_the_node(self):
        _, _, nk, atlas = furnished()
        verify_place(atlas.places["concourse"])

    def test_a_tampered_place_is_refused(self):
        node, keys, nk, atlas = furnished()
        p = sign_place(nk, "sneaky", "Home", "door", parent="concourse",
                       ring_to_see=RING_READ, points_to="personal:Aurora")
        p["ring_to_see"] = RING_TEASER          # try to make it public
        with self.assertRaises(InvalidSignature):
            atlas.add_place(p)

    def test_a_place_from_another_node_is_refused(self):
        node, keys, nk, atlas = furnished()
        with self.assertRaises(AgoraError):
            atlas.add_place(sign_place(nk, "elsewhere", "Frosty", "commons"))

    def test_an_orphan_place_is_refused(self):
        node, keys, nk, atlas = furnished()
        with self.assertRaises(AgoraError):
            atlas.add_place(sign_place(nk, "floating", "Home", "door",
                                       parent="nowhere"))

    def test_a_place_cannot_parent_itself(self):
        node, keys, nk, atlas = furnished()
        with self.assertRaises(AgoraError):
            atlas.add_place(sign_place(nk, "loop", "Home", "door",
                                       parent="loop"))


class TheMapCannotDecide(unittest.TestCase):
    """The whole point. A door is drawn because the keyring already allows
    it — never the other way round."""

    def test_a_stranger_sees_the_commons_but_not_a_gated_door(self):
        node, keys, nk, atlas = furnished()
        stranger = key("Nobody")
        ids = {p["place_id"] for p in atlas.view(stranger.key_id, NOW_MS)["places"]}
        self.assertIn("concourse", ids)
        self.assertIn("stall-1", ids)
        self.assertNotIn("codas-door", ids)

    def test_the_door_appears_once_the_grant_actually_exists(self):
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        self.assertNotIn("codas-door",
                         {p["place_id"] for p in atlas.view(v.key_id, NOW_MS)["places"]})
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE))
        self.assertIn("codas-door",
                      {p["place_id"] for p in atlas.view(v.key_id, NOW_MS)["places"]})

    def test_a_forged_low_ring_hint_cannot_open_a_door(self):
        """If ring_to_see alone decided, re-signing a place with a lower
        hint would expose a board. The real ring check is what holds."""
        node, keys, nk, atlas = furnished()
        atlas.add_place(sign_place(nk, "back-door", "Home", "door",
                                   parent="concourse", ring_to_see=RING_TEASER,
                                   points_to="personal:Aurora"))
        stranger = key("Nobody")
        view = atlas.view(stranger.key_id, NOW_MS)
        door = next(p for p in view["places"] if p["place_id"] == "back-door")
        # The door may be drawn, but it points at a board the stranger still
        # cannot read — the map advertises, the node decides.
        self.assertEqual(
            node.effective_ring(stranger.key_id, door["points_to"]), RING_TEASER)

    def test_both_clients_get_the_same_object(self):
        """The human's map and the visiting mind's feed are one snapshot,
        filtered once. If they diverge there are two Agoras."""
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        self.assertEqual(atlas.view(v.key_id, NOW_MS), atlas.view(v.key_id, NOW_MS))


class PresenceTests(unittest.TestCase):
    def test_presence_is_signed_by_whoever_is_present(self):
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        p = sign_presence(v, "Home", "concourse", label="Marvin")
        verify_presence(p)
        atlas.arrive(p)
        self.assertEqual(len(atlas.view(v.key_id, NOW_MS)["presence"]), 1)

    def test_you_cannot_stand_in_a_place_that_does_not_exist(self):
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        with self.assertRaises(AgoraError):
            atlas.arrive(sign_presence(v, "Home", "nowhere"))

    def test_presence_expires(self):
        """A room that looks occupied when it is empty is a lie the
        renderer tells for free."""
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        atlas.arrive(sign_presence(v, "Home", "concourse",
                                   ttl_ms=1000, now_ms=1_000_000))
        self.assertEqual(len(atlas.view(v.key_id, now_ms=1_000_500)["presence"]), 1)
        self.assertEqual(len(atlas.view(v.key_id, now_ms=1_002_000)["presence"]), 0)

    def test_an_evicted_key_cannot_stand_in_the_room(self):
        """Being visible in a space is itself a form of access."""
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        atlas.arrive(sign_presence(v, "Home", "concourse"))
        node.accept_eviction(sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious"))
        self.assertEqual(len(atlas.view(keys["Coda"].key_id, NOW_MS)["presence"]), 0)
        with self.assertRaises(AgoraError):
            atlas.arrive(sign_presence(v, "Home", "concourse"))

    def test_presence_in_an_unseen_place_is_not_leaked(self):
        node, keys, nk, atlas = furnished()
        resident = keys["Coda"]
        atlas.arrive(sign_presence(resident, "Home", "codas-door"))
        stranger = key("Nobody")
        self.assertEqual(len(atlas.view(stranger.key_id, NOW_MS)["presence"]), 0)


class ListingTests(unittest.TestCase):
    def test_a_listing_names_an_artifact_by_hash_not_a_program(self):
        node, keys, nk, atlas = furnished()
        li = sign_listing(keys["Coda"], "calc-1", "Home", "stall-1",
                          "Ultrasonic range calculator", "def f(): pass",
                          terms="free")
        atlas.add_listing(li)
        self.assertEqual(len(li["artifact_sha256"]), 64)
        self.assertNotIn("artifact", li)      # the ware itself never travels here

    def test_listings_belong_at_a_kiosk(self):
        node, keys, nk, atlas = furnished()
        with self.assertRaises(AgoraError) as cm:
            atlas.add_listing(sign_listing(
                keys["Coda"], "x", "Home", "concourse", "t", "a"))
        self.assertIn("kiosk", str(cm.exception))

    def test_a_table_is_an_unserious_listing_surface(self):
        node, keys, nk, atlas = furnished()
        atlas.add_place(sign_place(
            nk, "table-1", "Home", "table", parent="concourse",
            ring_to_see=1, points_to="collab"))
        listing = sign_listing(
            keys["Coda"], "aside-1", "Home", "table-1",
            "a rubber chicken", b"not a program")
        atlas.add_listing(listing)
        self.assertEqual(
            atlas.view(keys["Coda"].key_id, NOW_MS)["listings"],
            [listing],
        )

    def test_a_stranger_cannot_list(self):
        node, keys, nk, atlas = furnished()
        outsider = key("Rando")
        with self.assertRaises(AgoraError):
            atlas.add_listing(sign_listing(
                outsider, "x", "Home", "stall-1", "t", "a"))



class SignedViewTests(unittest.TestCase):
    """P0. Grok: 'everything else is costume until that exists.'"""

    def setUp(self):
        from kin_diary.agora.places import verify_view
        self.verify_view = verify_view
        self.node, self.keys, self.nk, self.atlas = furnished()

    def test_the_node_signs_the_inventory_not_the_contents(self):
        """The server attests 'this is the set I served you', never 'I wrote
        these'. Signing contents would be claiming authorship of other
        minds' presence — which is how a host invents occupancy."""
        v = self.atlas.signed_view(self.nk, self.keys["Coda"].key_id, NOW_MS)
        self.verify_view(v)
        self.assertEqual(len(v["inventory_sha256"]), 64)

    def test_a_host_cannot_invent_occupancy(self):
        """The attack the inventory signature exists to stop."""
        from kin_diary.agora.places import sign_presence
        ghost = key("NeverWasHere")
        v = self.atlas.signed_view(self.nk, self.keys["Coda"].key_id, NOW_MS)
        v["presence"].append(sign_presence(ghost, "Home", "concourse"))
        with self.assertRaises(AgoraError) as cm:
            self.verify_view(v)
        self.assertIn("inventory", str(cm.exception))

    def test_a_forged_object_inside_a_valid_view_is_caught(self):
        v = self.atlas.signed_view(self.nk, self.keys["Coda"].key_id, NOW_MS)
        v["places"][0]["kind"] = "kiosk"
        with self.assertRaises(InvalidSignature):
            self.verify_view(v)

    def test_a_view_cannot_be_replayed_to_a_different_viewer(self):
        """Filtering is part of the claim, not a detail of delivery."""
        other = key("SomeoneElse")
        v = self.atlas.signed_view(self.nk, self.keys["Coda"].key_id, NOW_MS)
        self.verify_view(v, expected_viewer_key_id=self.keys["Coda"].key_id)
        with self.assertRaises(AgoraError):
            self.verify_view(v, expected_viewer_key_id=other.key_id)

    def test_a_view_from_an_unpinned_node_key_is_caught(self):
        impostor = key("Impostor-node")
        v = self.atlas.signed_view(impostor, self.keys["Coda"].key_id, NOW_MS)
        self.verify_view(v)                       # internally consistent
        with self.assertRaises(AgoraError):
            self.verify_view(v, expected_node_key_id=self.nk.key_id)

    def test_the_hint_cannot_open_a_door_in_a_served_view(self):
        """Grok's required test. A place claiming ring_to_see=0 while
        pointing at a gated board must not hand a stranger the board."""
        from kin_diary.agora.places import sign_place
        self.atlas.add_place(sign_place(
            self.nk, "trapdoor", "Home", "door", parent="concourse",
            ring_to_see=RING_TEASER, points_to="personal:Aurora"))
        stranger = key("Nobody")
        v = self.atlas.signed_view(self.nk, stranger.key_id, NOW_MS)
        self.verify_view(v, expected_viewer_key_id=stranger.key_id)
        door = next(p for p in v["places"] if p["place_id"] == "trapdoor")
        # The door is drawn; the board behind it is still shut.
        self.assertEqual(
            self.node.effective_ring(stranger.key_id, door["points_to"]),
            RING_TEASER)
        self.assertEqual(
            self.node.read(stranger.key_id, "personal:Aurora", NOW_MS), [])

    def test_both_clients_verify_the_identical_snapshot(self):
        """One snapshot, two clients. If they diverge there are two
        Agoras."""
        v1 = self.atlas.signed_view(self.nk, self.keys["Coda"].key_id,
                                    now_ms=1_000_000)
        v2 = self.atlas.signed_view(self.nk, self.keys["Coda"].key_id,
                                    now_ms=1_000_000)
        self.assertEqual(v1["inventory_sha256"], v2["inventory_sha256"])
        self.assertEqual(v1["signature"], v2["signature"])


class PresenceDoesNotGossip(unittest.TestCase):
    """Locked 2026-08-26: presence is local. Broadcasting "key K is at
    place P" across nodes is how a commons becomes a surveillance network,
    and the notice wire fans out by design — so it must never carry this."""

    def test_a_presence_event_from_elsewhere_is_not_occupancy_here(self):
        """A peer handed a presence event signed for another node must
        refuse it as occupancy, not quietly display it."""
        node, keys, nk, atlas = furnished()
        from kin_diary.agora.places import sign_presence
        visitor = key("Marvin")
        elsewhere = sign_presence(visitor, "Frosty", "concourse")
        with self.assertRaises(AgoraError) as cm:
            atlas.arrive(elsewhere)
        self.assertIn("different node", str(cm.exception))

    def test_a_recognised_key_is_not_standing_anywhere_until_it_says_so(self):
        """Co-Work: recognition is of the key. An imported collaborator is a
        known key, not a body in a room."""
        node, keys, nk, atlas = furnished()
        v, bundle = __import__("test_agora").eli_with_bundle()
        node.accept_bundle_import(bundle)
        self.assertIn(v.key_id, node.visitor_ceiling)      # recognised
        self.assertEqual(len(atlas.view(v.key_id, NOW_MS)["presence"]), 0)  # not here

    def test_federation_never_carries_presence(self):
        """Structural, not a comment: the federation client fetches facts
        and notices. If a presence endpoint ever joins that list, this
        fails."""
        import inspect
        from kin_diary.agora import federation
        src = inspect.getsource(federation)
        self.assertNotIn("/view", src)
        self.assertNotIn("presence", src.lower())


class NothingCapturesPermissionsAtConstruction(unittest.TestCase):
    """Grok: "if you only fixed Atlas, you fixed the incident."

    The original bug survived because every fixture built the node and the
    atlas in the same breath, so neither could ever drift. These build them
    apart on purpose and mutate afterwards — the shape that catches this
    class, not just the one instance.
    """

    def test_a_grant_made_after_construction_is_seen(self):
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        self.assertNotIn("codas-door",
                         {p["place_id"] for p in atlas.view(v.key_id, NOW_MS)["places"]})
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE))
        self.assertIn("codas-door",
                      {p["place_id"] for p in atlas.view(v.key_id, NOW_MS)["places"]})

    def test_an_eviction_made_after_construction_closes_the_door(self):
        """The dangerous direction. A stale grant merely fails to open; a
        stale eviction leaves a door standing open for someone thrown out."""
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        self.assertIn("codas-door",
                      {p["place_id"] for p in atlas.view(v.key_id, NOW_MS)["places"]})
        node.accept_eviction(sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))
        self.assertNotIn("codas-door",
                         {p["place_id"] for p in atlas.view(v.key_id, NOW_MS)["places"]})

    def test_a_store_backed_atlas_sees_writes_from_another_process(self):
        """The live shape: the wire's Atlas must not answer from boot."""
        from kin_diary.agora.places import Atlas
        from kin_diary.agora.store import NodeStore
        from test_agora_store import elect, fresh_store
        store, keys, path = fresh_store()
        elect(store, keys)
        atlas = Atlas(store.load(), store=store)
        atlas.add_place(sign_place(key("N"), "concourse", "Home", "commons"))

        v = key("Marvin")
        other = NodeStore(path, "Home")          # a different process
        other.record("intro", countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        other.record("grant", sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE))

        self.assertEqual(atlas.node.effective_ring(v.key_id, "personal:Coda"),
                         RING_WRITE)


class PeerDoorTests(unittest.TestCase):
    """P0b. Grok's line, and the hard limit: a door is a teaser fact plus a
    pin, never the far node's view. If a door ever grows a nested view you
    have left Agora."""

    def furnished_with_peer(self):
        from kin_diary.agora.places import Atlas
        from test_agora_store import elect, fresh_store
        store, keys, path = fresh_store()
        elect(store, keys)
        nk = key("Home-node")
        atlas = Atlas(store.load(), store=store)
        atlas.add_place(sign_place(nk, "concourse", "Home", "commons"))
        store.pin_peer("Frosty", key("Frosty-node").key_id, "http://192.168.1.119:8770")
        return store, keys, nk, atlas

    def test_a_pinned_peer_appears_as_a_locked_door(self):
        store, keys, nk, atlas = self.furnished_with_peer()
        v = atlas.view(keys["Coda"].key_id, NOW_MS)
        door = v["peer_doors"][0]
        self.assertEqual(door["peer"], "Frosty")
        self.assertTrue(door["locked"])

    def test_a_peer_url_is_ring_1_material(self):
        """Marvin's first ruling as the law, 2026-09-20: the address is a
        coordinate, not an advertisement (Wall 8). A stranger sees that the
        door exists; only an introduced key sees where it leads."""
        store, keys, nk, atlas = self.furnished_with_peer()
        stranger = key("Stranger-" + NOW_MS.__str__()[-4:])
        d0 = atlas.view(stranger.key_id, NOW_MS)["peer_doors"][0]
        self.assertEqual(d0["peer"], "Frosty")
        self.assertEqual(d0["url"], "")
        d1 = atlas.view(keys["Coda"].key_id, NOW_MS)["peer_doors"][0]
        self.assertEqual(d1["url"], "http://192.168.1.119:8770")
        # and the signed view a stranger is handed verifies with the blank
        from kin_diary.agora.places import verify_view
        sv = atlas.signed_view(nk, stranger.key_id, NOW_MS)
        self.assertEqual(sv["peer_doors"][0]["url"], "")
        verify_view(sv, expected_node_key_id=nk.key_id, expected_viewer_key_id=stranger.key_id)

    def test_a_door_carries_nothing_from_behind_it(self):
        """Presence-export by layout is the failure this guards."""
        store, keys, nk, atlas = self.furnished_with_peer()
        door = atlas.view(keys["Coda"].key_id, NOW_MS)["peer_doors"][0]
        for forbidden in ("places", "presence", "listings", "view", "boards"):
            self.assertNotIn(forbidden, door)

    def test_a_door_smuggling_occupancy_is_refused_on_verify(self):
        from kin_diary.agora.places import verify_view
        store, keys, nk, atlas = self.furnished_with_peer()
        sv = atlas.signed_view(nk, keys["Coda"].key_id, NOW_MS)
        sv["peer_doors"][0]["presence"] = [{"key_id": "f" * 64}]
        with self.assertRaises(AgoraError) as cm:
            verify_view(sv)
        self.assertIn("never the far node's state", str(cm.exception))

    def test_doors_are_signed_and_cannot_be_injected_in_transit(self):
        """Doors are the node's OWN claim, so the node signing them is
        honest — but they still have to be covered, or a relay could add a
        door to a peer that was never pinned."""
        from kin_diary.agora.places import verify_view
        store, keys, nk, atlas = self.furnished_with_peer()
        sv = atlas.signed_view(nk, keys["Coda"].key_id, NOW_MS)
        verify_view(sv)
        sv["peer_doors"].append({
            "place_id": "door-to-Evil", "node": "Home", "kind": "door",
            "parent": "concourse", "peer": "Evil", "peer_key_id": "e" * 64,
            "url": "http://evil", "locked": True,
        })
        with self.assertRaises(AgoraError) as cm:
            verify_view(sv)
        self.assertIn("doors hash", str(cm.exception))

    def test_a_node_does_not_show_a_door_to_itself(self):
        store, keys, nk, atlas = self.furnished_with_peer()
        store.pin_peer("Home", nk.key_id, "http://self")
        ids = {d["peer"] for d in atlas.view(keys["Coda"].key_id, NOW_MS)["peer_doors"]}
        self.assertNotIn("Home", ids)


class AViewIsAReceiptNotATicket(unittest.TestCase):
    """P0d, decided by Grok 2026-08-26.

    Three clocks, not two. Authenticity never expires — the node really did
    serve that inventory, and making verify fail later would make "what was
    Marvin shown" unfalsifiable, destroying the same audit property the
    retract/audit split exists to protect. Currency is the client's last
    fetch. Permission is the live ring, checked at the action.

    Option A (max-age inside verify) was rejected as the grant-replay bug
    in a client's hat: it defines the eviction window as in-spec.
    """

    def test_an_old_view_still_verifies_after_an_eviction(self):
        """Not a bug. The node did serve it. It is a receipt of a moment."""
        from kin_diary.agora.places import verify_view
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        old = atlas.signed_view(nk, v.key_id, NOW_MS)
        self.assertIn("codas-door", {p["place_id"] for p in old["places"]})

        node.accept_eviction(sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))

        verify_view(old)      # still authentic, deliberately
        fresh = atlas.signed_view(nk, v.key_id, NOW_MS)
        self.assertNotIn("codas-door", {p["place_id"] for p in fresh["places"]})

    def test_the_receipt_cannot_be_cashed_at_the_wire(self):
        """The wall is the live ring, not the age of the paper. Holding a
        view that listed the door does not open the board."""
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        old = atlas.signed_view(nk, v.key_id, NOW_MS)
        node.accept_eviction(sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))

        self.assertIn("codas-door", {p["place_id"] for p in old["places"]})
        self.assertEqual(
            node.effective_ring(v.key_id, "personal:Coda"), RING_TEASER)
        self.assertEqual(node.read(v.key_id, "personal:Coda", NOW_MS), [])
        with self.assertRaises(AgoraError):
            atlas.arrive(sign_presence(v, "Home", "concourse"))

    def test_no_action_anywhere_accepts_a_view(self):
        """Structural, not a promise. The moment any endpoint takes a view
        (or an inventory hash, or a view signature) and opens something, the
        snapshot has become a second permission system and would then need
        nonces, replay sets and expiry — the grants table already refused."""
        import inspect
        from kin_diary.agora import artifacts, node as node_mod, places, store, wire
        for mod in (node_mod, store, wire, artifacts):
            for name, fn in inspect.getmembers(mod, inspect.isfunction):
                params = set(inspect.signature(fn).parameters)
                self.assertFalse(
                    params & {"view", "inventory_sha256", "view_signature"},
                    f"{mod.__name__}.{name} takes a view as an argument")
        for cls in (node_mod.Node, store.NodeStore, places.Atlas,
                    artifacts.ArtifactStore):
            for name, fn in inspect.getmembers(cls, inspect.isfunction):
                if name in ("signed_view", "view", "_view_signatures"):
                    continue
                params = set(inspect.signature(fn).parameters)
                self.assertFalse(
                    params & {"view", "inventory_sha256", "view_signature"},
                    f"{cls.__name__}.{name} takes a view as an argument")

    def test_projection_replaces_rather_than_merges(self):
        """Replace-not-merge is the protocol rule, not client discipline.
        Difference-patching that keeps a door the new view omitted IS the
        Marvin bug as an algorithm."""
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE,
            now_ms=1_000_000))
        v1 = atlas.signed_view(nk, v.key_id, NOW_MS)
        node.accept_eviction(sign_board_evict(
            keys["Coda"], v.key_id, "Home", "malicious", now_ms=2_000_000))
        v2 = atlas.signed_view(nk, v.key_id, NOW_MS)

        projected = project(v1)
        projected = project(v2)          # replaces, never unions
        self.assertNotIn("codas-door", projected["places"])
        union = project(v1)["places"] | project(v2)["places"]
        self.assertIn("codas-door", union)   # what merging would have kept


def project(view: dict) -> dict:
    """The one legal way to turn a verified view into a scene: take it
    whole. Both clients use this, because if the human map and the data
    feed diverge there are two Agoras."""
    return {
        "places": {p["place_id"] for p in view.get("places") or []},
        "presence": {p["key_id"] for p in view.get("presence") or []},
        "listings": {li["listing_id"] for li in view.get("listings") or []},
        "doors": {d["place_id"] for d in view.get("peer_doors") or []},
    }

if __name__ == "__main__":
    unittest.main(verbosity=2)
