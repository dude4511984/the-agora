#!/usr/bin/env python3
"""Keep this node's resident Kin visible in its concourse.

    python3 presence_heartbeat.py <NodeName> <port>

The heartbeat is deliberately local-only. Serving the Agora and signing a
presence are separate jobs; this process only signs for resident keys already
held by the steward.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from kin_diary.agora.places import sign_presence
from kin_diary.agora.store import NodeStore
from kin_diary.agora.wire import sign_request
from kin_diary.keys import DEFAULT_KEYS_ROOT, load_current


TTL_MS = 120_000
PERIOD_MS = 30_000
HOST = "127.0.0.1"
PLACE_ID = "concourse"

LOG = logging.getLogger("presence_heartbeat")


def _require_loopback(host: str) -> None:
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("presence heartbeat requires a loopback host")
    except ValueError as exc:
        raise ValueError("presence heartbeat requires a loopback host") from exc


def _post_presence(node: str, port: int, key) -> None:
    _require_loopback(HOST)
    path = "/presence"
    body = json.dumps(
        sign_presence(
            key, node, PLACE_ID, label=key.author, ttl_ms=TTL_MS
        ),
        sort_keys=True,
    ).encode("utf-8")
    headers = sign_request(key, node, path, body=body)
    headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://{HOST}:{port}{path}",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def _heartbeat(store: NodeStore, node: str, port: int) -> None:
    current = store.load()
    steward_key_id = (store.steward_key_id or "").lower()
    for author, key_id in sorted(current.residents.items()):
        if key_id.lower() == steward_key_id:
            continue
        try:
            key = load_current(author, DEFAULT_KEYS_ROOT)
            try:
                _post_presence(node, port, key)
            finally:
                del key
        except FileNotFoundError:
            LOG.warning("presence key missing for resident %s; skipping", author)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                LOG.warning("presence refused for resident %s; skipping", author)
                continue
            LOG.warning("presence HTTP failure for resident %s: %s", author, exc)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            LOG.warning("presence transport failure for resident %s: %s",
                        author, exc)


def run(node: str, port: int) -> None:
    _require_loopback(HOST)
    store = NodeStore(
        Path.home() / ".config" / "kin_diary" / f"{node.lower()}_node.db",
        node,
    )
    try:
        while True:
            _heartbeat(store, node, port)
            time.sleep(PERIOD_MS / 1000)
    finally:
        store.close()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <NodeName> <port>", file=sys.stderr)
        return 2
    node = argv[1]
    try:
        port = int(argv[2])
        if not 1 <= port <= 65535:
            raise ValueError
        _require_loopback(HOST)
        run(node, port)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
