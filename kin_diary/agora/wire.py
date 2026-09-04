"""The advertise-only wire: one node's public surface, over HTTP.

agora.md step two — "Not the work: a signed notice." A node publishes who
is here and enough of what they're saying to make you want to ask in. Every
event submitted is already self-authenticating, so the wire adds no new
trust: the signature IS the authorization, and this server is only a
transport that refuses to skip the checks.

stdlib only, deliberately. A node should not need a dependency tree to
answer the door.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.exceptions import InvalidSignature

from ..keys import KeyRecord, load_public
from .artifacts import ArtifactHashMismatch
from .canonical import COLLAB, body_digest, request_canonical
from .node import AgoraError
from .store import NodeStore

# How stale a signed request may be. Generous enough for a human pasting a
# blob between machines, tight enough that a captured request stops working.
REQUEST_WINDOW_MS = 5 * 60 * 1000

ANONYMOUS = "0" * 64

# Mitigations, not walls. A LAN node with no limits is a free DoS for
# anyone already introduced: writes are cheap to issue and permanent to
# store, and an unbounded board serialises into one ever-growing response.
MAX_BODY_BYTES = 256 * 1024        # a board entry is text, not a payload
MAX_ENTRIES_PER_READ = 200         # bound the response, not the board
RATE_WINDOW_MS = 60_000
RATE_MAX_WRITES = 20               # per key per minute


class _RateLimiter:
    """Per-key sliding window. Keyed on the PROVEN key, never on an address:
    the whole point of signed requests is that identity is not the network's
    to assert, and a shared LAN address would punish the wrong caller.
    """

    def __init__(self):
        self._hits: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def check(self, key_id: str, now_ms: int | None = None) -> None:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._lock:
            hits = [t for t in self._hits.get(key_id, []) if now - t < RATE_WINDOW_MS]
            if len(hits) >= RATE_MAX_WRITES:
                raise AgoraError("rate limit: too many writes, slow down")
            hits.append(now)
            self._hits[key_id] = hits


def sign_request(
    key: KeyRecord,
    host_node: str,
    path: str,
    now_ms: int | None = None,
    body: bytes | None = None,
) -> dict:
    """Client side. Produces the headers proving who is asking.

    Pass `body` for writes so the signature covers the payload, not just
    the route.
    """
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    digest = body_digest(body) if body else ""
    sig = key.sign(request_canonical(key.key_id, host_node, path, ts, digest))
    return {
        "X-Agora-Key": key.key_id,
        "X-Agora-Time": str(ts),
        "X-Agora-Signature": sig,
    }


def identify(
    headers,
    host_node: str,
    path: str,
    now_ms: int | None = None,
    body: bytes | None = None,
) -> str:
    """Who is asking, proven. Falls back to anonymous (ring 0) rather than
    rejecting — an unidentified caller is a legitimate visitor who simply
    hasn't been introduced, and the teaser exists precisely for them.

    A *failed* proof is different from no proof at all: claiming a key you
    cannot sign for is refused outright, not quietly downgraded, because
    silently serving a teaser to an impostor hides the attempt.
    """
    key_id = headers.get("X-Agora-Key")
    if not key_id:
        return ANONYMOUS
    sig = headers.get("X-Agora-Signature") or ""
    raw_ts = headers.get("X-Agora-Time") or "0"
    try:
        ts = int(raw_ts)
    except ValueError:
        raise AgoraError("bad X-Agora-Time")
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if abs(now - ts) > REQUEST_WINDOW_MS:
        raise AgoraError("request is outside the freshness window")
    digest = body_digest(body) if body else ""
    try:
        load_public(key_id).verify(
            bytes.fromhex(sig),
            request_canonical(key_id, host_node, path, ts, digest),
        )
    except (InvalidSignature, ValueError):
        raise AgoraError("request signature does not prove this key")
    return key_id.lower()


class AgoraHandler(BaseHTTPRequestHandler):
    store: NodeStore = None          # set by serve()
    limiter: "_RateLimiter" = None   # set by serve()
    node_key = None                  # set by serve(); may be None
    atlas = None                     # set by serve(); may be None
    artifacts = None                 # set by serve(); may be None
    presence = None                  # set by serve(); may be None
    server_version = "agora/1"

    def log_message(self, fmt, *args):
        pass                          # quiet; the event log is the record

    # ── plumbing ───────────────────────────────────────────────────────────

    def _send(self, code: int, obj) -> None:
        body = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _raw_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise AgoraError("empty body")
        if n > MAX_BODY_BYTES:
            raise AgoraError(f"body too large (max {MAX_BODY_BYTES} bytes)")
        return self.rfile.read(n)

    def _who(self, body: bytes | None = None) -> str:
        return identify(self.headers, self.store.node_name, self.path, body=body)

    # ── routes ─────────────────────────────────────────────────────────────

    def do_GET(self):
        try:
            node = self.store.load()
            if self.path == "/":
                # Published at ring 0 on purpose. A visitor who needs ring 3
                # has to know who to ask; hiding the Speaker is pointless
                # secrecy (agora.md, "Visibility").
                #
                # Signed when the node has a key, because these facts are
                # how a visitor learns WHO TO ASK — an unsigned answer lets
                # anyone on the wire advertise a Speaker key of their own.
                facts = node.node_facts()
                if self.node_key is not None:
                    from .events import sign_node_fact
                    facts["signed"] = sign_node_fact(
                        self.node_key, node.name, node.speaker,
                        node.speaker_key_id, node.residents)
                self._send(200, facts)
                return
            if self.path == "/view":
                # P0: the snapshot on the wire. Filtering happens BEFORE
                # assembly, so an object this key may not see is absent from
                # the inventory it is handed rather than present-and-hidden.
                if self.atlas is None or self.node_key is None:
                    self._send(404, {"error": "this node publishes no atlas"})
                    return
                self._send(200, self.atlas.signed_view(
                    self.node_key, self._who(), int(time.time() * 1000)))
                return
            if self.path.startswith("/artifact/"):
                # Content-addressed retrieval. Never execution: bytes go
                # out as octet-stream with no filename and no sniffable
                # type, because a name or a mimetype is where "the kiosk
                # ran it" gets in.
                if self.artifacts is None or self.atlas is None:
                    self._send(404, {"error": "artifact unavailable"})
                    return
                digest = self.path[len("/artifact/"):]
                who = self._who()
                self.limiter.check(who)
                listing = next(
                    (li for li in self.atlas.listings.values()
                     if li.get("artifact_sha256") == digest.lower()), None)
                if listing is None:
                    # Unlistable and unknown must be indistinguishable, so
                    # this is the same 404 and the same words as every
                    # other denial. Telling them apart tells a caller which
                    # digests exist.
                    self._send(404, {"error": "artifact unavailable"})
                    return
                try:
                    blob = self.artifacts.fetch(
                        self.store, who, listing, self.atlas,
                        access_board=listing.get("access_board") or COLLAB,
                        now_ms=int(time.time() * 1000),
                        required_ring=int(listing.get("required_ring") or 1))
                except ArtifactHashMismatch:
                    # The one case that must NOT look like the others: we
                    # authorized this and our own copy is corrupt. Saying
                    # "unavailable" would hide a broken store behind a
                    # permission answer.
                    self._send(500, {"error": "artifact integrity failure"})
                    return
                except Exception:
                    self._send(404, {"error": "artifact unavailable"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(blob)
                return
            if self.path == "/notices":
                # The advertise-only wire. Notices, never the work itself.
                self._send(200, {"node": node.name,
                                 "notices": self.store.notices()})
                return
            if self.path == "/peers":
                self._send(200, {"node": node.name,
                                 "peers": self.store.known_peers()})
                return
            if self.path.startswith("/board/"):
                board = self.path[len("/board/"):]
                who = self._who()
                now_ms = int(time.time() * 1000)
                rows = node.read(who, board, now_ms)
                self._send(200, {
                    "board": board,
                    "ring": node.live_ring(who, board, now_ms),
                    "total": len(rows),
                    "truncated": len(rows) > MAX_ENTRIES_PER_READ,
                    "entries": rows[-MAX_ENTRIES_PER_READ:],
                })
                return
            self._send(404, {"error": "no such path"})
        except AgoraError as e:
            self._send(403, {"error": str(e)})
        except Exception as e:
            self._send(400, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        routes = {
            "/intro": "intro",
            "/grant": "grant",
            "/evict": "evict",
            "/revoke": "revoke",
            "/bundle": "bundle",
            "/election": "election",
            "/rotation": "rotation",
            "/house-decision": "house-decision",
        }
        try:
            if self.path == "/appeal":
                try:
                    raw = self._raw_body()
                    event = json.loads(raw)
                    from .events import verify_appeal
                    verify_appeal(event)
                except (InvalidSignature, ValueError, TypeError, KeyError,
                        IndexError, AttributeError, OverflowError):
                    self._send(403, {"error": "appeal refused"})
                    return
                try:
                    self.store.record("appeal", event)
                except AgoraError as e:
                    self._send(403, {"error": str(e)})
                    return
                except sqlite3.Error:
                    self._send(500, {"error": "appeal storage failure"})
                    return
                self._send(200, {"accepted": "appeal"})
                return
            if self.path == "/ephemeral":
                try:
                    raw = self._raw_body()
                    who = self._who(raw)
                    if who == ANONYMOUS:
                        raise AgoraError("anonymous cannot submit ephemeral admission")
                    event = json.loads(raw)
                    self.store.record("ephemeral", event)
                except (AgoraError, InvalidSignature, ValueError, TypeError,
                        KeyError, IndexError, AttributeError, OverflowError):
                    self._send(403, {"error": "ephemeral refused"})
                    return
                except sqlite3.Error:
                    self._send(500, {"error": "ephemeral storage failure"})
                    return
                self._send(200, {"accepted": "ephemeral"})
                return
            if self.path == "/finding":
                raw = self._raw_body()
                event = json.loads(raw)
                from .events import verify_finding
                verify_finding(event)
                self.limiter.check(event["council_key_id"])
                self.store.record("finding", event)
                self._send(200, {"accepted": "finding"})
                return
            if self.path in routes:
                # No auth beyond the event's own signature — that is the
                # whole point. An unsigned submission cannot pass, and a
                # signed one needs no further permission to be *offered*;
                # whether it is *accepted* is the node's policy call.
                raw = self._raw_body()
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError("event body must be a JSON object")
                except (UnicodeDecodeError, ValueError, TypeError):
                    self._send(403, {"error": "event refused"})
                    return
                # Event submissions carry no request signature (the event's
                # own signature is the authority), so limit them per issuing
                # key where one is PROVABLE, else per route.
                #
                # 2026-09-01: this read the X-Agora-Key header directly, which
                # is a string the caller types. A fresh value each request is a
                # fresh bucket, so the limit was not a limit: demonstrated at
                # 200/200 writes accepted while an honest caller sending one
                # real key got 20/200. It punished the only party it could see.
                # Same shape as `unknown` outranking `wandered` on 08-28 —
                # unlabelled traffic escaping a ceiling that honest traffic
                # obeys. _who proves the key or refuses the claim outright;
                # a caller with nothing to prove shares the route's bucket.
                try:
                    who = self._who(raw)
                    self.limiter.check(self.path if who == ANONYMOUS else who)
                except (InvalidSignature, ValueError, TypeError, KeyError,
                        IndexError, AttributeError, OverflowError):
                    self._send(403, {"error": "event refused"})
                    return
                try:
                    self.store.record(routes[self.path], event)
                except ValueError:
                    self._send(403, {"error": "event refused"})
                    return
                self._send(200, {"accepted": routes[self.path]})
                return
            if self.path == "/presence":
                # Presence is LOCAL and is never relayed. The key signs its
                # own arrival; the host may not author one for anybody.
                raw = self._raw_body()
                who = self._who(raw)
                if who == ANONYMOUS:
                    raise AgoraError("presence requires an identified key")
                self.limiter.check(who)
                if self.presence is None or self.atlas is None:
                    self._send(404, {"error": "this node records no presence"})
                    return
                from .presence_wire import accept_presence
                accept_presence(self.store, self.atlas, self.presence,
                                who, json.loads(raw))
                self._send(200, {"standing": True})
                return
            if self.path == "/post":
                raw = self._raw_body()
                who = self._who(raw)     # signature covers this exact body
                if who == ANONYMOUS:
                    raise AgoraError("posting requires an identified key")
                payload = json.loads(raw)
                entry = payload["entry"]
                if (entry.get("key_id") or "").lower() != who:
                    raise AgoraError("post must be signed by the identified key")
                self.limiter.check(who)
                self.store.post(
                    who, payload.get("board") or COLLAB, entry,
                    int(time.time() * 1000),
                )
                self._send(200, {"posted": True})
                return
            self._send(404, {"error": "no such path"})
        except AgoraError as e:
            self._send(403, {"error": str(e)})
        except InvalidSignature:
            self._send(403, {"error": "invalid signature"})
        except Exception as e:
            self._send(500, {"error": "internal server error"})


def serve(store: NodeStore, host: str = "0.0.0.0", port: int = 8770,
          node_key=None, atlas=None, artifacts=None, presence=None):
    handler = type("Bound", (AgoraHandler,),
                   {"store": store, "limiter": _RateLimiter(),
                    "node_key": node_key, "atlas": atlas,
                    "artifacts": artifacts, "presence": presence})
    httpd = HTTPServer((host, port), handler)
    return httpd
