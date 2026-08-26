"""Local, signed presence submission and persistence.

Presence is deliberately not an event-log record.  It is a replaceable,
expiring observation, so the current marker belongs in a small table keyed by
node and key rather than in the append-only history of policy decisions.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .node import AgoraError
from .places import Atlas, verify_presence
from .store import NodeStore


PRESENCE_ERROR = "presence refused"


class PresenceError(AgoraError):
    """A presence submission or persisted marker is invalid."""


class PresenceStore:
    """Persistent current presence, separate from the append-only event log."""

    def __init__(self, node_store: NodeStore):
        self.node_store = node_store
        self.conn = sqlite3.connect(node_store.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agora_presence (
                node TEXT NOT NULL,
                key_id TEXT NOT NULL,
                presence TEXT NOT NULL,
                PRIMARY KEY (node, key_id)
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def save(self, presence: dict[str, Any]) -> None:
        node = presence["node"]
        key_id = presence["key_id"].lower()
        self.conn.execute(
            """
            INSERT INTO agora_presence(node, key_id, presence)
            VALUES (?, ?, ?)
            ON CONFLICT(node, key_id) DO UPDATE SET presence=excluded.presence
            """,
            (node, key_id, json.dumps(presence, sort_keys=True)),
        )
        self.conn.commit()

    def restore(self, atlas: Atlas, now_ms: int | None = None) -> list[dict]:
        """Restore only current, valid, locally authorized markers."""
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        node = self.node_store.load()
        rows = self.conn.execute(
            "SELECT key_id, presence FROM agora_presence WHERE node=?",
            (node.name,),
        ).fetchall()
        restored = []
        for row in rows:
            presence = json.loads(row["presence"] if isinstance(row, sqlite3.Row)
                                  else row[1])
            key_id = presence.get("key_id", "").lower()
            if (int(presence.get("expires_at_unix_ms", 0)) <= now
                    or key_id in node.evicted
                    or not _introduced(node, key_id)):
                self._delete(node.name, key_id)
                continue
            if presence.get("node") != node.name:
                raise PresenceError(PRESENCE_ERROR)
            verify_presence(presence)
            atlas.arrive(presence)
            restored.append(presence)
        return restored

    def _delete(self, node: str, key_id: str) -> None:
        self.conn.execute(
            "DELETE FROM agora_presence WHERE node=? AND key_id=?",
            (node, key_id),
        )
        self.conn.commit()


def _introduced(node, key_id: str) -> bool:
    return node.resident_for_key(key_id) is not None or key_id in node.visitor_ceiling


def accept_presence(
    node_store: NodeStore,
    atlas: Atlas,
    presence_store: PresenceStore,
    proven_key_id: str,
    presence: dict[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Verify and persist one self-signed presence for this local node."""
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    node = node_store.load()
    signer = (proven_key_id or "").lower()
    key_id = (presence.get("key_id") or "").lower()
    expires = presence.get("expires_at_unix_ms")
    if (
        not isinstance(expires, int)
        or isinstance(expires, bool)
        or expires <= now
        or key_id != signer
        or presence.get("node") != node.name
        or not _introduced(node, key_id)
    ):
        raise PresenceError(PRESENCE_ERROR)
    try:
        verify_presence(presence)
        atlas.arrive(presence)
    except Exception as exc:
        raise PresenceError(PRESENCE_ERROR) from exc
    presence_store.save(presence)
    return presence


__all__ = ["PRESENCE_ERROR", "PresenceError", "PresenceStore", "accept_presence"]
