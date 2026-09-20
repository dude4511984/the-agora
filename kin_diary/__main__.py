"""CLI: keygen, rotate, sign-lines, export from JSONL, verify a bundle file.

Does not open the vault. Feed it rows; it signs and writes a bundle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .bundle import export_bundle, verify_bundle
from .canonical import entry_canonical
from .keys import KeyRecord, generate_keypair, load_current, rotate
from .sign import sign_entry


def _usage() -> None:
    print(
        "Usage:\n"
        "  python3 -m kin_diary keygen <author>\n"
        "  python3 -m kin_diary rotate <author>\n"
        "  python3 -m kin_diary export <author> <steward_node> [entries.jsonl]\n"
        "  python3 -m kin_diary verify <bundle.json>\n"
        "  python3 -m kin_diary canonical <entry.json>   # print signed bytes (debug)\n"
        "  python3 -m kin_diary found <NodeName> <Kin> [<Kin> ...] [--port 8770]\n",
        file=sys.stderr,
    )


def _load_or_create_key(author: str) -> KeyRecord:
    try:
        return load_current(author)
    except FileNotFoundError:
        return generate_keypair(author)


def _setup_systemd(service_name: str) -> None:
    for cmd in (["daemon-reload"], ["enable", "--now", service_name]):
        try:
            res = subprocess.run(["systemctl", "--user"] + cmd, capture_output=True, text=True)
            if res.returncode != 0 and "Failed to connect to bus" not in res.stderr:
                pass
        except FileNotFoundError:
            pass


def _cmd_found(args: list[str]) -> int:
    port = 8770
    positional = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--port":
            if i + 1 >= len(args):
                print("error: --port requires an argument", file=sys.stderr)
                return 2
            try:
                port = int(args[i + 1])
            except ValueError:
                print(f"error: invalid port: {args[i + 1]}", file=sys.stderr)
                return 2
            i += 2
        elif arg.startswith("--port="):
            try:
                port = int(arg.split("=", 1)[1])
            except ValueError:
                print(f"error: invalid port: {arg.split('=', 1)[1]}", file=sys.stderr)
                return 2
            i += 1
        elif arg.startswith("-"):
            print(f"error: unknown option: {arg}", file=sys.stderr)
            return 2
        else:
            positional.append(arg)
            i += 1

    if len(positional) < 2:
        print(
            "Usage: python3 -m kin_diary found <NodeName> <Kin> [<Kin> ...] [--port 8770]",
            file=sys.stderr,
        )
        return 2

    node_name = positional[0]
    residents = positional[1:]

    config_dir = Path.home() / ".config" / "kin_diary"
    db_path = config_dir / f"{node_name.lower()}_node.db"
    if db_path.exists():
        print(f"error: node db for {node_name} already exists: {db_path}", file=sys.stderr)
        return 1

    # Keygen for each Kin that has no key yet
    kin_keys = {}
    for kin in residents:
        kin_keys[kin] = _load_or_create_key(kin)

    # <NodeName>-steward key if none
    steward_author = f"{node_name}-steward"
    steward_key = _load_or_create_key(steward_author)

    # <NodeName>-node key if none
    node_author = f"{node_name}-node"
    node_key = _load_or_create_key(node_author)

    # Found resident rows in NodeStore
    config_dir.mkdir(parents=True, exist_ok=True)
    from .agora.store import NodeStore
    store = NodeStore(db_path, node_name, steward_key_id=steward_key.key_id)
    for kin in residents:
        store.found_resident(kin, kin_keys[kin].key_id)

    # Minimal furnishing so snapshot is real rather than empty
    from .agora.canonical import COLLAB
    from .agora.places import Atlas, sign_place
    node = store.load()
    atlas = Atlas(node, store=store)
    atlas.add_place(sign_place(node_key, "concourse", node_name, "commons"))
    atlas.add_place(sign_place(
        node_key, "table", node_name, "table", parent="concourse",
        ring_to_see=1, points_to=COLLAB))
    atlas.add_place(sign_place(node_key, "stall-1", node_name, "kiosk",
                                parent="concourse"))
    for author in sorted(node.residents):
        atlas.add_place(sign_place(
            node_key, f"door-{author}", node_name, "door", parent="concourse",
            ring_to_see=1, points_to=f"personal:{author}"))

    # Write systemd service file
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_name = f"agora-{node_name.lower()}.service"
    service_path = service_dir / service_name

    repo_root = Path(__file__).resolve().parent.parent
    try:
        rel = (repo_root / "serve_node.py").resolve().relative_to(Path.home().resolve())
        serve_node_exec = f"%h/{rel}"
    except ValueError:
        serve_node_exec = str(repo_root / "serve_node.py")

    exec_args = [
        "/usr/bin/python3", "-u", serve_node_exec,
        node_name, str(port),
        f"steward={steward_key.key_id}",
    ]
    for kin in residents:
        exec_args.append(f"{kin}={kin_keys[kin].key_id}")

    service_content = f"""[Unit]
Description=Agora node ({node_name})
After=network-online.target

[Service]
ExecStart={' '.join(exec_args)}
Restart=on-failure
StandardOutput=append:%h/{node_name.lower()}_node.log
StandardError=append:%h/{node_name.lower()}_node.log

[Install]
WantedBy=default.target
"""
    service_path.write_text(service_content, encoding="utf-8")

    # Run daemon-reload and enable --now
    _setup_systemd(service_name)

    # Print serving line, keys directory, and backup reminder
    keys_dir = Path.home() / ".config" / "kin_diary" / "keys"
    print(
        f"{node_name} serving on :{port} — speaker={node.speaker} "
        f"residents={sorted(node.residents)} node_key={node_key.key_id[:16]}…"
    )
    print(f"keys directory: {keys_dir}")
    print("back this up.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _usage()
        return 2
    cmd = argv[0]
    if cmd == "found":
        return _cmd_found(argv[1:])
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
