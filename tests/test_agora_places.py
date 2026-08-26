"""Places and presence: the map must never be able to decide."""

import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
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
        ids = {p["place_id"] for p in atlas.view(stranger.key_id)["places"]}
        self.assertIn("concourse", ids)
        self.assertIn("stall-1", ids)
        self.assertNotIn("codas-door", ids)

    def test_the_door_appears_once_the_grant_actually_exists(self):
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        node.accept_intro(countersign_key_intro(
            keys["Coda"], start_key_intro(v, "Home", keys["Coda"].key_id)))
        self.assertNotIn("codas-door",
                         {p["place_id"] for p in atlas.view(v.key_id)["places"]})
        node.accept_grant(sign_board_grant(
            keys["Coda"], v.key_id, "Home", "personal:Coda", RING_WRITE))
        self.assertIn("codas-door",
                      {p["place_id"] for p in atlas.view(v.key_id)["places"]})

    def test_a_forged_low_ring_hint_cannot_open_a_door(self):
        """If ring_to_see alone decided, re-signing a place with a lower
        hint would expose a board. The real ring check is what holds."""
        node, keys, nk, atlas = furnished()
        atlas.add_place(sign_place(nk, "back-door", "Home", "door",
                                   parent="concourse", ring_to_see=RING_TEASER,
                                   points_to="personal:Aurora"))
        stranger = key("Nobody")
        view = atlas.view(stranger.key_id)
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
        self.assertEqual(atlas.view(v.key_id), atlas.view(v.key_id))


class PresenceTests(unittest.TestCase):
    def test_presence_is_signed_by_whoever_is_present(self):
        node, keys, nk, atlas = furnished()
        v = key("Marvin")
        p = sign_presence(v, "Home", "concourse", label="Marvin")
        verify_presence(p)
        atlas.arrive(p)
        self.assertEqual(len(atlas.view(v.key_id)["presence"]), 1)

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
        self.assertEqual(len(atlas.view(keys["Coda"].key_id)["presence"]), 0)
        with self.assertRaises(AgoraError):
            atlas.arrive(sign_presence(v, "Home", "concourse"))

    def test_presence_in_an_unseen_place_is_not_leaked(self):
        node, keys, nk, atlas = furnished()
        resident = keys["Coda"]
        atlas.arrive(sign_presence(resident, "Home", "codas-door"))
        stranger = key("Nobody")
        self.assertEqual(len(atlas.view(stranger.key_id)["presence"]), 0)


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

    def test_a_stranger_cannot_list(self):
        node, keys, nk, atlas = furnished()
        outsider = key("Rando")
        with self.assertRaises(AgoraError):
            atlas.add_listing(sign_listing(
                outsider, "x", "Home", "stall-1", "t", "a"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
