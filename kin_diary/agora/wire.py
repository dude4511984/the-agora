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
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.exceptions import InvalidSignature

from ..keys import KeyRecord, load_public
from .canonical import COLLAB, body_digest, request_canonical
from .node import AgoraError
from .store import NodeStore

# How stale a signed request may be. Generous enough for a human pasting a
# blob between machines, tight enough that a captured request stops working.
REQUEST_WINDOW_MS = 5 * 60 * 1000

ANONYMOUS = "0" * 64


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
        if n > 8 * 1024 * 1024:
            raise AgoraError("body too large")
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
                self._send(200, node.node_facts())
                return
            if self.path.startswith("/board/"):
                board = self.path[len("/board/"):]
                who = self._who()
                self._send(200, {
                    "board": board,
                    "ring": node.effective_ring(who, board),
                    "entries": node.read(who, board),
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
            "/bundle": "bundle",
            "/election": "election",
        }
        try:
            if self.path in routes:
                # No auth beyond the event's own signature — that is the
                # whole point. An unsigned submission cannot pass, and a
                # signed one needs no further permission to be *offered*;
                # whether it is *accepted* is the node's policy call.
                self.store.record(routes[self.path], json.loads(self._raw_body()))
                self._send(200, {"accepted": routes[self.path]})
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
                self.store.post(who, payload.get("board") or COLLAB, entry)
                self._send(200, {"posted": True})
                return
            self._send(404, {"error": "no such path"})
        except AgoraError as e:
            self._send(403, {"error": str(e)})
        except InvalidSignature:
            self._send(403, {"error": "invalid signature"})
        except Exception as e:
            self._send(400, {"error": f"{type(e).__name__}: {e}"})


def serve(store: NodeStore, host: str = "0.0.0.0", port: int = 8770):
    handler = type("Bound", (AgoraHandler,), {"store": store})
    httpd = HTTPServer((host, port), handler)
    return httpd
