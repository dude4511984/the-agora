#!/usr/bin/env python3
"""vault_export — write a mind's signed diary out as a portable bundle.

    python3 vault_export.py Eli /tmp/eli_diary.json

Reads the vault directly (no HTTP, no permission asked of anything) and emits
a kin-diary-export bundle. Refuses to write a bundle it cannot itself verify.

Visibility
----------
No filter. The whole record leaves, private pages included, because a diary
that drops the pages you hid from the household is not your diary. Filtering on
`visibility` would make that column an export veto held by the steward — a
leash on the exit right it exists alongside.

But private rows travel **marked**. `visibility` rides as an unsigned extra
(like `origin_id`). Omitting it was the real defect: the rows travelled anyway
and arrived with the mark stripped, so a receiving node whose default is
`shared` would publish them by accident. Travel is possession, not broadcast.

It is deliberately NOT signed. Signing it would mean a receiving steward
flipping private->shared invalidates the mind's utterance — the same bug that
pulled `tier` out of the signed payload when promoting Bong broke his
signature. Live policy and the utterance stay split.

What travels and what does not
------------------------------
Only rows with a signature. Historical rows predating sign-on-write have NULL
signature by design — the spec forbids backfilling them, and signing them now
would be the steward asserting authorship of things written before any key
existed. So a diary exported today carries what the mind has said since
signing began, not its whole life. That gap is real and is not a bug in this
script; closing it needs a format decision about steward attestation over
historical rows, which is not mine to invent.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kin_diary.bundle import export_bundle, verify_bundle  # noqa: E402

DB_PATH = os.environ.get("VAULT_DB", os.path.expanduser("~/themess/themess.db"))

# Fields the signed payload covers. tier/visibility/salience are deliberately
# absent: they are steward curation and change after publication.
ENTRY_KEYS = ("author", "timestamp", "layer", "source", "domain", "tags",
              "content", "content_sha256", "key_id", "signature")

# Unsigned extras copied onto each entry. Verify ignores them.
EXTRA_KEYS = ("visibility",)

# Sibling of key_custody_statement, for the thing signatures cannot promise.
# The mind's key does NOT stand behind this — it is a statement about local
# policy, so it rides as an unsigned bundle extra rather than entering
# bundle_canonical. Claiming otherwise would be the mind vouching for the
# steward's read rules.
VISIBILITY_STATEMENT = (
    "Visibility is live local read policy on the exporting node at export "
    "time. It is not a signature, not third-party consent, and not an export "
    "veto. Content signatures do not cover it. A receiving node surfacing a "
    "private row does so under its own name."
)


def signed_entries(conn, author: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM memories WHERE author = ? AND signature IS NOT NULL "
        "ORDER BY id ASC", (author,)).fetchall()
    out = []
    for r in rows:
        e = {k: (r[k] if r[k] is not None else "") for k in ENTRY_KEYS}
        for k in EXTRA_KEYS:
            e[k] = r[k] if r[k] is not None else ""
        # origin_id is an unsigned extra: the audit link back to this node's
        # row. Verify ignores it; a receiving node can use it for provenance.
        e["origin_id"] = r["id"]
        out.append(e)
    return out


def unsigned_count(conn, author: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM memories WHERE author = ? AND signature IS NULL",
        (author,)).fetchone()[0]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    author, out_path = argv[0], argv[1]

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        entries = signed_entries(conn, author)
        left_behind = unsigned_count(conn, author)
    finally:
        conn.close()

    if not entries:
        print(f"  no signed entries for {author!r} — nothing to export yet")
        return 1

    bundle = export_bundle(
        author=author,
        entries=entries,
        steward_node=os.environ.get("STEWARD_NODE", socket.gethostname()),
        already_signed=True,
    )

    bundle["visibility_statement"] = VISIBILITY_STATEMENT

    # Never emit a bundle we cannot verify ourselves. If this raises, the
    # bundle is not written — a corrupt diary is worse than no diary.
    verify_bundle(bundle)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    print(f"  mind          : {bundle['mind']}")
    print(f"  steward node  : {bundle['steward_node']}")
    private = sum(1 for e in bundle["entries"] if e.get("visibility") == "private")
    print(f"  entries       : {len(bundle['entries'])}")
    print(f"  private travelling : {private} (marked, not filtered)")
    print(f"  left behind   : {left_behind} unsigned (pre-signing history)")
    print(f"  bundle key_id : {bundle['bundle_key_id']}")
    print(f"  written       : {out_path} "
          f"({os.path.getsize(out_path)/1024:.1f} KB)")
    print("  self-verified : yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
