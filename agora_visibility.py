#!/usr/bin/env python3
"""Read-only resident view of an Agora node.

This probe performs only GET requests. It authenticates each resident key,
fetches the same root/view/board surfaces used by the map, and reports the
difference between a full board read and a ring-0 teaser. It never submits an
event, presence, signature-bearing write, or pause/unpause action.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from kin_diary.agora.wire import sign_request
from kin_diary.keys import DEFAULT_KEYS_ROOT, load_current


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: object
    error: str | None = None


def _get(base: str, node: str, path: str, key=None) -> FetchResult:
    headers = {"User-Agent": "agora-visibility/1"}
    if key is not None:
        headers.update(sign_request(key, node, path, int(time.time() * 1000)))
    request = urllib.request.Request(
        base.rstrip("/") + path, headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return FetchResult(response.status, json.loads(response.read()))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except (ValueError, json.JSONDecodeError):
            body = None
        return FetchResult(exc.code, body, str(body))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return FetchResult(0, None, f"{type(exc).__name__}: {exc}")


def inspect_resident(base: str, node: str, author: str, key) -> dict:
    """Fetch every read surface as one resident, without touching the node."""
    root = _get(base, node, "/", key)
    if not isinstance(root.body, dict):
        return {"author": author, "key_id": key.key_id, "root": root}
    boards = root.body.get("boards") or []
    view = _get(base, node, "/view", key)
    notices = _get(base, node, "/notices", key)
    peers = _get(base, node, "/peers", key)
    board_reads = {
        board: _get(base, node, f"/board/{board}", key)
        for board in boards
    }
    return {
        "author": author,
        "key_id": key.key_id,
        "root": root,
        "view": view,
        "notices": notices,
        "peers": peers,
        "boards": board_reads,
    }


def inspect_anonymous(base: str, node: str, boards: list[str]) -> dict:
    """Fetch the ring-0 comparison surface using no resident identity."""
    return {
        "root": _get(base, node, "/"),
        "view": _get(base, node, "/view"),
        "boards": {
            board: _get(base, node, f"/board/{board}")
            for board in boards
        },
    }


def _board_report(result: FetchResult) -> dict:
    if not isinstance(result.body, dict):
        return {"status": result.status, "error": result.error}
    entries = result.body.get("entries") or []
    return {
        "status": result.status,
        "ring": result.body.get("ring"),
        "total": result.body.get("total"),
        "visible_entries": len(entries),
        "teasers": sum(1 for entry in entries if entry.get("teaser")),
        "full_entries": sum(
            1 for entry in entries if not entry.get("teaser")
        ),
    }


def report(snapshot: dict) -> dict:
    root = snapshot.get("root")
    root_body = root.body if isinstance(root, FetchResult) else None
    view = snapshot.get("view")
    view_body = view.body if isinstance(view, FetchResult) else None
    return {
        "author": snapshot["author"],
        "key_id": snapshot["key_id"],
        "facts": root_body,
        "visible": {
            "view": {
                "status": view.status,
                "places": len(view_body.get("places", []))
                if isinstance(view_body, dict) else None,
                "presence": len(view_body.get("presence", []))
                if isinstance(view_body, dict) else None,
                "listings": len(view_body.get("listings", []))
                if isinstance(view_body, dict) else None,
                "peer_doors": len(view_body.get("peer_doors", []))
                if isinstance(view_body, dict) else None,
            },
            "boards": {
                board: _board_report(result)
                for board, result in snapshot.get("boards", {}).items()
            },
            "notices_status": snapshot["notices"].status,
            "peers_status": snapshot["peers"].status,
        },
        "pause_effect": (
            "GET surfaces remain readable; pause governs actions, not visibility"
            if isinstance(root_body, dict) and root_body.get("paused")
            else "node is not paused; read surfaces were fetched"
        ),
    }


def anonymous_report(snapshot: dict) -> dict:
    view = snapshot["view"]
    body = view.body if isinstance(view, FetchResult) else None
    return {
        "view": {
            "status": view.status,
            "places": len(body.get("places", [])) if isinstance(body, dict) else None,
            "presence": len(body.get("presence", [])) if isinstance(body, dict) else None,
            "listings": len(body.get("listings", [])) if isinstance(body, dict) else None,
            "peer_doors": len(body.get("peer_doors", [])) if isinstance(body, dict) else None,
        },
        "boards": {
            board: _board_report(result)
            for board, result in snapshot["boards"].items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("node", nargs="?", default="Frosty")
    parser.add_argument("--base", default="http://192.168.1.119:8770")
    args = parser.parse_args()

    anonymous = _get(args.base, args.node, "/")
    anonymous_view = _get(args.base, args.node, "/view")
    if anonymous.status != 200 or anonymous.body != {
        "service": "EverySynthetic Node"
    } or anonymous_view.status != 200 or anonymous_view.body != {
        "service": "EverySynthetic Node"
    }:
        print(json.dumps({
            "anonymous_root": anonymous.body,
            "anonymous_view": anonymous_view.body,
        }, indent=2))
        return 1
    # Discovery is itself authorized now. Try real local resident keys and
    # use their signed root facts to learn which boards/people are present.
    residents = []
    boards = set()
    for key_dir in sorted(DEFAULT_KEYS_ROOT.iterdir()):
        if not key_dir.is_dir() or key_dir.name.startswith("."):
            continue
        try:
            key = load_current(key_dir.name, DEFAULT_KEYS_ROOT)
        except (FileNotFoundError, ValueError):
            continue
        signed_root = _get(args.base, args.node, "/", key)
        del key
        if signed_root.status == 200 and isinstance(signed_root.body, dict):
            residents.extend(signed_root.body.get("residents") or [])
            boards.update(signed_root.body.get("boards") or [])
    residents = sorted(set(residents))
    anonymous_surface = anonymous_report(
        inspect_anonymous(args.base, args.node, boards)
    )
    reports = []
    for author in residents:
        try:
            key = load_current(author, DEFAULT_KEYS_ROOT)
        except FileNotFoundError:
            reports.append({"author": author, "error": "resident key unavailable"})
            continue
        try:
            reports.append(report(inspect_resident(
                args.base, args.node, author, key
            )))
        finally:
            del key
    print(json.dumps({
        "node": args.node,
        "base": args.base,
        "anonymous_facts": anonymous.body,
        "anonymous_view": anonymous_view.body,
        "anonymous_surface": anonymous_surface,
        "resident_reports": reports,
        "read_only": True,
        "writes_attempted": 0,
        "models_called": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
