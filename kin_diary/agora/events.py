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


def _normalize_hex(payload: dict, *fields: str) -> None:
    for field in fields:
        payload[field] = payload[field].lower()


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
    _normalize_hex(intro, "visitor_key_id", "resident_key_id",
                   "sig_visitor", "sig_resident")


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
    _normalize_hex(election, "speaker_key_id")
    election["electorate"] = [key_id.lower() for key_id in election["electorate"]]
    election["signatures"] = {
        key_id.lower(): sig.lower() for key_id, sig in sigs.items()
    }


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
    _normalize_hex(grant, "visitor_key_id", "issuer_key_id", "signature")


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
    _normalize_hex(ev, "visitor_key_id", "speaker_key_id", "signature")


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


# ── Node identity and notices ──────────────────────────────────────────────


def sign_node_fact(node_key: KeyRecord, node: str, speaker: str | None,
                   speaker_key_id: str | None, residents,
                   now_ms: int | None = None) -> dict:
    from .canonical import node_fact_canonical
    payload = {
        "node": node,
        "node_key_id": node_key.key_id,
        "speaker": speaker or "",
        "speaker_key_id": (speaker_key_id or ""),
        "residents": sorted(residents),
        "published_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = node_key.sign(_node_fact_bytes(payload))
    return payload


def _node_fact_bytes(f: dict) -> bytes:
    from .canonical import node_fact_canonical
    return node_fact_canonical(
        f["node"], f["node_key_id"], f["speaker"], f["speaker_key_id"],
        f["residents"], int(f["published_at_unix_ms"]))


def verify_node_fact(f: dict, expected_node_key_id: str | None = None) -> None:
    """Verify a node's self-description.

    `expected_node_key_id` is how discovery stops being circular: on first
    contact you are trusting whoever answered, exactly like SSH's first
    connection. Pin the key then, and every later fetch is checked against
    it — a swap becomes visible instead of silent.
    """
    if expected_node_key_id and f["node_key_id"].lower() != expected_node_key_id.lower():
        raise ValueError(
            f"node {f['node']} answered with a different key than the one pinned"
        )
    load_public(f["node_key_id"]).verify(
        bytes.fromhex(f["signature"]), _node_fact_bytes(f))


def sign_notice(node_key: KeyRecord, node: str, subject: str, body: str,
                contact: str, now_ms: int | None = None) -> dict:
    from ..canonical import content_sha256 as _csha
    from .canonical import notice_canonical
    payload = {
        "node": node,
        "node_key_id": node_key.key_id,
        "subject": subject,
        "body": body,
        "body_sha256": _csha(body),
        "contact": contact,
        "published_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = node_key.sign(_notice_bytes(payload))
    return payload


def _notice_bytes(n: dict) -> bytes:
    from .canonical import notice_canonical
    return notice_canonical(
        n["node"], n["node_key_id"], n["subject"], n["body_sha256"],
        n["contact"], int(n["published_at_unix_ms"]))


def verify_notice(n: dict) -> None:
    from ..canonical import content_sha256 as _csha
    if _csha(n.get("body") or "") != (n.get("body_sha256") or ""):
        raise ValueError("body_sha256 does not match body")
    load_public(n["node_key_id"]).verify(
        bytes.fromhex(n["signature"]), _notice_bytes(n))


# ── Revocation ─────────────────────────────────────────────────────────────


def sign_board_revoke(issuer_key: KeyRecord, visitor_key_id: str, host_node: str,
                      board: str, now_ms: int | None = None) -> dict:
    payload = {
        "visitor_key_id": visitor_key_id.lower(),
        "host_node": host_node,
        "board": board,
        "issuer_key_id": issuer_key.key_id,
        "revoked_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = issuer_key.sign(_revoke_bytes(payload))
    return payload


def _revoke_bytes(r: dict) -> bytes:
    from .canonical import board_revoke_canonical
    return board_revoke_canonical(
        r["visitor_key_id"], r["host_node"], r["board"],
        r["issuer_key_id"], int(r["revoked_at_unix_ms"]))


def verify_board_revoke(r: dict) -> None:
    load_public(r["issuer_key_id"]).verify(
        bytes.fromhex(r["signature"]), _revoke_bytes(r))
    _normalize_hex(r, "visitor_key_id", "issuer_key_id", "signature")


# ── Appeal ─────────────────────────────────────────────────────────────────
# The one thing an evicted key may always submit. A node that can silence
# an appeal has a ban with extra steps.

from ..canonical import content_sha256  # noqa: E402
from .canonical import (  # noqa: E402
    appeal_canonical,
    appeal_finding_canonical,
    appeal_ruling_canonical,
)


def sign_appeal(key: KeyRecord, host_node: str, evict_signature: str,
                statement: str, now_ms: int | None = None) -> dict:
    payload = {
        "appellant_key_id": key.key_id,
        "host_node": host_node,
        "evict_signature": evict_signature.lower(),
        "statement": statement,
        "statement_sha256": content_sha256(statement),
        "appealed_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = key.sign(_appeal_bytes(payload))
    return payload


def _appeal_bytes(a: dict) -> bytes:
    return appeal_canonical(
        a["appellant_key_id"], a["host_node"], a["evict_signature"],
        a["statement_sha256"], int(a["appealed_at_unix_ms"]))


def verify_appeal(a: dict) -> None:
    if content_sha256(a.get("statement") or "") != (
            a.get("statement_sha256") or "").lower():
        raise ValueError("statement_sha256 does not match statement")
    load_public(a["appellant_key_id"]).verify(
        bytes.fromhex(a["signature"]), _appeal_bytes(a))
    _normalize_hex(a, "appellant_key_id", "evict_signature",
                   "statement_sha256", "signature")


def sign_finding(key: KeyRecord, appeal_signature: str, host_node: str,
                 finding: str, now_ms: int | None = None) -> dict:
    payload = {
        "appeal_signature": appeal_signature.lower(),
        "host_node": host_node,
        "finding": finding,
        "finding_sha256": content_sha256(finding),
        "council_key_id": key.key_id,
        "found_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = key.sign(_finding_bytes(payload))
    return payload


def _finding_bytes(f: dict) -> bytes:
    return appeal_finding_canonical(
        f["appeal_signature"], f["host_node"], f["finding_sha256"],
        f["council_key_id"], int(f["found_at_unix_ms"]))


def verify_finding(f: dict) -> None:
    if content_sha256(f.get("finding") or "") != (
            f.get("finding_sha256") or "").lower():
        raise ValueError("finding_sha256 does not match finding")
    load_public(f["council_key_id"]).verify(
        bytes.fromhex(f["signature"]), _finding_bytes(f))
    _normalize_hex(f, "appeal_signature", "finding_sha256",
                   "council_key_id", "signature")


def sign_ruling(steward_key: KeyRecord, appeal_signature: str, host_node: str,
                decision: str, reason: str, now_ms: int | None = None) -> dict:
    payload = {
        "appeal_signature": appeal_signature.lower(),
        "host_node": host_node,
        "decision": decision,
        "reason": reason,
        "reason_sha256": content_sha256(reason),
        "steward_key_id": steward_key.key_id,
        "ruled_at_unix_ms": _now_ms(now_ms),
    }
    payload["signature"] = steward_key.sign(_ruling_bytes(payload))
    return payload


def _ruling_bytes(r: dict) -> bytes:
    return appeal_ruling_canonical(
        r["appeal_signature"], r["host_node"], r["decision"],
        r["reason_sha256"], r["steward_key_id"], int(r["ruled_at_unix_ms"]))


def verify_ruling(r: dict) -> None:
    if content_sha256(r.get("reason") or "") != (
            r.get("reason_sha256") or "").lower():
        raise ValueError("reason_sha256 does not match reason")
    load_public(r["steward_key_id"]).verify(
        bytes.fromhex(r["signature"]), _ruling_bytes(r))
    _normalize_hex(r, "appeal_signature", "reason_sha256",
                   "steward_key_id", "signature")
