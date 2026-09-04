"""Cross-node discovery and notice exchange.

This module is deliberately a client-side coordinator.  A peer's signed
facts are verified before trust-on-first-use pinning, and notices are
verified independently of the node that served them.  A valid notice signed
by another node is a relay, not a forgery; an invalid signature is neither.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen

from .events import verify_node_fact, verify_notice
from .node import AgoraError
from .store import NodeStore
from .wire import MAX_BODY_BYTES


class FederationError(Exception):
    """The peer response could not be safely incorporated."""


class PeerUnreachable(FederationError):
    """The peer could not be contacted or did not return a usable response."""


class PeerVerificationError(FederationError):
    """The peer returned data that failed cryptographic or pin validation."""


class PeerResponseTooLarge(FederationError):
    """The peer response exceeded the bounded federation envelope."""


MAX_PEER_RESPONSE_BYTES = MAX_BODY_BYTES
MAX_NOTICES_PER_FETCH = 50


@dataclass(frozen=True)
class NoticeEnvelope:
    """A verified notice and whether it was relayed by the serving peer."""

    notice: dict
    relayed: bool


def _endpoint(url: str, path: str) -> str:
    base = (url or "").rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _get_json(url: str, timeout: float) -> object:
    try:
        with urlopen(url, timeout=timeout) as response:
            raw = response.read(MAX_PEER_RESPONSE_BYTES + 1)
        if len(raw) > MAX_PEER_RESPONSE_BYTES:
            raise PeerResponseTooLarge(
                f"peer response exceeds {MAX_PEER_RESPONSE_BYTES} bytes"
            )
        return json.loads(raw)
    except PeerResponseTooLarge:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise PeerUnreachable(f"peer unreachable at {url}: {exc}") from exc
    except (ValueError, UnicodeError) as exc:
        raise PeerUnreachable(f"peer returned invalid JSON at {url}: {exc}") from exc


def _known_peer(store: NodeStore, peer: str) -> dict | None:
    return next((p for p in store.known_peers() if p["peer"] == peer), None)


def _url_pin_conflict(store: NodeStore, url: str, peer_key_id: str) -> bool:
    """Detect a key swap at an already-known endpoint, even if its name changed."""
    normalized = url.rstrip("/")
    return any(
        (p.get("url") or "").rstrip("/") == normalized
        and p["peer_key_id"].lower() != peer_key_id.lower()
        for p in store.known_peers()
    )


def discover_peer(
    store: NodeStore,
    url: str,
    *,
    timeout: float = 10,
) -> dict:
    """Fetch, verify, and TOFU-pin a peer's signed node facts.

    The unsigned wrapper returned by ``GET /`` is transport metadata.  The
    nested ``signed`` object is the node's self-description and is the only
    object that is verified or pinned.
    """
    payload = _get_json(_endpoint(url, "/"), timeout)
    if not isinstance(payload, dict):
        raise PeerVerificationError("peer facts response is not an object")
    fact = payload.get("signed")
    if not isinstance(fact, dict):
        raise PeerVerificationError("peer returned unsigned node facts")

    peer = fact.get("node")
    peer_key_id = fact.get("node_key_id")
    if not isinstance(peer, str) or not peer:
        raise PeerVerificationError("signed node facts have no node name")
    if not isinstance(peer_key_id, str) or not peer_key_id:
        raise PeerVerificationError("signed node facts have no node key")

    known = _known_peer(store, peer)
    expected = known["peer_key_id"] if known else None
    if expected is None and _url_pin_conflict(store, url, peer_key_id):
        raise PeerVerificationError(
            "peer endpoint previously presented a different node key"
        )
    try:
        verify_node_fact(fact, expected_node_key_id=expected)
    except Exception as exc:
        raise PeerVerificationError(
            f"node facts from {peer!r} failed verification: {exc}"
        ) from exc

    try:
        store.pin_peer(peer, peer_key_id, url.rstrip("/"))
    except AgoraError as exc:
        raise PeerVerificationError(str(exc)) from exc
    return fact


def fetch_peer_notices(
    store: NodeStore,
    peer: str,
    *,
    url: str | None = None,
    timeout: float = 10,
) -> list[NoticeEnvelope]:
    """Fetch and verify a pinned peer's notices.

    A notice whose key differs from the serving peer is accepted when its own
    signature verifies.  That is a legitimate relay: the serving node is
    transporting another node's signed statement, not claiming authorship.
    A notice with a bad signature is rejected because it is neither a valid
    origin statement nor a trustworthy relay.
    """
    pinned = _known_peer(store, peer)
    if pinned is None:
        raise PeerVerificationError(f"peer {peer!r} is not pinned")
    target = url or pinned.get("url")
    if not target:
        raise PeerVerificationError(f"peer {peer!r} has no URL")

    payload = _get_json(_endpoint(target, "/notices"), timeout)
    if not isinstance(payload, dict) or not isinstance(payload.get("notices"), list):
        raise PeerVerificationError("peer returned malformed notices")
    if len(payload["notices"]) > MAX_NOTICES_PER_FETCH:
        raise PeerResponseTooLarge(
            f"peer returned more than {MAX_NOTICES_PER_FETCH} notices"
        )

    out: list[NoticeEnvelope] = []
    for index, notice in enumerate(payload["notices"]):
        if not isinstance(notice, dict):
            raise PeerVerificationError(f"notice {index} is not an object")
        try:
            verify_notice(notice)
        except Exception as exc:
            raise PeerVerificationError(
                f"notice {index} failed verification: {exc}"
            ) from exc
        notice_key = notice.get("node_key_id")
        if not isinstance(notice_key, str) or not notice_key:
            raise PeerVerificationError(f"notice {index} has no node key")
        out.append(NoticeEnvelope(
            notice=notice,
            relayed=notice_key.lower() != pinned["peer_key_id"].lower(),
        ))
    return out


def exchange(
    store: NodeStore,
    url: str,
    *,
    timeout: float = 10,
) -> tuple[dict, list[NoticeEnvelope]]:
    """Discover a peer and fetch its verified notices in one operation."""
    facts = discover_peer(store, url, timeout=timeout)
    return facts, fetch_peer_notices(store, facts["node"], timeout=timeout)


__all__ = [
    "FederationError",
    "PeerUnreachable",
    "PeerVerificationError",
    "PeerResponseTooLarge",
    "MAX_PEER_RESPONSE_BYTES",
    "MAX_NOTICES_PER_FETCH",
    "NoticeEnvelope",
    "discover_peer",
    "fetch_peer_notices",
    "exchange",
]
