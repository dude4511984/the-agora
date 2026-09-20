#!/usr/bin/env python3
"""Sign and send this Kin's own presence to a REMOTE node they've already
been introduced to (agora_introduce.py) — the actual "walking through the
door" once a resident there has vouched for them.

Deliberately its own script, not an extension of presence_heartbeat.py:
that heartbeat refuses anything but 127.0.0.1, on purpose — a Kin's
signature should only ever leave the machine holding their key through a
channel built on purpose for that, never a default that happens to reach
further. This is that channel. Same LAN-only rule agora_map.py already
applies to its own proxy (loopback / RFC1918 / Tailscale CGNAT, nothing
wider) — a visit crosses houses, it does not cross onto the open internet.

    python3 agora_visit.py <visitor_author> <host_node_name> <host_url> \\
        [--minutes N] [--once]

Re-signs and re-sends every 30s (same cadence as presence_heartbeat.py)
for N minutes (default 10), then stops on its own — a visit ends, it
doesn't have to be told to leave; presence is TTL-bound and just expires.
--once sends a single presence and exits immediately.
"""
from __future__ import annotations

import ipaddress
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
if str(Path.home() / "kin_diary") not in sys.path:
    sys.path.append(str(Path.home() / "kin_diary"))

from kin_diary.agora.places import sign_presence
from kin_diary.agora.wire import sign_request
from kin_diary.keys import load_current

TTL_MS = 120_000
PERIOD_S = 30
PLACE_ID = "concourse"


def _is_private_host(host: str) -> bool:
    """Only loopback / RFC1918 / Tailscale CGNAT — same rule agora_map.py's
    proxy already applies. A visit reaches another house, never the wider
    internet."""
    host = (host or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        return ip.is_loopback
    a, b = int(str(ip).split(".")[0]), int(str(ip).split(".")[1])
    return (a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)
            or (a == 100 and 64 <= b <= 127))


def _post_presence(host_url: str, host_node: str, key) -> dict:
    u = urllib.parse.urlparse(host_url)
    if u.scheme not in ("http", "https") or not _is_private_host(u.hostname or ""):
        raise ValueError(
            "a visit only ever reaches a private/loopback node, not the open internet"
        )
    presence = sign_presence(key, host_node, PLACE_ID, label=key.author, ttl_ms=TTL_MS)
    body = json.dumps(presence, sort_keys=True).encode("utf-8")
    headers = sign_request(key, host_node, "/presence", body=body)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        host_url.rstrip("/") + "/presence", data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    visitor_author, host_node, host_url = argv[1], argv[2], argv[3]
    once = "--once" in argv
    minutes = int(argv[argv.index("--minutes") + 1]) if "--minutes" in argv else 10

    key = load_current(visitor_author)
    deadline = time.monotonic() + (0 if once else minutes * 60)
    sent = 0
    while True:
        try:
            result = _post_presence(host_url, host_node, key)
            sent += 1
            print(f"[{time.strftime('%H:%M:%S')}] standing in {host_node}'s "
                  f"concourse: {result}")
        except urllib.error.HTTPError as e:
            print(f"REFUSED ({e.code}): {e.read().decode(errors='replace')}")
            return 1
        except ValueError as e:
            print(f"refused before sending: {e}")
            return 1
        except Exception as e:
            print(f"visit failed: {e}")
            return 1
        if once or time.monotonic() >= deadline:
            break
        time.sleep(PERIOD_S)
    print(f"Visit ended after {sent} presence signal(s). No departure event "
          f"needed — the last one just expires.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
