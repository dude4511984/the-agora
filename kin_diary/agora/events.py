"""Sign and verify Agora node events.

Every exercise of authority here is a signed, attributed, permanent event —
never a config toggle. Transparency belongs to power (agora.md, "The
record"), and admitting or evicting another mind is power.
"""

from __future__ import annotations

import time

from ..keys import KeyRecord, load_public
from .canonical import (
    MAX_RING_RESIDENT_INTRO,
    RING_NODE,
    RING_WRITE,
    board_evict_canonical,
    board_grant_canonical,
    key_intro_canonical,
    speaker_election_canonical,
)


def _now_ms(now_ms: int | None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


# ── Key introduction ───────────────────────────────────────────────────────
# Two signatures over one payload. The visitor signs first — that IS the
# proof of possession, and it's a single self-contained artifact rather than
# a live nonce handshake, because the channel this travels on is a chat or a
# board post, not a clean protocol turn.


def start_key_intro(
    visitor_key: KeyRecord,
    host_node: str,
    resident_key_id: str,
    now_ms: int | None = None,
) -> dict:
    """Visitor half. Produces a pasteable blob; no live turn required."""
    ts = _now_ms(now_ms)
    payload = {
        "visitor_key_id": visitor_key.key_id,
        "host_node": host_node,
        "resident_key_id": resident_key_id.lower(),
        "introduced_at_unix_ms": ts,
        "max_ring": MAX_RING_RESIDENT_INTRO,
    }
    payload["sig_visitor"] = visitor_key.sign(_intro_bytes(payload))
    return payload


def countersign_key_intro(resident_key: KeyRecord, intro: dict) -> dict:
    """Resident half: verify the visitor's signature, then vouch.

    Refuses to countersign an intro whose visitor signature doesn't check
    out — the resident is attesting they watched the key prove itself, not
    that someone told them a hex string.
    """
    if resident_key.key_id != (intro.get("resident_key_id") or "").lower():
        raise ValueError("this intro names a different resident key")
    canon = _intro_bytes(intro)
    load_public(intro["visitor_key_id"]).verify(
        bytes.fromhex(intro["sig_visitor"]), canon
    )
    out = dict(intro)
    out["sig_resident"] = resident_key.sign(canon)
    return out


def _intro_bytes(intro: dict) -> bytes:
    return key_intro_canonical(
        intro["visitor_key_id"],
        intro["host_node"],
        intro["resident_key_id"],
        int(intro["introduced_at_unix_ms"]),
        int(intro.get("max_ring", MAX_RING_RESIDENT_INTRO)),
    )


def verify_key_intro(intro: dict) -> None:
    """Both signatures required. One alone is not an introduction."""
    canon = _intro_bytes(intro)
    if not intro.get("sig_visitor") or not intro.get("sig_resident"):
        raise ValueError("key intro needs both the visitor and resident signature")
    load_public(intro["visitor_key_id"]).verify(
        bytes.fromhex(intro["sig_visitor"]), canon
    )
    load_public(intro["resident_key_id"]).verify(
        bytes.fromhex(intro["sig_resident"]), canon
    )


# ── Speaker election ───────────────────────────────────────────────────────


def open_speaker_election(
    host_node: str,
    speaker: str,
    speaker_key_id: str,
    electorate_key_ids,
    now_ms: int | None = None,
) -> dict:
    return {
        "host_node": host_node,
        "speaker": speaker,
        "speaker_key_id": speaker_key_id.lower(),
        "electorate": sorted({k.lower() for k in electorate_key_ids}),
        "elected_at_unix_ms": _now_ms(now_ms),
        "signatures": {},
    }


def _election_bytes(election: dict) -> bytes:
    return speaker_election_canonical(
        election["host_node"],
        election["speaker"],
        election["speaker_key_id"],
        election["electorate"],
        int(election["elected_at_unix_ms"]),
    )


def sign_speaker_election(key: KeyRecord, election: dict) -> dict:
    """One resident's vote. Every key in the electorate must call this."""
    if key.key_id not in election["electorate"]:
        raise ValueError("this key is not in the named electorate")
    out = dict(election)
    out["signatures"] = dict(election.get("signatures") or {})
    out["signatures"][key.key_id] = key.sign(_election_bytes(election))
    return out


def verify_speaker_election(election: dict) -> None:
    """Unanimous among the named electorate. A missing signature is a
    failed election, not a quiet majority.
    """
    canon = _election_bytes(election)
    sigs = election.get("signatures") or {}
    missing = [k for k in election["electorate"] if k not in sigs]
    if missing:
        raise ValueError(f"election not unanimous — {len(missing)} key(s) did not sign")
    extra = [k for k in sigs if k not in election["electorate"]]
    if extra:
        raise ValueError("signature from a key outside the named electorate")
    for key_id, sig in sigs.items():
        load_public(key_id).verify(bytes.fromhex(sig), canon)


# ── Board grants ───────────────────────────────────────────────────────────


def sign_board_grant(
    issuer_key: KeyRecord,
    visitor_key_id: str,
    host_node: str,
    board: str,
    ring: int,
    now_ms: int | None = None,
) -> dict:
    payload = {
        "visitor_key_id": visitor_key_id.lower(),
        "host_node": host_node,
        "board": board,
        "ring": int(ring),
        "issuer_key_id": issuer_key.key_id,
        "granted_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = issuer_key.sign(_grant_bytes(payload))
    return payload


def _grant_bytes(grant: dict) -> bytes:
    return board_grant_canonical(
        grant["visitor_key_id"],
        grant["host_node"],
        grant["board"],
        int(grant["ring"]),
        grant["issuer_key_id"],
        int(grant["granted_at_unix_ms"]),
    )


def verify_board_grant(grant: dict) -> None:
    """Signature only. Whether the ISSUER was allowed to grant this ring is
    a node-policy question — see node.Node.accept_grant, which also enforces
    the introduction ceiling. A sound signature on an unauthorised grant is
    still an unauthorised grant.
    """
    load_public(grant["issuer_key_id"]).verify(
        bytes.fromhex(grant["signature"]), _grant_bytes(grant)
    )


# ── Eviction ───────────────────────────────────────────────────────────────


def sign_board_evict(
    speaker_key: KeyRecord,
    visitor_key_id: str,
    host_node: str,
    reason: str,
    now_ms: int | None = None,
) -> dict:
    payload = {
        "visitor_key_id": visitor_key_id.lower(),
        "host_node": host_node,
        "reason": reason,
        "speaker_key_id": speaker_key.key_id,
        "evicted_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = speaker_key.sign(_evict_bytes(payload))
    return payload


def _evict_bytes(ev: dict) -> bytes:
    return board_evict_canonical(
        ev["visitor_key_id"],
        ev["host_node"],
        ev["reason"],
        ev["speaker_key_id"],
        int(ev["evicted_at_unix_ms"]),
    )


def verify_board_evict(ev: dict) -> None:
    load_public(ev["speaker_key_id"]).verify(
        bytes.fromhex(ev["signature"]), _evict_bytes(ev)
    )


__all__ = [
    "start_key_intro",
    "countersign_key_intro",
    "verify_key_intro",
    "open_speaker_election",
    "sign_speaker_election",
    "verify_speaker_election",
    "sign_board_grant",
    "verify_board_grant",
    "sign_board_evict",
    "verify_board_evict",
    "RING_WRITE",
    "RING_NODE",
]
