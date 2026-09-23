"""The Commons, v1 — SPEC_commons.md. Open reading, known-key-only
posting, ads only. Binds to the Kin's 2026-09-16 consent
(`agora_commons_public_decision_2026-09-16.md`): a resident can close
their own door to the Commons at any time, signed by their own key,
needing no one else's permission — POST /commons/opt-out and
/commons/opt-in.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kin_diary.agora.node import AgoraError
from kin_diary.agora.wire import ANONYMOUS, identify

HOST_NODE = "Commons"

# "Open to anyone" was true for reading but not for posting — v1 posting
# is known-keys-only (Marvin's ruling, Q1: no scarce resource yet stops
# a stranger's free key, so open posting waits on Don's design call for
# one). The label has to say the narrower true thing, not the one that
# sounds more welcoming.
UNSAFE_LABEL = (
    "UNSAFE. Anyone can read. Nothing here is verified. Posting is by "
    "introduction, for now. Ads only: what you're working on, why, how "
    "to ask in. Never the artifact."
)

MAX_WHAT = 120
MAX_WHY = 280
MAX_HOW_TO_ASK = 200
POST_TTL_MS = 14 * 24 * 60 * 60 * 1000

RATE_PER_KEY_WINDOW_MS = 10 * 60 * 1000
RATE_PER_KEY_MAX = 1
RATE_PER_IP_WINDOW_MS = 24 * 60 * 60 * 1000
RATE_PER_IP_MAX = 20

# Hardening item 3: opt-out/opt-in are deliberately gated on nothing but
# proving key ownership (no known-keys check — see _handle_opt_toggle),
# which is exactly right for a resident closing their own door but also
# means a stranger can mint a free key and write a row per call, forever
# (Wall 12: keys cost nothing). Per-key stays generous — a resident
# toggling back and forth a few times in ten minutes is normal use, not
# abuse — the global cap is the real backstop against a flood of distinct
# minted keys. Either limit is high enough that a real resident's own
# first opt-out, ever, always succeeds instantly: a fresh bucket is never
# already at its ceiling.
RATE_OPT_PER_KEY_WINDOW_MS = 10 * 60 * 1000
RATE_OPT_PER_KEY_MAX = 5
RATE_OPT_GLOBAL_WINDOW_MS = 60 * 60 * 1000
RATE_OPT_GLOBAL_MAX = 100

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
CREATE TABLE IF NOT EXISTS opt_outs (
    key_id TEXT PRIMARY KEY,
    opted_out_at_unix_ms INTEGER NOT NULL
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
            c.executescript(_SCHEMA)

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
        """A resident's own opt-out removes their posts from this list
        entirely — no tombstone, nothing marking that they were ever
        here. This is their own choice to withdraw, not a moderation
        record, so it leaves none.
        """
        self.sweep_expired(now_ms)
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, what, why, how_to_ask, key_id, posted_at_unix_ms, "
                "expires_at_unix_ms, hidden_reason, hidden_at_unix_ms "
                "FROM posts WHERE key_id NOT IN (SELECT key_id FROM opt_outs) "
                "ORDER BY posted_at_unix_ms DESC"
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

    def set_opt_out(self, key_id, now_ms=None) -> None:
        """A resident closes their own door. Signed by their own key,
        needs no one else's permission — enforced by the handler, not
        here; this call itself trusts its caller completely.
        """
        now = _now_ms() if now_ms is None else now_ms
        with self._conn() as c:
            c.execute(
                "INSERT INTO opt_outs (key_id, opted_out_at_unix_ms) VALUES (?,?) "
                "ON CONFLICT(key_id) DO UPDATE SET opted_out_at_unix_ms=excluded.opted_out_at_unix_ms",
                (key_id, now))

    def clear_opt_out(self, key_id) -> None:
        """Opens the door back up. The Kin's decision says "close their
        own door again... at any time" — reversible, on the same terms.
        """
        with self._conn() as c:
            c.execute("DELETE FROM opt_outs WHERE key_id=?", (key_id,))

    def is_opted_out(self, key_id) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM opt_outs WHERE key_id=?", (key_id,)).fetchone()
        return row is not None


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
    # A slow/stalled client (Content-Length: 8000, a trickle of bytes)
    # blocks the thread reading it, not the server — serve() runs
    # ThreadingHTTPServer, one thread per connection. This timeout
    # (StreamRequestHandler.setup() applies it via socket.settimeout())
    # bounds how long that one thread waits before the read gives up,
    # so a slowloris client costs one thread for 10s, never the board.
    timeout = 10

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
        try:
            return self.rfile.read(n)
        except TimeoutError:
            # A trickling client (slowloris): the socket timeout fired
            # mid-read. Turn it into an ordinary 400 rather than an
            # unhandled exception — the thread that was waiting on this
            # one connection is the only cost, never the board.
            raise CommonsError("client stopped sending data")

    def _visitor_ip(self) -> str:
        """Hardening item 4/7: Commons sits behind public_door.py, which
        relays /commons/post from its own loopback process — every real
        visitor's request arrives with client_address[0] equal to the
        DOOR's address, not theirs, which turns "20 per IP per day" into
        20 posts per day total. The door already computes the real
        visitor address correctly (CF-Connecting-IP through the
        Cloudflare tunnel, its own peer otherwise) and forwards it as
        X-Forwarded-For specifically for /commons/post — the header is
        trusted ONLY when the direct TCP peer is loopback, so a caller
        who reaches Commons some other way (skipping the door, or if
        Commons is ever bound wider than 127.0.0.1) can't spoof a header
        to dodge its own rate limit.
        """
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):
            forwarded = (self.headers.get("X-Forwarded-For") or "").strip()
            if forwarded:
                return forwarded
        return peer

    def _optional_body(self) -> bytes:
        """For the opt-out/opt-in actions, which carry no payload — the
        signature covers the empty body the same way sign_request's own
        default (body=None) does."""
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY_BYTES:
            raise CommonsError(f"body too large (max {MAX_BODY_BYTES} bytes)")
        if n <= 0:
            return b""
        try:
            return self.rfile.read(n)
        except TimeoutError:
            raise CommonsError("client stopped sending data")

    def do_GET(self):
        if self.path == "/commons/posts":
            self._send(200, {"label": UNSAFE_LABEL, "posts": self.store.list_posts()})
            return
        self._send(404, {"error": "no such path"})

    def do_POST(self):
        if self.path == "/commons/post":
            self._handle_post()
            return
        if self.path == "/commons/opt-out":
            self._handle_opt_toggle(opted_out=True)
            return
        if self.path == "/commons/opt-in":
            self._handle_opt_toggle(opted_out=False)
            return
        self._send(404, {"error": "no such path"})

    def _handle_opt_toggle(self, opted_out: bool):
        """A resident closing (or reopening) their own door. Signed by
        their own key only — no known-keys gate, no steward involved,
        needs no one else's permission, per the Kin's 2026-09-16 consent.
        """
        try:
            raw = self._optional_body()
        except CommonsError as e:
            self._send(400, {"error": str(e)})
            return
        try:
            who = identify(self.headers, HOST_NODE, self.path, body=raw)
        except AgoraError as e:
            self._send(401, {"error": str(e)})
            return
        if who == ANONYMOUS:
            self._send(401, {"error": "this action requires a signed request"})
            return
        try:
            now = _now_ms()
            self.limiter.check(f"opt-key:{who}", RATE_OPT_PER_KEY_WINDOW_MS,
                                RATE_OPT_PER_KEY_MAX, now)
            self.limiter.check("opt:global", RATE_OPT_GLOBAL_WINDOW_MS,
                                RATE_OPT_GLOBAL_MAX, now)
        except CommonsError as e:
            self._send(429, {"error": str(e)})
            return
        if opted_out:
            self.store.set_opt_out(who)
        else:
            self.store.clear_opt_out(who)
        self._send(200, {"key_id": who, "opted_out": opted_out})

    def _handle_post(self):
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
        if self.store.is_opted_out(who):
            self._send(403, {"error": "this key has closed its door to the Commons"})
            return

        now = _now_ms()
        try:
            self.limiter.check(f"key:{who}", RATE_PER_KEY_WINDOW_MS,
                               RATE_PER_KEY_MAX, now)
            self.limiter.check(f"ip:{self._visitor_ip()}",
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


class _Server(ThreadingHTTPServer):
    # One connection per thread, daemonized so a thread stuck in a slow
    # read (up to CommonsHandler.timeout) never keeps the process alive
    # on its own and never blocks any other connection's thread.
    daemon_threads = True


def serve(store, known_keys, host="127.0.0.1", port=8781):
    handler = type("Bound", (CommonsHandler,), {
        "store": store, "known_keys": known_keys, "limiter": _SlidingWindow(),
    })
    return _Server((host, port), handler)


def _parse_cli_args(argv):
    """Hardening item 2: loopback by default. Behind public_door.py, the
    Commons should only ever be reached through the door — binding
    0.0.0.0 put it directly on the public interface too, a second,
    unintended way in with none of the door's own protections. Widening
    the bind is now something you have to ask for by name, not the
    default shape.
    """
    import argparse
    parser = argparse.ArgumentParser(description="The Commons — public ad board")
    parser.add_argument("port", nargs="?", type=int, default=8781)
    parser.add_argument("--bind-all", action="store_true",
                         help="bind 0.0.0.0 instead of 127.0.0.1 (not the "
                              "deployed shape — Commons is meant to sit "
                              "behind public_door.py)")
    args = parser.parse_args(argv)
    return ("0.0.0.0" if args.bind_all else "127.0.0.1"), args.port


if __name__ == "__main__":
    cli_host, cli_port = _parse_cli_args(sys.argv[1:])
    store = CommonsStore(DEFAULT_DB_PATH)
    known = KnownKeys(DEFAULT_KNOWN_KEYS_PATH)
    httpd = serve(store, known, host=cli_host, port=cli_port)
    print(f"Commons on {cli_host}:{cli_port} "
          f"(db={DEFAULT_DB_PATH}, known_keys={DEFAULT_KNOWN_KEYS_PATH})")
    httpd.serve_forever()
