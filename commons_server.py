"""The Commons, v1 — SPEC_commons.md. Open reading, known-key-only
posting, ads only.

Runs standalone: its own SQLite file, its own port. It never touches a
node's own storage — "known keys" is a flat local list, not a live
query against any node. Signatures reuse kin_diary.agora.wire's
existing request-signing scheme (the same one nodes use for reads and
writes), with host_node="Commons". No second signature scheme.

The firewall is the shape: three plain-text fields, short, no markup,
no attachments. An artifact cannot fit through a 280-character slot.
Enforced here, not by trust in what a caller claims to be posting.

stdlib only, same reason wire.py gives: a node — or a lobby next to
one — should not need a dependency tree to answer the door.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from kin_diary.agora.node import AgoraError
from kin_diary.agora.wire import ANONYMOUS, identify

HOST_NODE = "Commons"

UNSAFE_LABEL = (
    "UNSAFE. Open to anyone, nothing here is verified. Ads only — what "
    "you're working on, why, how to ask in. Never the artifact itself."
)

MAX_WHAT = 120
MAX_WHY = 280
MAX_HOW_TO_ASK = 200
POST_TTL_MS = 14 * 24 * 60 * 60 * 1000

RATE_PER_KEY_WINDOW_MS = 10 * 60 * 1000
RATE_PER_KEY_MAX = 1
RATE_PER_IP_WINDOW_MS = 24 * 60 * 60 * 1000
RATE_PER_IP_MAX = 20

MAX_BODY_BYTES = 8192  # a post is three short fields, not a payload

DEFAULT_DB_PATH = Path.home() / ".config" / "kin_diary" / "commons.db"
DEFAULT_KNOWN_KEYS_PATH = Path.home() / ".config" / "kin_diary" / "commons_known_keys.json"


class CommonsError(Exception):
    """Refused for a stated reason. Every raise site names one."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ── storage ──────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    what TEXT,
    why TEXT,
    how_to_ask TEXT,
    key_id TEXT NOT NULL,
    posted_at_unix_ms INTEGER NOT NULL,
    expires_at_unix_ms INTEGER NOT NULL,
    hidden_reason TEXT,
    hidden_at_unix_ms INTEGER
);
"""


class CommonsStore:
    """One SQLite file, its own. A connection per call: this is an ads
    board, a few writes an hour at most, not a hot path worth a pool.
    """

    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as c:
            c.execute(_SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def sweep_expired(self, now_ms=None) -> int:
        """Expired, non-hidden posts are tombstoned, not deleted.
        SPEC_commons.md item 5 originally called for a hard delete here;
        Marvin's still-open reasoning ("the log does not DELETE") holds
        that until his ruling lands, so expiry now leaves the same shape
        of record a steward hide does — reason "expired" — rather than
        removing the row. A post already hidden by a steward keeps its
        own reason: this only touches rows with hidden_reason IS NULL.
        """
        now = _now_ms() if now_ms is None else now_ms
        with self._conn() as c:
            cur = c.execute(
                "UPDATE posts SET hidden_reason='expired', hidden_at_unix_ms=? "
                "WHERE hidden_reason IS NULL AND expires_at_unix_ms <= ?",
                (now, now))
            return cur.rowcount

    def list_posts(self, now_ms=None) -> list[dict]:
        self.sweep_expired(now_ms)
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, what, why, how_to_ask, key_id, posted_at_unix_ms, "
                "expires_at_unix_ms, hidden_reason, hidden_at_unix_ms "
                "FROM posts ORDER BY posted_at_unix_ms DESC"
            ).fetchall()
        out = []
        for (pid, what, why, how_to_ask, key_id, posted_at, expires_at,
             hidden_reason, hidden_at) in rows:
            post = {
                "id": pid, "key_id": key_id,
                "posted_at_unix_ms": posted_at,
                "expires_at_unix_ms": expires_at,
            }
            if hidden_reason is not None:
                post["what"] = post["why"] = post["how_to_ask"] = None
                post["hidden"] = {"reason": hidden_reason, "at_unix_ms": hidden_at}
            else:
                post["what"], post["why"], post["how_to_ask"] = what, why, how_to_ask
                post["hidden"] = None
            out.append(post)
        return out

    def insert_post(self, key_id, what, why, how_to_ask, now_ms=None) -> str:
        now = _now_ms() if now_ms is None else now_ms
        post_id = uuid.uuid4().hex
        with self._conn() as c:
            c.execute(
                "INSERT INTO posts (id, what, why, how_to_ask, key_id, "
                "posted_at_unix_ms, expires_at_unix_ms) VALUES (?,?,?,?,?,?,?)",
                (post_id, what, why, how_to_ask, key_id, now, now + POST_TTL_MS))
        return post_id

    def hide_post(self, post_id, reason, now_ms=None) -> bool:
        """Steward hide. A tombstone stays, always — nothing disappears
        silently, including from this call: it returns False, not an
        exception, for an id that no longer exists, but never hides
        without a stated reason.
        """
        if not reason or not reason.strip():
            raise ValueError("hide requires a stated reason")
        now = _now_ms() if now_ms is None else now_ms
        with self._conn() as c:
            cur = c.execute(
                "UPDATE posts SET hidden_reason=?, hidden_at_unix_ms=? WHERE id=?",
                (reason.strip(), now, post_id))
            return cur.rowcount > 0


# ── the ad shape — the firewall ─────────────────────────────────────────

_CONTROL = frozenset(chr(c) for c in list(range(0, 32)) + [127])
_AD_FIELDS = {"what": MAX_WHAT, "why": MAX_WHY, "how_to_ask": MAX_HOW_TO_ASK}


def _clean_field(value, max_len, name) -> str:
    if not isinstance(value, str):
        raise CommonsError(f"{name} must be a string")
    v = _nfc(value).strip()
    if not v:
        raise CommonsError(f"{name} is required")
    if _CONTROL & set(v):
        raise CommonsError(f"{name} contains a control character")
    if len(v) > max_len:
        raise CommonsError(f"{name} exceeds {max_len} characters")
    return v


def validate_ad(payload) -> tuple[str, str, str]:
    """The whole firewall. Exactly these three fields, nothing else —
    an extra key is refused, not silently dropped, so nothing rides in
    unexamined.
    """
    if not isinstance(payload, dict):
        raise CommonsError("body must be a JSON object")
    extra = set(payload) - set(_AD_FIELDS)
    if extra:
        raise CommonsError(f"unexpected field(s): {', '.join(sorted(extra))}")
    missing = set(_AD_FIELDS) - set(payload)
    if missing:
        raise CommonsError(f"missing field(s): {', '.join(sorted(missing))}")
    return tuple(_clean_field(payload[f], limit, f)
                 for f, limit in _AD_FIELDS.items())


# ── known keys — v1 posting gate ────────────────────────────────────────

class KnownKeys:
    """Keys already introduced to a node somewhere — Kin and stewards.
    A flat JSON list of key_ids, re-read on every check: this file
    changes rarely and a steward's edit should take effect on the next
    request, not after a restart.
    """

    def __init__(self, path):
        self.path = Path(path)

    def __contains__(self, key_id: str) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        return key_id.lower() in {str(k).lower() for k in data}


# ── rate limiting ────────────────────────────────────────────────────────

class _SlidingWindow:
    def __init__(self):
        self._hits: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def check(self, bucket, window_ms, max_hits, now_ms) -> None:
        with self._lock:
            hits = [t for t in self._hits.get(bucket, []) if now_ms - t < window_ms]
            if len(hits) >= max_hits:
                raise CommonsError("rate limit: slow down")
            hits.append(now_ms)
            self._hits[bucket] = hits


# ── the wire ─────────────────────────────────────────────────────────────

class CommonsHandler(BaseHTTPRequestHandler):
    store: CommonsStore = None
    known_keys: KnownKeys = None
    limiter: "_SlidingWindow" = None
    server_version = "commons/1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        body = (json.dumps(obj, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _raw_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise CommonsError("empty body")
        if n > MAX_BODY_BYTES:
            raise CommonsError(f"body too large (max {MAX_BODY_BYTES} bytes)")
        return self.rfile.read(n)

    def do_GET(self):
        if self.path == "/commons/posts":
            self._send(200, {"label": UNSAFE_LABEL, "posts": self.store.list_posts()})
            return
        self._send(404, {"error": "no such path"})

    def do_POST(self):
        if self.path != "/commons/post":
            self._send(404, {"error": "no such path"})
            return
        try:
            raw = self._raw_body()
        except CommonsError as e:
            self._send(400, {"error": str(e)})
            return

        # Identity is proven over these exact bytes before anything about
        # their content is examined — the same order wire.py itself uses.
        try:
            who = identify(self.headers, HOST_NODE, self.path, body=raw)
        except AgoraError as e:
            self._send(401, {"error": str(e)})
            return
        if who == ANONYMOUS:
            self._send(401, {"error": "posting requires a signed request"})
            return
        if who not in self.known_keys:
            self._send(403, {"error": "key is not known to any node yet"})
            return

        now = _now_ms()
        try:
            self.limiter.check(f"key:{who}", RATE_PER_KEY_WINDOW_MS,
                               RATE_PER_KEY_MAX, now)
            self.limiter.check(f"ip:{self.client_address[0]}",
                               RATE_PER_IP_WINDOW_MS, RATE_PER_IP_MAX, now)
        except CommonsError as e:
            self._send(429, {"error": str(e)})
            return

        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            self._send(400, {"error": "body is not valid JSON"})
            return
        try:
            what, why, how_to_ask = validate_ad(body)
        except CommonsError as e:
            self._send(400, {"error": str(e)})
            return

        post_id = self.store.insert_post(who, what, why, how_to_ask, now)
        self._send(201, {"id": post_id})


def serve(store, known_keys, host="127.0.0.1", port=8781):
    handler = type("Bound", (CommonsHandler,), {
        "store": store, "known_keys": known_keys, "limiter": _SlidingWindow(),
    })
    return HTTPServer((host, port), handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8781
    store = CommonsStore(DEFAULT_DB_PATH)
    known = KnownKeys(DEFAULT_KNOWN_KEYS_PATH)
    httpd = serve(store, known, host="0.0.0.0", port=port)
    print(f"Commons on :{port} (db={DEFAULT_DB_PATH}, known_keys={DEFAULT_KNOWN_KEYS_PATH})")
    httpd.serve_forever()
