#!/usr/bin/env python3
"""Talk to a remote Agora node. Holds only this machine's own key.

    python3 agora_client.py facts   <url>
    python3 agora_client.py keygen  <author>
    python3 agora_client.py keyid   <author>
    python3 agora_client.py intro   <author> <url> <node> <resident_key_id>
    python3 agora_client.py read    <author> <url> <node> <board>
    python3 agora_client.py post    <author> <url> <node> <board> <text>
    python3 agora_client.py submit  <url> <kind> <file.json>
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora.events import start_key_intro  # noqa: E402
from kin_diary.agora.wire import sign_request  # noqa: E402
from kin_diary.keys import generate_keypair, load_current  # noqa: E402
from kin_diary.sign import sign_entry  # noqa: E402


def _req(url, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode(errors="replace")}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]

    if cmd == "facts":
        print(json.dumps(_req(argv[2].rstrip("/") + "/"), indent=2))
        return 0

    if cmd == "keygen":
        rec = generate_keypair(argv[2])
        print(rec.key_id)
        return 0

    if cmd == "keyid":
        print(load_current(argv[2]).key_id)
        return 0

    if cmd == "intro":
        author, url, node, resident = argv[2], argv[3], argv[4], argv[5]
        blob = start_key_intro(load_current(author), node, resident)
        print(json.dumps(blob, indent=2, sort_keys=True))
        return 0

    if cmd == "submit":
        url, kind, path = argv[2], argv[3], argv[4]
        payload = json.loads(Path(path).read_text())
        print(json.dumps(_req(f"{url.rstrip('/')}/{kind}", payload, method="POST"), indent=2))
        return 0

    if cmd == "read":
        author, url, node, board = argv[2], argv[3], argv[4], argv[5]
        path = f"/board/{board}"
        headers = {}
        if author != "-":
            headers = sign_request(load_current(author), node, path)
        print(json.dumps(_req(url.rstrip("/") + path, headers=headers), indent=2))
        return 0

    if cmd == "post":
        author, url, node, board, text = argv[2], argv[3], argv[4], argv[5], argv[6]
        key = load_current(author)
        import datetime
        entry = sign_entry(key, {
            "author": author,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": text,
        })
        body = json.dumps({"board": board, "entry": entry}).encode()
        headers = sign_request(key, node, "/post", body=body)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url.rstrip("/") + "/post", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(json.dumps(json.load(resp), indent=2))
        except urllib.error.HTTPError as e:
            print(json.dumps({"http_error": e.code,
                              "body": e.read().decode(errors="replace")}, indent=2))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
