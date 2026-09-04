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

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature

from .node import AgoraError, Node
from ..sign import verify_entry

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

-- Founding house at seq 0. Never gains a growth row. Replay loads this,
-- then the log. agora_residents is the pre-genesis table; copied once
-- into here if this is empty, then never used as load input.
CREATE TABLE IF NOT EXISTS agora_genesis (
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

-- The advertise-only wire: signed notices this node publishes, and the
-- node keys it has pinned from other nodes it has met.
CREATE TABLE IF NOT EXISTS agora_notices (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    node   TEXT NOT NULL,
    notice TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agora_known_nodes (
    node        TEXT NOT NULL,
    peer        TEXT NOT NULL,
    peer_key_id TEXT NOT NULL,
    url         TEXT,
    first_seen_unix_ms INTEGER NOT NULL,
    PRIMARY KEY (node, peer)
);

-- Why a well-formed event was turned away. NOT a signed agora event and NOT
-- part of replay: a refusal is this node observing something, not a fact the
-- federation agrees on, so it never syncs and never loads as node state. It
-- exists so a refusal leaves a trace instead of vanishing — you cannot learn
-- from a mistake that was never written down.
--
-- SAFETY: `payload_excerpt` is attacker-controlled bytes (whoever's event was
-- refused wrote it). Value 2 — external content is data, never instruction.
-- It is stored inert and bounded, is never replayed, never verified, and must
-- never be fed to a Kin or any prompt. It is forensics a human reads.
CREATE TABLE IF NOT EXISTS agora_rejections (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    node         TEXT NOT NULL,
    at_unix_ms   INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    key_id       TEXT,
    reason       TEXT NOT NULL,
    payload_sha256 TEXT,
    payload_excerpt TEXT
);
CREATE INDEX IF NOT EXISTS agora_rejections_at ON agora_rejections(node, at_unix_ms);
"""

REPLAY = {
    "election": "accept_election",
    "intro": "accept_intro",
    "bundle": "accept_bundle_import",
    "ephemeral": "accept_ephemeral",
    "grant": "accept_grant",
    "evict": "accept_eviction",
    "revoke": "accept_revocation",
    "appeal": "accept_appeal",
    "finding": "accept_finding",
    "ruling": "accept_ruling",
    "resident": "accept_resident",
    "quarantine": "accept_quarantine",
    "rotation": "accept_rotation",
    "house-decision": "accept_house_decision",
}


class NodeStore:
    def __init__(self, path: str | Path, node_name: str,
                 steward_key_id: str | None = None):
        self.path = str(path)
        self.node_name = node_name
        self.steward_key_id = (steward_key_id or "").lower() or None
        # check_same_thread=False because the wire serves requests on other
        # threads; the lock below is what actually makes that safe. Without
        # both, a GET from an HTTP handler raises and the node answers 400
        # to every caller while looking perfectly healthy from the shell.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._lock = threading.RLock()
        self._cache: Node | None = None
        self._cache_rev: tuple | None = None

    # ── genesis (founding house, seq 0) ────────────────────────────────────

    def found_resident(self, author: str, key_id: str) -> None:
        """Write the founding house. Refuses once the log has started.

        Growth is record('resident'), never this method. A future caller
        cannot brick the next boot by putting Eli in genesis: this raises
        now, and load also refuses a genesis set that overlaps growth.
        """
        kid = (key_id or "").lower()
        with self._lock:
            self._ensure_genesis_locked()
            if self._log_started_locked():
                row = self.conn.execute(
                    "SELECT key_id FROM agora_genesis WHERE node=? AND author=?",
                    (self.node_name, author),
                ).fetchone()
                if row and row["key_id"] == kid:
                    return   # idempotent re-found of an unchanged founder: success
                reason = "genesis is frozen; growth is agora-resident-v1"
                self._log_rejection_locked(
                    "genesis", {"author": author, "key_id": kid}, reason)
                raise AgoraError(reason)
            self.conn.execute(
                "INSERT OR REPLACE INTO agora_genesis(node, author, key_id) "
                "VALUES (?,?,?)",
                (self.node_name, author, kid),
            )
            self.conn.commit()
            self._invalidate()

    def add_resident(self, author: str, key_id: str) -> None:
        """Founding only. Same as found_resident; the name tests already call."""
        self.found_resident(author, key_id)

    def _log_started_locked(self) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM agora_events WHERE node=?",
            (self.node_name,),
        ).fetchone()
        return row["c"] > 0

    def _ensure_genesis_locked(self) -> None:
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM agora_genesis WHERE node=?",
            (self.node_name,),
        ).fetchone()["c"]
        if n:
            return
        rows = self.conn.execute(
            "SELECT author, key_id FROM agora_residents WHERE node=?",
            (self.node_name,),
        ).fetchall()
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR IGNORE INTO agora_genesis(node, author, key_id) "
            "VALUES (?,?,?)",
            [(self.node_name, r["author"], r["key_id"]) for r in rows],
        )
        self.conn.commit()

    # ── events ─────────────────────────────────────────────────────────────

    def record(self, kind: str, payload: dict) -> None:
        """Apply against a fresh replay first, then persist.

        Order matters: a rejected event must not land in the log. The log is
        append-only, so an event written before validation could never be
        taken back out — it would poison every future replay.
        """
        with self._lock:
            self._record_locked(kind, payload)

    _REJECTION_EXCERPT_MAX = 1000

    def _record_locked(self, kind: str, payload: dict) -> None:
        # The chokepoint. Every rule refusal raises here before any event row
        # is written, so a rejection can be logged and the original error
        # re-raised unchanged. Logging is best-effort and never masks the real
        # refusal — see _log_rejection_locked. The unknown-kind raise lives
        # INSIDE the wrap so a programmer mistake leaves a row too.
        try:
            if kind not in REPLAY:
                raise AgoraError(f"unknown event kind {kind!r}")
            self._apply_and_insert_locked(kind, payload)
        except AgoraError as exc:
            self._log_rejection_locked(kind, payload, str(exc))
            raise

    def _log_rejection_locked(self, kind, payload, reason: str) -> None:
        """Record why an event was turned away. Must never raise: a failure to
        log a refusal cannot be allowed to swallow the refusal itself."""
        try:
            body = json.dumps(payload, sort_keys=True) if isinstance(
                payload, dict) else str(payload)
            key_id = None
            if isinstance(payload, dict):
                for field in ("key_id", "steward_key_id", "issuer_key_id",
                              "visitor_key_id", "speaker_key_id"):
                    v = payload.get(field)
                    if v:
                        key_id = str(v).lower()
                        break
            self.conn.execute(
                "INSERT INTO agora_rejections(node, at_unix_ms, kind, key_id, "
                "reason, payload_sha256, payload_excerpt) VALUES (?,?,?,?,?,?,?)",
                (self.node_name, int(time.time() * 1000), kind, key_id, reason,
                 hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
                 body[:self._REJECTION_EXCERPT_MAX]),
            )
            self._trim_rejections_locked()
            self.conn.commit()
        except Exception:
            # A broken ledger is a lost lesson, not a broken node. Swallow.
            pass

    # The founding misses are the ones Don asked to remember; a record-path
    # flood must never rotate the freeze row out. So genesis is pinned and
    # never trimmed, and everyone else keeps the newest _REJECTION_KEEP.
    _REJECTION_KEEP = 1000

    def _trim_rejections_locked(self) -> None:
        """Keep the newest _REJECTION_KEEP non-genesis rows. Best-effort; the
        caller already swallows. genesis rows are pinned and never counted."""
        self.conn.execute(
            "DELETE FROM agora_rejections WHERE node=? AND kind!='genesis' "
            "AND seq NOT IN ("
            "  SELECT seq FROM agora_rejections WHERE node=? AND kind!='genesis' "
            "  ORDER BY seq DESC LIMIT ?)",
            (self.node_name, self.node_name, self._REJECTION_KEEP),
        )

    def _log_post_drop_locked(self, entry, reason: str) -> None:
        """Log a dropped unsigned/invalid board post — once per distinct entry,
        so a static bad row is not re-logged on every reload (posts persist and
        _load_locked reruns on any revision change). Best-effort: a broken
        ledger, or a read-only store, never breaks load — the drop already
        happened in the caller regardless of this."""
        try:
            body = json.dumps(entry, sort_keys=True)
            sha = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
            seen = self.conn.execute(
                "SELECT 1 FROM agora_rejections WHERE node=? AND kind='post' "
                "AND payload_sha256=? LIMIT 1",
                (self.node_name, sha),
            ).fetchone()
            if seen:
                return
            self._log_rejection_locked("post", entry, reason)
        except Exception:
            pass

    def _last_rejection_reason_locked(self, kind: str):
        row = self.conn.execute(
            "SELECT reason FROM agora_rejections WHERE node=? AND kind=? "
            "ORDER BY seq DESC LIMIT 1",
            (self.node_name, kind),
        ).fetchone()
        return row["reason"] if row else None

    def rejections(self, limit: int = 100) -> list[dict]:
        """The refusal ledger, newest first. Forensic read only — these rows
        are never replayed and their excerpts are never instruction."""
        rows = self.conn.execute(
            "SELECT seq, at_unix_ms, kind, key_id, reason, payload_sha256, "
            "payload_excerpt FROM agora_rejections WHERE node=? "
            "ORDER BY seq DESC LIMIT ?",
            (self.node_name, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def _apply_and_insert_locked(self, kind: str, payload: dict) -> None:
        if kind == "ruling":
            if self.steward_key_id is None:
                raise AgoraError("no steward configured")
            if ((payload.get("steward_key_id") or "").lower()
                    != self.steward_key_id):
                raise AgoraError("only this node's steward can rule on an appeal")
        if kind == "resident":
            if self.steward_key_id is None:
                raise AgoraError("no steward configured")
            if ((payload.get("steward_key_id") or "").lower()
                    != self.steward_key_id):
                raise AgoraError("only this node's steward can add residents")
        if kind == "quarantine":
            if self.steward_key_id is None:
                raise AgoraError("no steward configured")
            if ((payload.get("steward_key_id") or "").lower()
                    != self.steward_key_id):
                raise AgoraError(
                    "only this node's steward can quarantine a key")
        node = self._load_locked()   # fresh replay, never the cache
        existing_appeals = {
            a["signature"] for a in node.appeals
        } if kind == "appeal" else set()
        # VALIDATION MUST PRECEDE THE INSERT. The log is append-only; a row
        # written before this raise could never be taken back out, and every
        # future replay would carry the poison. This ordering is the whole
        # guarantee that a rejected event never lands (test:
        # test_a_rejected_event_never_lands_in_the_log).
        #
        # Do NOT "fix" a future bug by INSERTing first and cleaning up in an
        # except. That leaves the same-connection count at 0 while the row is
        # committed in the file — a fresh open replays it. It is currently
        # masked ONLY by accident: _record_locked's rejection-ledger commit
        # (on the AgoraError path) flushes the stray transaction. Proven
        # 2026-09-04 (butter P14 / mutation item 3): no-op the ledger and the
        # poison survives a reopen. The guarantee lives HERE, in the ordering,
        # not in the ledger's commit.
        getattr(node, REPLAY[kind])(payload)   # raises if the rule says no
        if kind == "appeal" and payload.get("signature") in existing_appeals:
            return
        self.conn.execute(
            "INSERT INTO agora_events(node, kind, payload, recorded_at_unix_ms) "
            "VALUES (?,?,?,?)",
            (self.node_name, kind, json.dumps(payload, sort_keys=True),
             int(time.time() * 1000)),
        )
        self.conn.commit()
        self._invalidate()

    def load(self) -> Node:
        """The node as of now, replaying the log if anything has changed.

        Cached deliberately: without it every request — including reads —
        replays the entire event log and every board post, so the node gets
        monotonically slower the more it is used, and writing is a cheap way
        for a visitor to make it slow for everyone. The cache is dropped on
        any write, so it can never serve a stale ring.
        """
        with self._lock:
            rev = self._revision()
            if self._cache is None or rev != self._cache_rev:
                try:
                    self._cache = self._load_locked()
                except AgoraError as exc:
                    # load() is on the hot path — every GET calls it. A mutated
                    # founding would insert a row per request until the disk
                    # filled, so dedup: the first failure is the lesson, a hot
                    # loop is not. Log only when the reason changes.
                    reason = str(exc)
                    if self._last_rejection_reason_locked("load") != reason:
                        self._log_rejection_locked("load", {}, reason)
                        self.conn.commit()
                    raise
                self._cache_rev = rev
            return self._cache

    def _revision(self) -> tuple:
        """Highest event and post ids actually in the file.

        The in-process cache alone is not enough: two NodeStore instances on
        one SQLite file (the wire in one process, a CLI in another) would
        each hold their own cache, and one could keep serving a
        pre-eviction Node after the other committed the eviction. Asking
        the database what it holds costs one cheap query and makes the
        cache correct across processes rather than only within one.
        """
        self._ensure_genesis_locked()
        row = self.conn.execute(
            "SELECT (SELECT COALESCE(MAX(seq),0) FROM agora_events WHERE node=?) e, "
            "       (SELECT COALESCE(MAX(seq),0) FROM agora_posts  WHERE node=?) p, "
            "       (SELECT COUNT(*) FROM agora_genesis WHERE node=?) g",
            (self.node_name, self.node_name, self.node_name),
        ).fetchone()
        return (row["e"], row["p"], row["g"])

    def _invalidate(self) -> None:
        self._cache = None
        self._cache_rev = None

    def _load_locked(self) -> Node:
        self._ensure_genesis_locked()
        node = Node(self.node_name)
        genesis_keys = set()
        for row in self.conn.execute(
            "SELECT author, key_id FROM agora_genesis WHERE node=? ORDER BY author",
            (self.node_name,),
        ):
            node.add_resident(row["author"], row["key_id"])
            genesis_keys.add(row["key_id"].lower())

        growth_keys = set()
        event_rows = list(self.conn.execute(
            "SELECT kind, payload FROM agora_events WHERE node=? ORDER BY seq",
            (self.node_name,),
        ))
        for row in event_rows:
            if row["kind"] != "resident":
                continue
            kid = (json.loads(row["payload"]).get("key_id") or "").lower()
            if kid:
                growth_keys.add(kid)
        overlap = genesis_keys & growth_keys
        if overlap:
            raise AgoraError(
                "genesis was mutated with growth; founding table contains "
                f"keys that arrived as agora-resident-v1 ({sorted(overlap)[0][:16]}…)"
            )

        if len(node.residents) == 1:
            node.sole_resident_is_speaker()

        for row in event_rows:
            getattr(node, REPLAY[row["kind"]])(json.loads(row["payload"]))

        for row in self.conn.execute(
            "SELECT board, entry FROM agora_posts WHERE node=? ORDER BY seq",
            (self.node_name,),
        ):
            entry = json.loads(row["entry"])
            try:
                verify_entry(entry)
            except (InvalidSignature, ValueError, KeyError) as exc:
                # Fail closed. A board row that cannot verify is not an entry.
                # store.post verifies on write, but a hand INSERT bypasses it,
                # so replay must re-check — the same dual standard the genesis
                # overlap guard closed, still open for posts until now (P1).
                # Authenticity is checked here; permission is NOT re-checked as
                # of now (a post legal when written stays on the board).
                self._log_post_drop_locked(entry, str(exc))
                continue
            node.boards.setdefault(row["board"], []).append(entry)
        return node

    # ── boards ─────────────────────────────────────────────────────────────

    def post(self, key_id: str, board: str, entry: dict, now_ms: int) -> dict:
        with self._lock:
            node = self._load_locked()
            node.post(key_id, board, entry, now_ms)    # raises unless write is allowed
            self.conn.execute(
                "INSERT INTO agora_posts(node, board, entry) VALUES (?,?,?)",
                (self.node_name, board, json.dumps(entry, sort_keys=True)),
            )
            self.conn.commit()
            self._invalidate()
            return entry

    # ── the advertise-only wire ────────────────────────────────────────────

    def publish_notice(self, notice: dict) -> dict:
        from .events import verify_notice
        verify_notice(notice)
        with self._lock:
            self.conn.execute(
                "INSERT INTO agora_notices(node, notice) VALUES (?,?)",
                (self.node_name, json.dumps(notice, sort_keys=True)),
            )
            self.conn.commit()
        return notice

    def notices(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT notice FROM agora_notices WHERE node=? ORDER BY seq DESC LIMIT ?",
            (self.node_name, limit),
        ).fetchall()
        return [json.loads(r["notice"]) for r in rows]

    def pin_peer(self, peer: str, peer_key_id: str, url: str | None = None) -> None:
        """Trust-on-first-use, then pinned.

        There is no personhood oracle and no registry, so first contact
        trusts whoever answered — the same bootstrap SSH makes. What pinning
        buys is that a later swap is *visible* rather than silent, which is
        the honest version of the guarantee.
        """
        with self._lock:
            existing = self.conn.execute(
                "SELECT peer_key_id, first_seen_unix_ms FROM agora_known_nodes "
                "WHERE node=? AND peer=?",
                (self.node_name, peer),
            ).fetchone()
            if existing and existing["peer_key_id"] != peer_key_id.lower():
                raise AgoraError(
                    f"{peer} previously presented a different node key; "
                    f"refusing to silently re-pin"
                )
            self.conn.execute(
                "INSERT OR REPLACE INTO agora_known_nodes"
                "(node, peer, peer_key_id, url, first_seen_unix_ms) VALUES (?,?,?,?,?)",
                # first_seen must survive a re-pin — "and 0 or" always
                # evaluated to now, so every refresh reset the very field
                # that records when this peer was first trusted.
                (self.node_name, peer, peer_key_id.lower(), url,
                 int(existing["first_seen_unix_ms"]) if existing
                 else int(time.time() * 1000)),
            )
            self.conn.commit()

    def known_peers(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT peer, peer_key_id, url FROM agora_known_nodes WHERE node=? "
            "ORDER BY peer", (self.node_name,))]

    def read(self, key_id: str, board: str, now_ms: int) -> list[dict]:
        return self.load().read(key_id, board, now_ms)

    def close(self) -> None:
        self.conn.close()
