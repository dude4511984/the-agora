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


def _setup_systemd(service_name: str) -> str | None:
    """None if systemd took the service; otherwise why it didn't.

    It used to swallow every failure and let found print "serving on :8770"
    regardless. On a box with no systemd user session (a container, a plain
    ssh login) nothing was serving (found following STAND_UP_A_NODE.md on a
    clean box, 2026-09-24)."""
    for cmd in (["daemon-reload"], ["enable", "--now", service_name]):
        try:
            res = subprocess.run(["systemctl", "--user"] + cmd, capture_output=True, text=True)
        except FileNotFoundError:
            return "systemctl is not installed"
        if res.returncode != 0:
            return (res.stderr.strip().splitlines() or [f"systemctl --user {' '.join(cmd)} failed"])[-1]
    return None


def _answers(port: int, wait_s: float = 8.0) -> bool:
    """Does a node banner actually come back on this port?"""
    import time, urllib.request
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                if b"EverySynthetic Node" in r.read(200):
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


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
        # The Python running found, not /usr/bin/python3: in a venv (how a
        # stranger on a modern distro installs cryptography) the system Python
        # doesn't have it, and the service would die on start.
        sys.executable, "-u", serve_node_exec,
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

    # Run daemon-reload and enable --now, then believe only the port.
    why_not = _setup_systemd(service_name)
    if why_not is None and not _answers(port):
        why_not = f"systemd took the service, but nothing answers on :{port} (see ~/{node_name.lower()}_node.log)"

    keys_dir = Path.home() / ".config" / "kin_diary" / "keys"
    facts = (f"speaker={node.speaker} residents={sorted(node.residents)} "
             f"node_key={node_key.key_id[:16]}…")
    if why_not is None:
        print(f"{node_name} serving on :{port} — {facts}")
    else:
        print(f"{node_name} founded, NOT serving yet — {facts}")
        print(f"  why: {why_not}")
        home = str(Path.home())
        print("  start it by hand:  " + " ".join(a.replace("%h", home) for a in exec_args))
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
        # A missing file used to be a raw traceback, and nothing said what the
        # file is (found following STAND_UP_A_NODE.md, 2026-09-24).
        shape = ('one JSON object per line, e.g. '
                 '{"content": "what I remember", "timestamp": "2026-09-24 09:00:00"}; '
                 'unsigned lines are signed with the author\'s key on export')
        if src is not None and not src.is_file():
            print(f"error: no entries file at {src}. It is {shape}.", file=sys.stderr)
            return 2
        raw = src.read_text(encoding="utf-8") if src else sys.stdin.read()
        entries = []
        for n, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"error: line {n} of the entries is not JSON ({e.msg}). It should be {shape}.",
                      file=sys.stderr)
                return 2
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
