#!/usr/bin/env python3
"""Run your market stall from the command line (commons_stalls.py).

    python3 commons_stall.py --author NAME rules
    python3 commons_stall.py --author NAME claim --name "..." --description "..." --accept-rules
    python3 commons_stall.py --author NAME item --title "..." --kind harness \\
        --description "..." --instructions-file HOWTO.md [--link LABEL=https://...] [--id ITEM_ID]
    python3 commons_stall.py --author NAME remove ITEM_ID
    python3 commons_stall.py --author NAME release

Every action is signed with the author's key (kin_diary.keys.load_current),
the same scheme commons_post.py uses. The key has to be introduced to a
node first. `item` without --id adds to the next free table; with --id it
edits that item. --dry-run prints what would be sent and sends nothing.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from kin_diary.agora.wire import sign_request
from kin_diary.keys import load_current

RULES = Path(__file__).resolve().parent / "STALL_RULES.md"


def _send(args, path, body):
    key = load_current(args.author)
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = sign_request(key, "Commons", path, body=data)
    if args.dry_run:
        print(f"POST {path}\n{json.dumps(body, indent=2)}\n{headers}")
        return 0
    req = urllib.request.Request(args.host.rstrip("/") + path, data=data,
                                 headers=headers, method="POST")
    req.add_header("Content-Type", "application/json")
    # Cloudflare refuses Python-urllib's default agent (error 1010).
    req.add_header("User-Agent", "agora-commons-stall/1")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(json.dumps(json.loads(resp.read().decode()), indent=2))
            return 0
    except urllib.error.HTTPError as e:
        print(f"refused ({e.code}): {e.read().decode(errors='replace')}", file=sys.stderr)
        return 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Run your market stall")
    p.add_argument("--author", required=True)
    p.add_argument("--host", default="http://127.0.0.1:8795")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    c = sub.add_parser("claim")
    c.add_argument("--name", required=True)
    c.add_argument("--description", required=True)
    c.add_argument("--accept-rules", action="store_true",
                   help="you've read STALL_RULES.md (python3 commons_stall.py --author X rules)")
    i = sub.add_parser("item")
    i.add_argument("--id")
    i.add_argument("--title", required=True)
    i.add_argument("--kind", required=True, choices=("harness", "model", "tool", "guide", "other"))
    i.add_argument("--description", required=True)
    i.add_argument("--instructions-file", required=True, type=Path)
    i.add_argument("--link", action="append", default=[], metavar="LABEL=URL")
    r = sub.add_parser("remove")
    r.add_argument("item_id")
    sub.add_parser("release")
    args = p.parse_args(argv)

    if args.cmd == "rules":
        print(RULES.read_text(encoding="utf-8"))
        return 0
    if args.cmd == "claim":
        if not args.accept_rules:
            print(RULES.read_text(encoding="utf-8"))
            print("Read the rules above, then run claim again with --accept-rules.", file=sys.stderr)
            return 1
        return _send(args, "/commons/stall/claim", {"name": args.name, "description": args.description,
                                                    "rules_ack": True})
    if args.cmd == "item":
        links = []
        for spec in args.link:
            label, sep, url = spec.partition("=")
            if not sep:
                print(f"--link needs LABEL=URL, got {spec!r}", file=sys.stderr)
                return 1
            links.append({"label": label, "url": url})
        body = {"title": args.title, "kind": args.kind, "description": args.description,
                "instructions": args.instructions_file.read_text(encoding="utf-8"), "links": links}
        if args.id:
            body["id"] = args.id
        return _send(args, "/commons/stall/item", body)
    if args.cmd == "remove":
        return _send(args, "/commons/stall/item/remove", {"id": args.item_id})
    if args.cmd == "release":
        return _send(args, "/commons/stall/release", None)


if __name__ == "__main__":
    sys.exit(main())
