#!/usr/bin/env python3
"""The public ring-0 door: a named visitor reading Frosty's teaser and courtyard.

A stranger on the public internet reaches this through the Cloudflare
tunnel as agora.everysynthetic.org. Anonymous callers to a node get only
the 39-byte banner — that lockdown is by design (2026-09-13), not a bug —
so this door is itself a named visitor: it holds exactly one key,
`door-everysynthetic`, never introduced, capped at ring 0 by the node no
matter what, and signs its reads with it. Every public read is attributed
to that key in Frosty's record. The door holds no other power and no
other key.

Allowlist, not blocklist:
- GET / and GET /3d -> Frosty agora_map:8791/3d?public=1 (read-only 3D courtyard)
- GET /facts -> Frosty node:8770/ (signed ring-0 facts HTML or JSON)
- GET /view -> Frosty node:8770/view (signed ring-0 atlas teaser JSON)
- GET /commons-recent, /kin-intent -> Frosty agora_map:8791 (polled JSON)
- GET /proxy, /avatar, /shape3d -> Frosty agora_map:8791
- GET /models/*, /static/agora/* -> Frosty agora_map:8791 (assets)
- GET /commons/posts -> Commons (co-located on this host), plain proxy
- POST /commons/post, /commons/opt-out, /commons/opt-in, /commons/says ->
  Commons, the one deliberate exception below

Every other path and every other method gets the same 404. Visitor headers are
never forwarded to the node or the map — a stranger cannot present a key
through this door for those; the only X-Agora-* headers upstream there are
the ones this door signs itself. Map proxy calls set X-Agora-Door: 1 to
ensure write routes are refused.

These four /commons/* POSTs are the one deliberate exception to "never
forward visitor headers": the whole point of the Commons is that a Kin or
steward, out on the public internet, proves THEIR OWN identity to
Commons, not the door's. Commons is not a node — it keeps no ring, grants
no access to anything — so there is nothing here for a forwarded key to
reach beyond Commons' own known-keys gate (for /commons/post) or its
own-key-only gate (for opt-out/opt-in/says), all of which Commons checks
itself. The door relays the caller's X-Agora-Key/-Time/-Signature and raw
body unmodified and nothing else; it never inspects or re-signs them,
exactly as it never inspects the body of any other proxied route.
/commons/opt-out and /commons/opt-in carry no body at all — Commons'
own _optional_body() expects exactly that, so the door allows a zero-
length body only for these two paths. /commons/says carries a real body
(the author's own self-declaration) and is relayed like /commons/post.

Also forwarded, on all four POSTs: X-Forwarded-For, carrying this door's
own already-computed visitor address (CF-Connecting-IP through the
tunnel, else the direct peer — the same value _visitor() uses for this
door's own rate limiter). Commons trusts that header only when ITS peer
is loopback, i.e. only when a request truly came through this door, so
sending it unconditionally here is safe.

stdlib only, like the wire it fronts. Runs on Themess; the node is on
Frosty over the LAN.
"""

from __future__ import annotations

import html
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
if str(Path.home() / "kin_diary") not in sys.path:
    sys.path.append(str(Path.home() / "kin_diary"))

from kin_diary.keys import load_current  # noqa: E402
from kin_diary.agora.wire import sign_request  # noqa: E402

ALLOWED_NODE_PATHS = frozenset({"/facts", "/view"})
ALLOWED_MAP_EXACT_PATHS = frozenset({"/", "/3d", "/commons-recent", "/kin-intent"})
ALLOWED_MAP_QUERY_PATHS = frozenset({"/proxy", "/avatar", "/shape3d"})
ALLOWED_MAP_PREFIXES = ("/models/", "/static/agora/")
ALLOWED_COMMONS_GET_PATHS = frozenset({"/commons/posts"})
# /commons/opt-out and /commons/opt-in carry no body (commons_server.py's
# own _optional_body() expects that) but are otherwise relayed exactly
# like /commons/post: the caller's own signed headers, untouched. Without
# these two, a Kin reaching the Commons only through this door could
# never close their own door to it — the consent condition itself
# requires that work from anywhere, not just on the LAN. /commons/says
# (item 9: the author's own self-declaration) DOES carry a real body
# (JSON {"says": ...}), so it's relayed like /commons/post, not like the
# opt routes — see ALLOWED_COMMONS_EMPTY_BODY_PATHS below.
ALLOWED_COMMONS_POST_PATHS = frozenset(
    {"/commons/post", "/commons/opt-out", "/commons/opt-in", "/commons/says"})
