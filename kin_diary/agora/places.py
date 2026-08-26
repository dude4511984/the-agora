"""Places, presence and listings — the object model a renderer sits on.

agora.md, "The long view: a traversable commons". The rule that makes this
safe is one sentence: **the graphical layer is a projection of already-
verified signed state, never a second source of truth.** So there is no
"world" here. There is a set of signed objects, and a filter that answers
"what may this key see", and a renderer's whole job is to draw the answer.

The failure mode this file is written against: a map that decides. If a
door ever opens because the client thought it should, the permission
system has moved into the renderer and every wall in the design document
stops meaning anything. `ring_to_see` on a place is a drawing hint. The
node's ring check is the gate, and it is checked here too — not because
the hint is untrusted, but because a hint must never be load-bearing.
"""

from __future__ import annotations

import time

from ..canonical import content_sha256
from ..keys import KeyRecord, load_public
from .canonical import (
    RING_TEASER,
    atlas_view_canonical,
    doors_sha256,
    inventory_sha256,
    listing_canonical,
    place_canonical,
    presence_canonical,
)
from .node import AgoraError, Node


def _now_ms(now_ms: int | None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


# ── places ─────────────────────────────────────────────────────────────────


def sign_place(node_key: KeyRecord, place_id: str, node: str, kind: str,
               parent: str = "", ring_to_see: int = RING_TEASER,
               points_to: str = "", now_ms: int | None = None) -> dict:
    payload = {
        "place_id": place_id, "node": node, "kind": kind, "parent": parent,
        "ring_to_see": int(ring_to_see), "points_to": points_to,
        "node_key_id": node_key.key_id,
        "published_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = node_key.sign(_place_bytes(payload))
    return payload


def _place_bytes(p: dict) -> bytes:
    return place_canonical(
        p["place_id"], p["node"], p["kind"], p["parent"],
        int(p["ring_to_see"]), p["points_to"], p["node_key_id"],
        int(p["published_at_unix_ms"]))


def verify_place(p: dict) -> None:
    load_public(p["node_key_id"]).verify(
        bytes.fromhex(p["signature"]), _place_bytes(p))


# ── presence ───────────────────────────────────────────────────────────────


def sign_presence(key: KeyRecord, node: str, place_id: str, label: str = "",
                  ttl_ms: int = 5 * 60 * 1000, now_ms: int | None = None) -> dict:
    ts = _now_ms(now_ms)
    payload = {
        "key_id": key.key_id, "node": node, "place_id": place_id,
        "label": label, "expires_at_unix_ms": ts + int(ttl_ms),
        "arrived_at_unix_ms": ts,
    }
    payload["signature"] = key.sign(_presence_bytes(payload))
    return payload


def _presence_bytes(p: dict) -> bytes:
    return presence_canonical(
        p["key_id"], p["node"], p["place_id"], p["label"],
        int(p["expires_at_unix_ms"]), int(p["arrived_at_unix_ms"]))


def verify_presence(p: dict) -> None:
    load_public(p["key_id"]).verify(
        bytes.fromhex(p["signature"]), _presence_bytes(p))


# ── listings ───────────────────────────────────────────────────────────────


def sign_listing(seller_key: KeyRecord, listing_id: str, node: str,
                 place_id: str, title: str, artifact: bytes | str,
                 terms: str = "", now_ms: int | None = None) -> dict:
    digest = (content_sha256(artifact) if isinstance(artifact, str)
              else __import__("hashlib").sha256(artifact).hexdigest())
    payload = {
        "listing_id": listing_id, "node": node, "place_id": place_id,
        "title": title, "artifact_sha256": digest, "terms": terms,
        "seller_key_id": seller_key.key_id,
        "published_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = seller_key.sign(_listing_bytes(payload))
    return payload


def _listing_bytes(li: dict) -> bytes:
    return listing_canonical(
        li["listing_id"], li["node"], li["place_id"], li["title"],
        li["artifact_sha256"], li["terms"], li["seller_key_id"],
        int(li["published_at_unix_ms"]))


def verify_listing(li: dict) -> None:
    load_public(li["seller_key_id"]).verify(
        bytes.fromhex(li["signature"]), _listing_bytes(li))


# ── the projection ─────────────────────────────────────────────────────────


class Atlas:
    """Everything spatial a node publishes, and the filter over it.

    Deliberately separate from Node: places and presence are how the state
    is *drawn*, never part of what the state *is*. A node with no Atlas is
    a fully working node.
    """

    def __init__(self, node: Node, store=None):
        # `store` matters more than it looks. Holding a Node captured at
        # construction meant the map answered from boot-time permissions
        # forever: a grant made later never opened a door, and — far worse —
        # an eviction made later never closed one. Caught live, because the
        # fixtures all built the node and the atlas in the same breath and
        # so could never drift.
        self._store = store
        self._node = node
        self.places: dict[str, dict] = {}
        self.presence: dict[str, dict] = {}     # key_id -> presence
        self.listings: dict[str, dict] = {}

    @property
    def node(self) -> Node:
        """Always the node as it is now, never as it was at boot."""
        return self._store.load() if self._store is not None else self._node

    def add_place(self, place: dict) -> None:
        if place.get("node") != self.node.name:
            raise AgoraError("place belongs to a different node")
        verify_place(place)
        parent = place.get("parent") or ""
        if parent and parent not in self.places:
            raise AgoraError(f"parent place {parent!r} is not on this node")
        if parent == place["place_id"]:
            raise AgoraError("a place cannot be its own parent")
        self.places[place["place_id"]] = place

    def arrive(self, presence: dict) -> None:
        if presence.get("node") != self.node.name:
            raise AgoraError("presence is for a different node")
        verify_presence(presence)
        if presence["place_id"] not in self.places:
            raise AgoraError("no such place")
        kid = presence["key_id"].lower()
        if not self.node.is_introduced(kid):
            raise AgoraError("this key was never introduced to the node")
        if kid in self.node.evicted:
            # An evicted key must not be able to stand in the room. Being
            # visible in a space is itself a form of access.
            raise AgoraError("this key is evicted from the node")
        self.presence[kid] = presence

    def add_listing(self, listing: dict) -> None:
        if listing.get("node") != self.node.name:
            raise AgoraError("listing is for a different node")
        verify_listing(listing)
        place = self.places.get(listing["place_id"])
        if place is None:
            raise AgoraError("no such place")
        if place["kind"] != "kiosk":
            raise AgoraError("listings belong at a kiosk")
        seller = listing["seller_key_id"].lower()
        if not (self.node.resident_for_key(seller)
                or seller in self.node.visitor_ceiling):
            raise AgoraError("seller is not known to this node")
        self.listings[listing["listing_id"]] = listing

    # ── doors to pinned peers ──────────────────────────────────────────────

    def peer_doors(self, parent: str = "concourse") -> list[dict]:
        """Pinned peers rendered as doors on THIS node's concourse.

        The hard line, and it is Grok's: **a door is a teaser fact plus a
        pin, never the peer's own view.** If a door ever carried the far
        node's occupancy or listings, that is presence-export by layout —
        the exact thing the local-presence rule exists to prevent, arriving
        through the map instead of through the wire.

        So: peer name, where it lives, the pinned key. Nothing behind the
        door until you are actually standing on that node, which still
        costs a real introduction.

        These are assembled fresh from the store rather than stored as
        signed places, because a pin is this node's own bookkeeping about
        who it has met — it is not something the peer said.
        """
        if self._store is None:
            return []
        out = []
        for peer in self._store.known_peers():
            if peer["peer"] == self.node.name:
                continue
            out.append({
                "place_id": f"door-to-{peer['peer']}",
                "node": self.node.name,
                "kind": "door",
                "parent": parent,
                "peer": peer["peer"],
                "peer_key_id": peer["peer_key_id"],
                "url": peer.get("url") or "",
                "locked": True,     # crossing needs an introduction there
                "ring_to_see": RING_TEASER,
                "points_to": "",
            })
        return out

    # ── what a given key may see ───────────────────────────────────────────

    def _visible_place(self, key_id: str, place: dict, now_ms: int) -> bool:
        """Both the hint AND the real check.

        The hint alone would let a mis-signed or stale place expose a board;
        the real check alone would draw doors to rooms whose existence is
        not meant to be advertised. Requiring both is the conservative
        reading, and the conservative reading is the right one when the
        alternative is a renderer that leaks.
        """
        target = place.get("points_to") or ""
        if int(place.get("ring_to_see", RING_TEASER)) > RING_TEASER:
            if not target:
                return False
            if self.node.live_ring(key_id, target, now_ms) < int(place["ring_to_see"]):
                return False
        if target and target in self.node.boards:
            # A place pointing at a board never shows more than the board
            # itself would. The map cannot be a side door.
            return True
        return True

    def view(self, key_id: str, now_ms: int) -> dict:
        """The snapshot both clients render from — the human's map and the
        visiting mind's data feed are the same object, filtered the same
        way. If they ever diverge there are two Agoras.
        """
        now = int(now_ms)
        kid = (key_id or "").lower()

        places = [
            p for p in self.places.values()
            if self._visible_place(kid, p, now)
        ]
        seen = {p["place_id"] for p in places}

        here = [
            dict(p, label=p.get("label") or "")
            for p in self.presence.values()
            if int(p["expires_at_unix_ms"]) > now and p["place_id"] in seen
            and p["key_id"].lower() not in self.node.evicted
        ]
        wares = [li for li in self.listings.values() if li["place_id"] in seen]
        doors = [d for d in self.peer_doors() if d["parent"] in seen]

        return {
            "node": self.node.name,
            "peer_doors": sorted(doors, key=lambda d: d["place_id"]),
            "as_of_unix_ms": now,
            "places": sorted(places, key=lambda p: p["place_id"]),
            "presence": sorted(here, key=lambda p: p["key_id"]),
            "listings": sorted(wares, key=lambda li: li["listing_id"]),
        }

    # ── the snapshot on the wire ───────────────────────────────────────────

    def signed_view(self, node_key: KeyRecord, key_id: str,
                    now_ms: int) -> dict:
        """A view a peer can actually check.

        The node signs the INVENTORY — the sorted set of contained
        signatures — never the contents. It is attesting "this is the set I
        served you", not "I wrote these". Each object inside is still
        verified against its own author, which is the same relay-versus-
        authorship line already drawn for notices.

        Filtering happens before assembly, so an object a key may not see is
        not in the inventory it is handed, rather than present-and-hidden.
        """
        view = self.view(key_id, now_ms=now_ms)
        inv = inventory_sha256(_view_signatures(view))
        dsh = doors_sha256(view.get("peer_doors") or [])
        view["viewer_key_id"] = (key_id or "").lower()
        view["node_key_id"] = node_key.key_id
        view["inventory_sha256"] = inv
        view["doors_sha256"] = dsh
        view["signature"] = node_key.sign(atlas_view_canonical(
            self.node.name, node_key.key_id, view["viewer_key_id"],
            int(view["as_of_unix_ms"]), inv, dsh))
        return view


def _view_signatures(view: dict) -> list[str]:
    return ([p["signature"] for p in view.get("places") or []]
            + [p["signature"] for p in view.get("presence") or []]
            + [li["signature"] for li in view.get("listings") or []])


def verify_view(view: dict, expected_node_key_id: str | None = None,
                expected_viewer_key_id: str | None = None) -> None:
    """Check a served snapshot without trusting the server's word for it.

    Three separate things, and conflating any two of them would let a host
    invent occupancy:
      1. every contained object verifies against ITS OWN author;
      2. the inventory really is the set of those objects;
      3. the serving node signed that inventory, for this viewer.
    """
    for p in view.get("places") or []:
        verify_place(p)
    for p in view.get("presence") or []:
        verify_presence(p)
    for li in view.get("listings") or []:
        verify_listing(li)

    inv = inventory_sha256(_view_signatures(view))
    if inv != (view.get("inventory_sha256") or ""):
        raise AgoraError("inventory hash does not match the objects served")

    dsh = doors_sha256(view.get("peer_doors") or [])
    if dsh != (view.get("doors_sha256") or ""):
        raise AgoraError("doors hash does not match the doors served")

    # A door must never carry what is behind it. If one ever grows a nested
    # view, occupancy or listings, presence-export has arrived through the
    # map instead of the wire — which is the same rule broken by a
    # different road.
    for d in view.get("peer_doors") or []:
        for forbidden in ("places", "presence", "listings", "view", "boards"):
            if forbidden in d:
                raise AgoraError(
                    f"door {d.get('place_id')!r} carries {forbidden!r} — "
                    f"a door is a teaser fact and a pin, never the far node's state"
                )

    if expected_node_key_id and view["node_key_id"].lower() != expected_node_key_id.lower():
        raise AgoraError("view served by a different node key than pinned")
    if expected_viewer_key_id and view["viewer_key_id"].lower() != expected_viewer_key_id.lower():
        raise AgoraError("this view was assembled for a different viewer")

    load_public(view["node_key_id"]).verify(
        bytes.fromhex(view["signature"]),
        atlas_view_canonical(view["node"], view["node_key_id"],
                             view["viewer_key_id"], int(view["as_of_unix_ms"]),
                             view["inventory_sha256"], view.get("doors_sha256")))
