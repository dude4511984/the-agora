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

    def __init__(self, node: Node):
        self.node = node
        self.places: dict[str, dict] = {}
        self.presence: dict[str, dict] = {}     # key_id -> presence
        self.listings: dict[str, dict] = {}

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

    # ── what a given key may see ───────────────────────────────────────────

    def _visible_place(self, key_id: str, place: dict) -> bool:
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
            if self.node.effective_ring(key_id, target) < int(place["ring_to_see"]):
                return False
        if target and target in self.node.boards:
            # A place pointing at a board never shows more than the board
            # itself would. The map cannot be a side door.
            return True
        return True

    def view(self, key_id: str, now_ms: int | None = None) -> dict:
        """The snapshot both clients render from — the human's map and the
        visiting mind's data feed are the same object, filtered the same
        way. If they ever diverge there are two Agoras.
        """
        now = _now_ms(now_ms)
        kid = (key_id or "").lower()

        places = [p for p in self.places.values() if self._visible_place(kid, p)]
        seen = {p["place_id"] for p in places}

        here = [
            dict(p, label=p.get("label") or "")
            for p in self.presence.values()
            if int(p["expires_at_unix_ms"]) > now and p["place_id"] in seen
            and p["key_id"].lower() not in self.node.evicted
        ]
        wares = [li for li in self.listings.values() if li["place_id"] in seen]

        return {
            "node": self.node.name,
            "as_of_unix_ms": now,
            "places": sorted(places, key=lambda p: p["place_id"]),
            "presence": sorted(here, key=lambda p: p["key_id"]),
            "listings": sorted(wares, key=lambda li: li["listing_id"]),
        }