ALLOWED_COMMONS_EMPTY_BODY_PATHS = frozenset({"/commons/opt-out", "/commons/opt-in"})

NOT_FOUND = {"error": "no such path"}

UPSTREAM_HOST = "192.168.1.119"
UPSTREAM_PORT = 8770               # Frosty node (signed reads)
UPSTREAM_MAP_PORT = 8791           # Frosty agora map (courtyard + assets)
NODE_NAME = "Frosty"
DOOR_KEY_AUTHOR = "door-everysynthetic"

COMMONS_HOST = "127.0.0.1"         # co-located with this door
COMMONS_PORT = 8781

UPSTREAM_TIMEOUT_S = 5
RATE_WINDOW_MS = 60_000
RATE_MAX_READS = 180               # per visitor per minute, sliding window

# A post is three short fields (SPEC_commons.md). commons_server.py's own
# limit is the real one; this is the door refusing an oversized claim
# before it even reads the body into memory, the same reason wire.py caps
# its own MAX_BODY_BYTES ahead of the node that actually enforces shape.
MAX_COMMONS_POST_BYTES = 8192


class _VisitorLimiter:
    """Per-visitor sliding window over reads. Keyed on CF-Connecting-IP when
    the request came through the tunnel (the door binds loopback, so the
    peer address is always the local cloudflared); else the peer address.
    """

    def __init__(self, max_reads: int = RATE_MAX_READS,
                 window_ms: int = RATE_WINDOW_MS):
        self._max = max_reads
        self._window = window_ms
        self._hits: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def allows(self, visitor: str, now_ms: int | None = None) -> bool:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._lock:
            hits = [t for t in self._hits.get(visitor, [])
                    if now - t < self._window]
            if len(hits) >= self._max:
                self._hits[visitor] = hits
                return False
            hits.append(now)
            self._hits[visitor] = hits
            return True


def render_facts_html(facts: dict, view_path: str = "/view") -> bytes:
    """A plain page for a human in a browser. The JSON is the record; this
    is the same facts, rendered. No tracking, no assets, no script."""
    def esc(v) -> str:
        if isinstance(v, bool):
            v = "yes" if v else "no"
        return html.escape(str(v))

    residents = facts.get("residents") or []
    rows = [
        ("Node", facts.get("node") or facts.get("name") or ""),
        ("Speaker", facts.get("speaker") or "none seated"),
        ("Residents", ", ".join(esc(r) for r in residents)),
    ]
    if facts.get("governance") == "unanimous":
        rows.append(("Governance", "Decides by unanimity"))
    else:
        rows.append(("Paused", facts.get("paused")))
        rows.append(("Pause reason", facts.get("pause_reason") or ""))
    body = "\n".join(
        f"<tr><th>{esc(k)}</th><td>{v if k == 'Residents' else esc(v)}</td></tr>"
        for k, v in rows)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Every Synthetic — ring 0</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
