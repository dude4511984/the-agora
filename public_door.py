#!/usr/bin/env python3
"""The public ring-0 door: a named visitor reading Frosty's teaser.

A stranger on the public internet reaches this through the Cloudflare
tunnel as agora.everysynthetic.org. Anonymous callers to a node get only
the 39-byte banner — that lockdown is by design (2026-09-13), not a bug —
so this door is itself a named visitor: it holds exactly one key,
`door-everysynthetic`, never introduced, capped at ring 0 by the node no
matter what, and signs its reads with it. Every public read is attributed
to that key in Frosty's record. The door holds no other power and no
other key.

Allowlist, not blocklist: GET / and GET /view, nothing else, ever. Every
other path and every other method gets the same 404. Visitor headers are
never forwarded — a stranger cannot present a key through this door;
the only X-Agora-* headers upstream are the ones this door signs itself.

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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.keys import load_current  # noqa: E402
from kin_diary.agora.wire import sign_request  # noqa: E402

ALLOWED_PATHS = ("/", "/view")
NOT_FOUND = {"error": "no such path"}

UPSTREAM_HOST = "192.168.1.119"
UPSTREAM_PORT = 8770
NODE_NAME = "Frosty"
DOOR_KEY_AUTHOR = "door-everysynthetic"

UPSTREAM_TIMEOUT_S = 5
RATE_WINDOW_MS = 60_000
RATE_MAX_READS = 30               # per visitor per minute, sliding window

HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})


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


def render_facts_html(facts: dict, view_path: str) -> bytes:
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
        ("Paused", facts.get("paused")),
        ("Pause reason", facts.get("pause_reason") or ""),
    ]
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
<p><a href="{html.escape(view_path)}">The atlas teaser (JSON)</a> ·
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
    node_name = NODE_NAME
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

    def _fetch_upstream(self, path: str):
        """One signed read. Visitor headers are never forwarded; the only
        identity upstream is this door's own signature."""
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
            # The node's own answer (403/404) is the answer; pass it through.
            return e.code, e.read()

    # ── the door ───────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path not in ALLOWED_PATHS:
            self._send(404, NOT_FOUND)
            return
        if not self.limiter.allows(self._visitor()):
            self._send(429, {"error": "rate limit: slow down"})
            return
        try:
            status, body = self._fetch_upstream(self.path)
        except (urllib.error.URLError, OSError):
            self._send(502, {"error": "the node is not answering"})
            return
        if status == 200 and self.path == "/" and self._wants_html():
            try:
                facts = json.loads(body)
            except ValueError:
                facts = None
            if isinstance(facts, dict):
                page = render_facts_html(facts, "/view")
                self.send_response(200)
                self.send_header("Content-Type",
                                 "text/html; charset=utf-8")
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

    def _refuse(self):
        self._send(404, NOT_FOUND)

    do_POST = _refuse
    do_PUT = _refuse
    do_PATCH = _refuse
    do_DELETE = _refuse
    do_HEAD = _refuse
    do_OPTIONS = _refuse


def make_server(host: str, port: int, door_key, node_name: str = NODE_NAME,
                upstream_host: str = UPSTREAM_HOST,
                upstream_port: int = UPSTREAM_PORT,
                max_reads: int = RATE_MAX_READS) -> ThreadingHTTPServer:
    handler = type("BoundDoor", (DoorHandler,), {
        "door_key": door_key,
        "limiter": _VisitorLimiter(max_reads=max_reads),
        "upstream_host": upstream_host,
        "upstream_port": upstream_port,
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
          f"{UPSTREAM_HOST}:{UPSTREAM_PORT} ({NODE_NAME})",
          flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
