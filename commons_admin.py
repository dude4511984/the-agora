#!/usr/bin/env python3
"""Steward tools for The Commons — commons_server.py has no HTTP route for
these on purpose: a stated reason from a human at a keyboard, not a request
a stranger's key could ever sign.

    python3 commons_admin.py hide <post_id> "<reason>"
    python3 commons_admin.py stall-reports
    python3 commons_admin.py stall-revoke <stall_id> "<reason>" [--no-bar]
    python3 commons_admin.py stall-resolve <report_id> "<what you did>"
    python3 commons_admin.py stall-unbar <key_id>

Stalls follow the same rule as hide: closing someone's stall is a stated
reason from a human at a keyboard, never an HTTP route a key could sign.

Talks to the same SQLite file commons_server.py does (CommonsStore is a
connection-per-call, same as the server), so this works whether or not the
server process is currently running.
"""

from __future__ import annotations

import argparse
import sys

import time

from commons_server import DEFAULT_DB_PATH, CommonsStore
from commons_stalls import StallStore


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


def cmd_stall_reports(args) -> int:
    reports = StallStore(args.db).open_reports()
    if not reports:
        print("no open reports")
    for r in reports:
        print(f"{r['id']}  stall {r['stall_id']} ({r['stall_name']}, key {r['key_id']})\n"
              f"    {r['reason']}")
    return 0


def cmd_stall_revoke(args) -> int:
    try:
        found = StallStore(args.db).revoke(args.stall_id, args.reason,
                                           int(time.time() * 1000), bar=not args.no_bar)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not found:
        print(f"no open stall: {args.stall_id}", file=sys.stderr)
        return 1
    print(f"revoked: {args.stall_id} ({args.reason})"
          + ("" if args.no_bar else "; its key can't claim again until unbarred"))
    return 0


def cmd_stall_resolve(args) -> int:
    try:
        found = StallStore(args.db).resolve_report(args.report_id, args.resolution,
                                                   int(time.time() * 1000))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not found:
        print(f"no open report: {args.report_id}", file=sys.stderr)
        return 1
    print(f"resolved: {args.report_id} ({args.resolution})")
    return 0


def cmd_stall_unbar(args) -> int:
    if not StallStore(args.db).unbar(args.key_id):
        print(f"not barred: {args.key_id}", file=sys.stderr)
        return 1
    print(f"unbarred: {args.key_id}")
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

    rep = sub.add_parser("stall-reports", help="list stall reports waiting for a human")
    rep.set_defaults(func=cmd_stall_reports)
    rev = sub.add_parser("stall-revoke", help="close a stall, with a stated reason "
                                              "(a visible tombstone stays)")
    rev.add_argument("stall_id")
    rev.add_argument("reason")
    rev.add_argument("--no-bar", action="store_true",
                     help="let the key claim a stall again")
    rev.set_defaults(func=cmd_stall_revoke)
    res = sub.add_parser("stall-resolve", help="close a report, saying what was done")
    res.add_argument("report_id")
    res.add_argument("resolution")
    res.set_defaults(func=cmd_stall_resolve)
    unb = sub.add_parser("stall-unbar", help="let a revoked key claim a stall again")
    unb.add_argument("key_id")
    unb.set_defaults(func=cmd_stall_unbar)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
