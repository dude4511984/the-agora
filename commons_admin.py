#!/usr/bin/env python3
"""Steward tools for The Commons — commons_server.py has no HTTP route for
these on purpose: a stated reason from a human at a keyboard, not a request
a stranger's key could ever sign.

    python3 commons_admin.py hide <post_id> "<reason>"

Talks to the same SQLite file commons_server.py does (CommonsStore is a
connection-per-call, same as the server), so this works whether or not the
server process is currently running.
"""

from __future__ import annotations

import argparse
import sys

from commons_server import DEFAULT_DB_PATH, CommonsStore


def cmd_hide(args) -> int:
    store = CommonsStore(args.db)
    try:
        found = store.hide_post(args.post_id, args.reason)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not found:
        print(f"no such post: {args.post_id}", file=sys.stderr)
        return 1
    print(f"hidden: {args.post_id} ({args.reason})")
    return 0


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="Steward tools for The Commons")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH),
                         help=f"path to commons.db (default: {DEFAULT_DB_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    hide = sub.add_parser("hide", help="hide a post, with a stated reason "
                                        "(a visible tombstone stays)")
    hide.add_argument("post_id")
    hide.add_argument("reason")
    hide.set_defaults(func=cmd_hide)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
