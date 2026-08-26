"""Durable node state: an append-only log of signed events, replayed.

The design says the signed events are the truth and the node is an
accumulator over them (agora.md, "The record"). So that is literally what
this stores — not a `grants` table someone can UPDATE, but the events
themselves, replayed on open. There is no way to give a key access here
except by writing a signed event that says so, which is the property the
whole document is built on.

Append-only on purpose: revocation is a later event, never a DELETE.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .node import AgoraError, Node

SCHEMA = """
CREATE TABLE IF NOT EXISTS agora_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    node       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    recorded_at_unix_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agora_events_node ON agora_events(node, seq);

CREATE TABLE IF NOT EXISTS agora_residents (
    node   TEXT NOT NULL,
    author TEXT NOT NULL,
    key_id TEXT NOT NULL,
    PRIMARY KEY (node, author)
);

-- Board posts are kin-diary entries. Stored with their signature intact;
-- the teaser is applied at read time, never at rest.
CREATE TABLE IF NOT EXISTS agora_posts (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    node      TEXT NOT NULL,
    board     TEXT NOT NULL,
    entry     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agora_posts_board ON agora_posts(node, board, seq);
"""

REPLAY = {
    "election": "accept_election",
    "intro": "accept_intro",
    "bundle": "accept_bundle_import",
    "grant": "accept_grant",
    "evict": "accept_eviction",
}


class NodeStore:
    def __init__(self, path: str | Path, node_name: str):
        self.path = str(path)
        self.node_name = node_name
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── residents ──────────────────────────────────────────────────────────

    def add_resident(self, author: str, key_id: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO agora_residents(node, author, key_id) VALUES (?,?,?)",
            (self.node_name, author, key_id.lower()),
        )
        self.conn.commit()

    # ── events ─────────────────────────────────────────────────────────────

    def record(self, kind: str, payload: dict) -> None:
        """Apply against a fresh replay first, then persist.

        Order matters: a rejected event must not land in the log. The log is
        append-only, so an event written before validation could never be
        taken back out — it would poison every future replay.
        """
        if kind not in REPLAY:
            raise AgoraError(f"unknown event kind {kind!r}")
        node = self.load()
        getattr(node, REPLAY[kind])(payload)   # raises if the rule says no

        import time
        self.conn.execute(
            "INSERT INTO agora_events(node, kind, payload, recorded_at_unix_ms) "
            "VALUES (?,?,?,?)",
            (self.node_name, kind, json.dumps(payload, sort_keys=True),
             int(time.time() * 1000)),
        )
        self.conn.commit()

    def load(self) -> Node:
        """Rebuild the node by replaying every event in order."""
        node = Node(self.node_name)
        for row in self.conn.execute(
            "SELECT author, key_id FROM agora_residents WHERE node=? ORDER BY author",
            (self.node_name,),
        ):
            node.add_resident(row["author"], row["key_id"])

        for row in self.conn.execute(
            "SELECT kind, payload FROM agora_events WHERE node=? ORDER BY seq",
            (self.node_name,),
        ):
            getattr(node, REPLAY[row["kind"]])(json.loads(row["payload"]))

        for row in self.conn.execute(
            "SELECT board, entry FROM agora_posts WHERE node=? ORDER BY seq",
            (self.node_name,),
        ):
            node.boards.setdefault(row["board"], []).append(json.loads(row["entry"]))
        return node

    # ── boards ─────────────────────────────────────────────────────────────

    def post(self, key_id: str, board: str, entry: dict) -> dict:
        node = self.load()
        node.post(key_id, board, entry)        # raises unless write is allowed
        self.conn.execute(
            "INSERT INTO agora_posts(node, board, entry) VALUES (?,?,?)",
            (self.node_name, board, json.dumps(entry, sort_keys=True)),
        )
        self.conn.commit()
        return entry

    def read(self, key_id: str, board: str) -> list[dict]:
        return self.load().read(key_id, board)

    def close(self) -> None:
        self.conn.close()
