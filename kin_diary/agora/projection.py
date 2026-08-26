"""Pure projection of a verified Atlas view into a renderable scene graph."""

from __future__ import annotations

from copy import deepcopy


class ProjectionError(ValueError):
    """The verified-view shape cannot represent a safe scene graph."""


def _list(view: dict, key: str) -> list:
    value = view.get(key, [])
    if not isinstance(value, list):
        raise ProjectionError(f"view.{key} must be a list")
    return value


def _required(mapping: dict, key: str, context: str):
    if key not in mapping:
        raise ProjectionError(f"{context} has no {key}")
    return mapping[key]


def project(view: dict, now_ms: int) -> dict:
    """Project one verified view, replacing any previous scene completely.

    This function does not verify signatures or consult live state.  Its input
    is already a verified snapshot; its only authority is that snapshot and
    the explicit projection time used to expire presence.
    """
    if not isinstance(view, dict):
        raise ProjectionError("view must be an object")
    if not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise ProjectionError("now_ms must be an integer")

    places = _list(view, "places")
    nodes_by_id: dict[str, dict] = {}
    for place in places:
        if not isinstance(place, dict):
            raise ProjectionError("place must be an object")
        place_id = _required(place, "place_id", "place")
        if not isinstance(place_id, str) or not place_id:
            raise ProjectionError("place_id must be a non-empty string")
        if place_id in nodes_by_id:
            raise ProjectionError(f"duplicate place id {place_id!r}")
        nodes_by_id[place_id] = {
            "place": deepcopy(place),
            "kind": _required(place, "kind", "place"),
            "parent": place.get("parent") or "",
            "children": [],
            "listings": [],
            "occupants": [],
        }

    parent_by_id: dict[str, str] = {}
    for place_id, node in nodes_by_id.items():
        parent = node["place"].get("parent") or ""
        if not isinstance(parent, str):
            raise ProjectionError(f"parent for {place_id!r} must be a string")
        if parent and parent not in nodes_by_id:
            raise ProjectionError(
                f"place {place_id!r} has absent parent {parent!r}"
            )
        if parent == place_id:
            raise ProjectionError(f"place {place_id!r} is its own parent")
        parent_by_id[place_id] = parent

    # Validate the entire forest before constructing child links.  A cycle
    # would otherwise make recursive renderers either loop or invent a root.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(place_id: str) -> None:
        if place_id in visiting:
            raise ProjectionError("place parent cycle detected")
        if place_id in visited:
            return
        visiting.add(place_id)
        parent = parent_by_id[place_id]
        if parent:
            visit(parent)
        visiting.remove(place_id)
        visited.add(place_id)

    for place_id in nodes_by_id:
        visit(place_id)

    roots: list[str] = []
    for place_id in sorted(nodes_by_id):
        parent = parent_by_id[place_id]
        if parent:
            nodes_by_id[parent]["children"].append(place_id)
        else:
            roots.append(place_id)
    for node in nodes_by_id.values():
        node["children"].sort()

    listing_ids: set[str] = set()
    for listing in _list(view, "listings"):
        if not isinstance(listing, dict):
            raise ProjectionError("listing must be an object")
        listing_id = _required(listing, "listing_id", "listing")
        place_id = _required(listing, "place_id", "listing")
        if listing_id in listing_ids:
            raise ProjectionError(f"duplicate listing id {listing_id!r}")
        if place_id not in nodes_by_id:
            raise ProjectionError(
                f"listing {listing_id!r} points to absent place {place_id!r}"
            )
        listing_ids.add(listing_id)
        nodes_by_id[place_id]["listings"].append(deepcopy(listing))
    for node in nodes_by_id.values():
        node["listings"].sort(key=lambda listing: listing["listing_id"])

    for presence in _list(view, "presence"):
        if not isinstance(presence, dict):
            raise ProjectionError("presence must be an object")
        place_id = _required(presence, "place_id", "presence")
        expires = _required(presence, "expires_at_unix_ms", "presence")
        if place_id not in nodes_by_id:
            raise ProjectionError(
                f"presence points to absent place {place_id!r}"
            )
        if not isinstance(expires, int) or isinstance(expires, bool):
            raise ProjectionError("presence expiry must be an integer")
        if expires > now_ms:
            nodes_by_id[place_id]["occupants"].append(deepcopy(presence))
    for node in nodes_by_id.values():
        node["occupants"].sort(
            key=lambda occupant: (
                occupant.get("key_id", ""),
                occupant["expires_at_unix_ms"],
            )
        )

    door_ids: set[str] = set()
    edges: list[dict] = []
    for door in _list(view, "peer_doors"):
        if not isinstance(door, dict):
            raise ProjectionError("peer door must be an object")
        door_id = _required(door, "place_id", "peer door")
        parent = _required(door, "parent", "peer door")
        peer = _required(door, "peer", "peer door")
        if door_id in door_ids:
            raise ProjectionError(f"duplicate peer door id {door_id!r}")
        if door_id in nodes_by_id:
            raise ProjectionError(
                f"peer door id {door_id!r} collides with a place id"
            )
        if parent not in nodes_by_id:
            raise ProjectionError(
                f"peer door {door_id!r} has absent parent {parent!r}"
            )
        if not isinstance(peer, str) or not peer:
            raise ProjectionError(f"peer door {door_id!r} has no peer label")
        if door.get("locked") is not True:
            raise ProjectionError(f"peer door {door_id!r} is not locked")
        door_ids.add(door_id)
        edges.append({
            "door": deepcopy(door),
            "from": parent,
            "to": peer,
            "label": peer,
            "locked": True,
        })
    edges.sort(key=lambda edge: (edge["from"], edge["label"], edge["door"]["place_id"]))

    return {
        "node": deepcopy(_required(view, "node", "view")),
        "as_of_unix_ms": deepcopy(_required(view, "as_of_unix_ms", "view")),
        "roots": roots,
        "nodes": [
            dict(node, id=place_id)
            for place_id, node in sorted(nodes_by_id.items())
        ],
        "edges": edges,
    }


__all__ = ["ProjectionError", "project"]
