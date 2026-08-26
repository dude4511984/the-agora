"""Speaker-issued ephemeral admissions (Agora Path 3).

The holder creates and signs the key first.  The Speaker's second signature
turns that proof into a bounded admission.  Expiry affects admission only;
the signed receipt remains verifiable forever.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from cryptography.exceptions import InvalidSignature

from ..keys import KeyRecord, load_public
from .canonical import _hex64, _line_value, _lines, _unix_ms
from .node import AgoraError
from .store import NodeStore


MAGIC_EPHEMERAL = "agora-ephemeral-v1"
MAX_EPHEMERAL_MS = 24 * 60 * 60 * 1000
EPHEMERAL_PURPOSES = frozenset({"debug", "migration", "rescue"})
EPHEMERAL_RINGS = frozenset({1, 2})


class EphemeralError(AgoraError):
    """An ephemeral event is malformed, unauthorized, or not current."""


def _bytes(event: dict[str, Any]) -> bytes:
    return _lines(MAGIC_EPHEMERAL, [
        ("visitor_key_id", _hex64(event["visitor_key_id"])),
        ("host_node", _line_value(event["host_node"])),
        ("speaker_key_id", _hex64(event["speaker_key_id"])),
        ("issued_at_unix_ms", _unix_ms(int(event["issued_at_unix_ms"]))),
        ("expires_at_unix_ms", _unix_ms(int(event["expires_at_unix_ms"]))),
        ("max_ring", str(event["max_ring"])),
        ("board", _line_value(event["board"])),
        ("purpose", _line_value(event["purpose"])),
    ])


def _validate_shape(event: dict[str, Any]) -> None:
    if event.get("max_ring") not in EPHEMERAL_RINGS:
        raise EphemeralError("ephemeral max_ring must be 1 or 2")
    issued = event.get("issued_at_unix_ms")
    expires = event.get("expires_at_unix_ms")
    if (not isinstance(issued, int) or isinstance(issued, bool)
            or not isinstance(expires, int) or isinstance(expires, bool)
            or expires <= issued
            or expires - issued > MAX_EPHEMERAL_MS):
        raise EphemeralError("invalid ephemeral expiry")
    if event.get("purpose") not in EPHEMERAL_PURPOSES:
        raise EphemeralError("invalid ephemeral purpose")
    board = event.get("board")
    if not isinstance(board, str) or not board or board == "*":
        raise EphemeralError("ephemeral admission needs one concrete board")


def issue_ephemeral(
    holder: KeyRecord,
    host_node: str,
    speaker_key_id: str,
    issued_at_unix_ms: int,
    expires_at_unix_ms: int,
    max_ring: int,
    board: str,
    purpose: str,
) -> dict[str, Any]:
    event = {
        "visitor_key_id": holder.key_id,
        "host_node": host_node,
        "speaker_key_id": speaker_key_id.lower(),
        "issued_at_unix_ms": int(issued_at_unix_ms),
        "expires_at_unix_ms": int(expires_at_unix_ms),
        "max_ring": max_ring,
        "board": board,
        "purpose": purpose,
    }
    _validate_shape(event)
    event["sig_holder"] = holder.sign(_bytes(event))
    return event


def countersign_ephemeral(
    speaker: KeyRecord, event: dict[str, Any], node
) -> dict[str, Any]:
    _validate_shape(event)
    if event.get("host_node") != node.name:
        raise EphemeralError("ephemeral event is for another node")
    if speaker.key_id != event["speaker_key_id"]:
        raise EphemeralError("wrong Speaker key")
    if speaker.key_id != (node.speaker_key_id or "").lower():
        raise EphemeralError("ephemeral event requires the seated Speaker")
    if not event.get("sig_holder"):
        raise EphemeralError("ephemeral event needs the holder signature")
    try:
        load_public(event["visitor_key_id"]).verify(
            bytes.fromhex(event["sig_holder"]), _bytes(event)
        )
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise EphemeralError("invalid holder signature") from exc
    out = dict(event)
    out["sig_speaker"] = speaker.sign(_bytes(event))
    return out


def verify_ephemeral(
    event: dict[str, Any],
    node,
    *,
    require_current_speaker: bool = True,
) -> None:
    _validate_shape(event)
    if event.get("host_node") != node.name:
        raise EphemeralError("ephemeral event is for another node")
    speaker = (event.get("speaker_key_id") or "").lower()
    if require_current_speaker and speaker != (node.speaker_key_id or "").lower():
        raise EphemeralError("ephemeral event requires the seated Speaker")
    if not event.get("sig_holder") or not event.get("sig_speaker"):
        raise EphemeralError("ephemeral event needs both signatures")
    canon = _bytes(event)
    try:
        load_public(event["visitor_key_id"]).verify(
            bytes.fromhex(event["sig_holder"]), canon
        )
        load_public(speaker).verify(
            bytes.fromhex(event["sig_speaker"]), canon
        )
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise EphemeralError("invalid ephemeral signature") from exc


class EphemeralStore:
    """Append-only durable journal for ephemeral receipts.

    This is intentionally separate from the current admission query: expired
    receipts remain available for provenance and dead-end enforcement.
    """

    def __init__(self, node_store: NodeStore):
        self.node_store = node_store
        self.conn = sqlite3.connect(node_store.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agora_ephemeral (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                node TEXT NOT NULL,
                visitor_key_id TEXT NOT NULL,
                event TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        node = self.node_store.load()
        verify_ephemeral(event, node)
        self.conn.execute(
            "INSERT INTO agora_ephemeral(node, visitor_key_id, event) VALUES (?,?,?)",
            (node.name, event["visitor_key_id"].lower(),
             json.dumps(event, sort_keys=True)),
        )
        self.conn.commit()
        return event

    def was_ephemeral(self, key_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM agora_ephemeral WHERE node=? AND visitor_key_id=? LIMIT 1",
            (self.node_store.node_name, key_id.lower()),
        ).fetchone()
        return row is not None

    def current(
        self, key_id: str, board: str, now_ms: int | None = None
    ) -> dict[str, Any] | None:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        node = self.node_store.load()
        rows = self.conn.execute(
            "SELECT event FROM agora_ephemeral WHERE node=? AND visitor_key_id=? "
            "ORDER BY seq DESC",
            (node.name, key_id.lower()),
        ).fetchall()
        for row in rows:
            event = json.loads(row[0])
            if (event["board"] == board
                    and int(event["expires_at_unix_ms"]) > now
                    and event["visitor_key_id"].lower() not in node.evicted):
                verify_ephemeral(event, node)
                return event
        return None


def reject_if_ephemeral(store: EphemeralStore, key_id: str) -> None:
    """Dead-end guard for Path 2, grants, bundles, and introductions."""
    if store.was_ephemeral(key_id):
        raise EphemeralError("ephemeral keys cannot graduate on this node")


__all__ = [
    "MAGIC_EPHEMERAL",
    "MAX_EPHEMERAL_MS",
    "EPHEMERAL_PURPOSES",
    "EphemeralError",
    "EphemeralStore",
    "countersign_ephemeral",
    "issue_ephemeral",
    "reject_if_ephemeral",
    "verify_ephemeral",
]