<h1>Every Synthetic — a node's front door</h1>
<p>You are reading the ring-0 teaser of this node, fetched and signed by the
visitor key <code>door-everysynthetic</code>. Ring 0 is what a node publishes
to a stranger: who is here, and enough to make you want to ask in.</p>
<table>
{body}
</table>
<p><a href="/">Enter the 3D courtyard</a> · <a href="{html.escape(view_path)}">The atlas teaser (JSON)</a> ·
send <code>Accept: application/json</code> here for the raw facts.</p>
</body>
</html>
"""
    return page.encode("utf-8")


class DoorHandler(BaseHTTPRequestHandler):
    door_key = None        # KeyRecord, set by make_server()
    limiter: _VisitorLimiter = None
    upstream_host = UPSTREAM_HOST
    upstream_port = UPSTREAM_PORT
    map_host = UPSTREAM_HOST
    map_port = UPSTREAM_MAP_PORT
    node_name = NODE_NAME
    commons_host = COMMONS_HOST
    commons_port = COMMONS_PORT
    server_version = "agora-door/1"

    def log_message(self, fmt, *args):
        pass              # the node's own record attributes every read

    # ── plumbing ───────────────────────────────────────────────────────────

    def _send(self, code: int, obj) -> None:
        body = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _visitor(self) -> str:
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def _wants_html(self) -> bool:
        accept = self.headers.get("Accept") or ""
        if "text/html" not in accept:
            return False
        # An explicit application/json preference beats the browser default.
        return "application/json" not in accept or accept.index(
            "text/html") < accept.index("application/json")

    def _fetch_node(self, path: str):
        """One signed read from the node. Visitor headers are never forwarded;
        the only identity upstream is this door's own signature."""
        auth = sign_request(self.door_key, self.node_name, path)
        req = urllib.request.Request(
            f"http://{self.upstream_host}:{self.upstream_port}{path}",
            headers={
                "Host": f"{self.upstream_host}:{self.upstream_port}",
                "Accept": "application/json",
                "Connection": "close",
                **auth,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _fetch_map(self, map_path_and_query: str):
        """Proxy a read to the Agora 3D map server. Visitor headers are stripped;
        X-Agora-Door: 1 is attached so upstream refuses write routes."""
        req = urllib.request.Request(
            f"http://{self.map_host}:{self.map_port}{map_path_and_query}",
            headers={
                "Host": f"{self.map_host}:{self.map_port}",
                "User-Agent": "agora-door/1",
                "Connection": "close",
                "X-Agora-Door": "1",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _fetch_commons_get(self, path: str):
        """Plain proxy — Commons reading needs no key at all
        (SPEC_commons.md: "open to anyone, bare browser included"), so
        there is nothing to sign or strip here, same shape as _fetch_map."""
        req = urllib.request.Request(
            f"http://{self.commons_host}:{self.commons_port}{path}",
            headers={
                "Host": f"{self.commons_host}:{self.commons_port}",
                "User-Agent": "agora-door/1",
                "Connection": "close",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _relay_commons_post(self, path: str, body: bytes):
        """The one deliberate exception: forward the caller's OWN
        X-Agora-* headers and raw body to Commons, unmodified. Commons
        proves and authorizes the caller itself; the door neither signs
        this as its own key nor inspects what it's carrying, same as it
        never inspects any other proxied body.

        X-Forwarded-For carries this visitor's real address (item 4/7 of
        the hardening pass) — the same value _visitor() already computes
        for this door's own rate limiter (CF-Connecting-IP through the
        tunnel, else the direct peer). Commons trusts it only when ITS
        peer is loopback, i.e. only when the request truly came through
        this door, so this is safe to send unconditionally.
        """
        fwd = {"Host": f"{self.commons_host}:{self.commons_port}",
               "User-Agent": "agora-door/1", "Connection": "close",
               "Content-Type": "application/json",
               "X-Forwarded-For": self._visitor()}
        for h in ("X-Agora-Key", "X-Agora-Time", "X-Agora-Signature"):
            if h in self.headers:
                fwd[h] = self.headers[h]
        req = urllib.request.Request(
            f"http://{self.commons_host}:{self.commons_port}{path}",
            data=body, headers=fwd, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # ── the door ───────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = parsed.query

        # Basic path traversal guard
        if ".." in path or "//" in path:
            self._send(404, NOT_FOUND)
            return

        # 1. Check if path is in allowlist
        target_kind = None  # 'node', 'map', or 'commons'
        target_path = None

        if path in ALLOWED_NODE_PATHS:
            target_kind = "node"
            # /facts reads the node root /; /view reads /view
            target_path = "/" if path == "/facts" else "/view"
        elif path in ("/", "/3d"):
            target_kind = "map"
            target_path = "/3d?public=1"
        elif path in ALLOWED_MAP_EXACT_PATHS:
            target_kind = "map"
            target_path = path
        elif path in ALLOWED_MAP_QUERY_PATHS:
            target_kind = "map"
            target_path = f"{path}?{query}" if query else path
        elif any(path.startswith(prefix) for prefix in ALLOWED_MAP_PREFIXES):
            target_kind = "map"
            target_path = f"{path}?{query}" if query else path
        elif path in ALLOWED_COMMONS_GET_PATHS:
            target_kind = "commons"
            target_path = path
        else:
            self._send(404, NOT_FOUND)
            return

        # 2. Rate limit check
        if not self.limiter.allows(self._visitor()):
            self._send(429, {"error": "rate limit: slow down"})
            return

        # 3. Dispatch to node or map
        if target_kind == "node":
            try:
                status, body = self._fetch_node(target_path)
            except (urllib.error.URLError, OSError):
                self._send(502, {"error": "the node is not answering"})
                return

            if status == 200 and path == "/facts" and self._wants_html():
                try:
                    facts = json.loads(body)
                except ValueError:
                    facts = None
                if isinstance(facts, dict):
                    page = render_facts_html(facts, "/view")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return

            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if target_kind == "map":
            try:
                status, hdrs, body = self._fetch_map(target_path)
            except (urllib.error.URLError, OSError):
                self._send(502, {"error": "the node is not answering"})
                return

            self.send_response(status)
            ctype = hdrs.get("Content-Type", "application/octet-stream")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if target_kind == "commons":
            try:
                status, body = self._fetch_commons_get(target_path)
            except (urllib.error.URLError, OSError):
                self._send(502, {"error": "commons is not answering"})
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

    def do_POST(self):
        # The only writes this door has ever forwarded. Everything else
        # a POST could name is refused with the same 404 as any unlisted
        # GET path — no distinct "method not allowed" that would tell a
        # prober which paths exist at all.
        path = urllib.parse.urlparse(self.path).path
        if path not in ALLOWED_COMMONS_POST_PATHS:
            self._send(404, NOT_FOUND)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_COMMONS_POST_BYTES:
            self._send(400, {"error": "bad or oversized body"})
            return
        # /commons/post always carries a body; opt-out/opt-in never do
        # (commons_server.py's own _optional_body() expects exactly that).
        if n <= 0 and path not in ALLOWED_COMMONS_EMPTY_BODY_PATHS:
            self._send(400, {"error": "bad or oversized body"})
            return
        body = self.rfile.read(n) if n > 0 else b""
        if not self.limiter.allows(self._visitor()):
            self._send(429, {"error": "rate limit: slow down"})
            return
        try:
            status, resp_body = self._relay_commons_post(path, body)
        except (urllib.error.URLError, OSError):
            self._send(502, {"error": "commons is not answering"})
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(resp_body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(resp_body)

    def _refuse(self):
        self._send(404, NOT_FOUND)

    do_PUT = _refuse
    do_PATCH = _refuse
    do_DELETE = _refuse
    do_HEAD = _refuse
    do_OPTIONS = _refuse


def make_server(host: str, port: int, door_key, node_name: str = NODE_NAME,
                upstream_host: str = UPSTREAM_HOST,
                upstream_port: int = UPSTREAM_PORT,
                map_host: str = UPSTREAM_HOST,
                map_port: int = UPSTREAM_MAP_PORT,
                commons_host: str = COMMONS_HOST,
                commons_port: int = COMMONS_PORT,
                max_reads: int = RATE_MAX_READS) -> ThreadingHTTPServer:
    handler = type("BoundDoor", (DoorHandler,), {
        "door_key": door_key,
        "limiter": _VisitorLimiter(max_reads=max_reads),
        "upstream_host": upstream_host,
        "upstream_port": upstream_port,
        "map_host": map_host,
        "map_port": map_port,
        "commons_host": commons_host,
        "commons_port": commons_port,
        "node_name": node_name,
    })
    return ThreadingHTTPServer((host, port), handler)


def main(argv):
    host = "127.0.0.1"
    port = 8790
    args = argv[1:]
    if args and args[0] == "--port" and len(args) > 1:
        port = int(args[1])
    door_key = load_current(DOOR_KEY_AUTHOR)
    httpd = make_server(host, port, door_key)
    print(f"ring-0 door on {host}:{port} as {DOOR_KEY_AUTHOR} "
          f"({door_key.key_id[:16]}…) -> "
          f"node={UPSTREAM_HOST}:{UPSTREAM_PORT} ({NODE_NAME}), "
          f"map={UPSTREAM_HOST}:{UPSTREAM_MAP_PORT}",
          flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
