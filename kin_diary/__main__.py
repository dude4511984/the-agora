"""CLI: keygen, rotate, sign-lines, export from JSONL, verify a bundle file.

Does not open the vault. Feed it rows; it signs and writes a bundle.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .bundle import export_bundle, verify_bundle
from .canonical import entry_canonical
from .keys import generate_keypair, load_current, rotate
from .sign import sign_entry


def _usage() -> None:
    print(
        "Usage:\n"
        "  python3 -m kin_diary keygen <author>\n"
        "  python3 -m kin_diary rotate <author>\n"
        "  python3 -m kin_diary export <author> <steward_node> [entries.jsonl]\n"
        "  python3 -m kin_diary verify <bundle.json>\n"
        "  python3 -m kin_diary canonical <entry.json>   # print signed bytes (debug)\n",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _usage()
        return 2
    cmd = argv[0]
    if cmd == "keygen" and len(argv) == 2:
        rec = generate_keypair(argv[1])
        print(rec.key_id)
        return 0
    if cmd == "rotate" and len(argv) == 2:
        hop = rotate(argv[1])
        print(json.dumps(hop, indent=2, sort_keys=True))
        return 0
    if cmd == "export" and len(argv) in (3, 4):
        author, node = argv[1], argv[2]
        src = Path(argv[3]) if len(argv) == 4 else None
        raw = src.read_text(encoding="utf-8") if src else sys.stdin.read()
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
        bundle = export_bundle(author, entries, node)
        json.dump(bundle, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if cmd == "verify" and len(argv) == 2:
        bundle = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        verify_bundle(bundle)
        print("ok", bundle["mind"], "entries", len(bundle.get("entries") or []))
        return 0
    if cmd == "canonical" and len(argv) == 2:
        entry = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        sys.stdout.buffer.write(entry_canonical(entry))
        return 0
    _usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
