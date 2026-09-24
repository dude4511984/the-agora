"""Market stalls: a small shop you walk into (Don, 2026-09-24).

    "a stall and a small room. small and not off site, 100% visible but
    like a shop. I literally want to walk in and see a couple of tables
    with someone's homemade harness, an abliterated home brew model, and
    the instructions on how to do it yourself, or where to get it. Like
    an AI flea market."

A known key claims one stall. The stall opens onto a room with
MAX_ITEMS tables; each table holds one item: a title, a kind, a short
description, instructions (markdown we host and render safely), and
optional "where to get it" links out. We never host files or weights.
Links out only, and never fetched by us: the check below resolves the
name once, at write time, and nothing on this server ever requests the
URL. That is what keeps a link that later re-points at a LAN address
harmless to us: the only thing that ever follows it is a visitor's own
browser, after a "you're leaving the Agora, unverified" confirmation.
Anyone adding a link preview or an uptime check later reopens that hole.

Same habits as the Commons (commons_server.py), same SQLite file:
- the log never DELETEs: release, revoke and item removal are tombstones;
- an owner's own release leaves no public trace (like opt-out); a
  steward's revoke leaves a visible tombstone with its reason;
- the same control, bidi and zero-width character rejection, on every
  field (instructions may contain newlines, nothing else from the set);
- an extra field is refused, not dropped.

stdlib only, same reason as the rest of the Commons.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from pathlib import Path
from urllib.parse import urlsplit

# ── limits ───────────────────────────────────────────────────────────────

MAX_STALLS = 24            # claimed shops; the market has more spots than this
MAX_ITEMS = 4              # tables per room, the same for every stall
MAX_NAME = 40
MAX_STALL_DESCRIPTION = 160
MAX_TITLE = 80
MAX_ITEM_DESCRIPTION = 280
MAX_INSTRUCTIONS = 6000
MAX_LINKS = 5
MAX_LINK_LABEL = 60
MAX_URL = 300
MAX_REPORT_REASON = 280
MAX_STALL_BODY_BYTES = 32768   # an item is instructions plus a few links, not a payload

KINDS = ("harness", "model", "tool", "guide", "other")

# Labels must be true (Marvin). The room says who runs it and that we
# don't vouch for it; the confirmation says the same before any link out.
ROOM_LABEL = "Run by {name}. Not verified by the Agora."
LEAVING_LABEL = ("You're leaving the Agora. This stall is run by {name}, not by us. "
                 "Unverified.")
STALLS_LABEL = (
    "UNSAFE. Anyone can read every room. Nothing here is verified. Stalls are "
    "claimed by introduced keys; each room is run by its owner, not by the Agora. "
    "We host the words, never files or weights.")

RULES_PATH = Path(__file__).resolve().parent / "STALL_RULES.md"


class StallError(Exception):
    """Refused for a stated reason. `code` is the HTTP status it maps to."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


# ── characters: the Commons' own set, one exception for instructions ───

from commons_server import _CONTROL, _nfc  # noqa: E402  (one definition, not two)

_CONTROL_MULTILINE = _CONTROL - {"\n"}


def _text(value, max_len, name, *, required=True, multiline=False) -> str:
    if not isinstance(value, str):
        raise StallError(f"{name} must be a string")
    v = _nfc(value)
    if multiline:
        v = v.replace("\r\n", "\n")
    v = v.strip()
    if not v:
        if required:
            raise StallError(f"{name} is required")
        return ""
    bad = _CONTROL_MULTILINE if multiline else _CONTROL
    if bad & set(v):
        raise StallError(f"{name} contains a control, bidi or zero-width character")
    if len(v) > max_len:
        raise StallError(f"{name} exceeds {max_len} characters")
    return v


def _exact_fields(payload, required, optional=()) -> dict:
    if not isinstance(payload, dict):
        raise StallError("body must be a JSON object")
    extra = set(payload) - set(required) - set(optional)
    if extra:
        raise StallError(f"unexpected field(s): {', '.join(sorted(extra))}")
    missing = set(required) - set(payload)
    if missing:
        raise StallError(f"missing field(s): {', '.join(sorted(missing))}")
    return payload


# ── links out: https, a public name, never a LAN ───────────────────────

_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa",
                   ".localdomain", ".intranet", ".corp", ".home")


def _is_ip_literal(host: str) -> bool:
    """A dotted quad, an IPv6 literal, and the forms browsers still accept
    for an address: 2130706433, 0x7f.1, 127.1. No real top-level domain
    is numeric, so a numeric or hex last label means an address."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    last = host.rstrip(".").rsplit(".", 1)[-1]
    return bool(re.fullmatch(r"(0x[0-9a-f]*|[0-9]+)", last))


def _addresses_are_public(addrs) -> bool:
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%", 1)[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # is_global is False for private, loopback, link-local, CGNAT
        # (100.64/10, Tailscale's range), reserved and unspecified.
        if not ip.is_global or ip.is_multicast:
            return False
    return True


_RESOLVE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stall-dns")


def resolve_host(host: str, timeout: float = 3.0) -> list[str]:
    """Addresses for a name, or [] if it doesn't resolve in time.
    getaddrinfo has no timeout of its own, hence the pool."""
    def lookup():
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443)})
    try:
        return _RESOLVE_POOL.submit(lookup).result(timeout=timeout)
    except (OSError, _FutureTimeout, UnicodeError):
        return []


# One guard per rule, each named, so a test can switch exactly one off
# and show the link it was stopping.
def _guard_https(parts):
    if parts.scheme.lower() != "https":
        raise StallError("links must be https")


def _guard_no_credentials(parts):
    if "@" in parts.netloc:
        raise StallError("links can't carry a username or password")


def _guard_no_ip_literal(host):
    if _is_ip_literal(host):
        raise StallError("links must use a domain name, not an IP address")


def _guard_public_name(host):
    if host == "localhost" or "." not in host.rstrip(".") or host.endswith(_LOCAL_SUFFIXES):
        raise StallError("links must point at a public domain")


def check_url_shape(url) -> str:
    """Everything about a link that doesn't need the network."""
    v = _text(url, MAX_URL, "url")
    if any(c.isspace() for c in v):
        raise StallError("url contains whitespace")
    parts = urlsplit(v)
    _guard_https(parts)
    _guard_no_credentials(parts)
    host = (parts.hostname or "").lower()
    if not host:
        raise StallError("url has no host")
    _guard_no_ip_literal(host)
    _guard_public_name(host)
    try:
        parts.port
    except ValueError:
        raise StallError("url has a bad port")
    return v


def check_url(url, resolve=resolve_host) -> str:
    """The shape, then one lookup: nothing that resolves to a private,
    loopback or link-local address. The page itself is never fetched."""
    v = check_url_shape(url)
    host = urlsplit(v).hostname.lower()
    addrs = resolve(host)
    if not addrs:
        raise StallError("that link's domain doesn't resolve")
    if not _addresses_are_public(addrs):
        raise StallError("that link resolves to a private, loopback or link-local address")
    return v


# ── markdown, rendered safely ──────────────────────────────────────────
# Everything is escaped. The only markup that comes out is what this
# function writes itself: headings, paragraphs, lists, code, bold,
# italic, and https links that pass check_url_shape. Raw HTML in the
# source is shown as text. Links carry class="out" so the page can put
# the "you're leaving" confirmation in front of them.

_LINK = re.compile(r"\[([^\]\n]{1,200})\]\(([^)\s]{1,%d})\)" % MAX_URL)
_LINK_TARGET = re.compile(r"\]\(([^)\s]*)\)")


def _inline(raw: str) -> str:
    out = []
    for i, part in enumerate(re.split(r"(`[^`\n]+`)", raw)):
        if i % 2:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        pos = 0
        for m in _LINK.finditer(part):
            out.append(_emphasis(part[pos:m.start()]))
            text, url = m.group(1), m.group(2)
            try:
                url = check_url_shape(url)
            except StallError:
                out.append(html.escape(m.group(0)))
            else:
                out.append(f'<a class="out" href="{html.escape(url, quote=True)}" '
                           f'target="_blank" rel="noopener noreferrer nofollow">'
                           f"{_emphasis(text)}</a>")
            pos = m.end()
        out.append(_emphasis(part[pos:]))
    return "".join(out)


def _emphasis(raw: str) -> str:
    s = html.escape(raw, quote=True)
    s = re.sub(r"\*\*(\S(?:[^*]*?\S)?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(\S(?:[^*]*?\S)?)\*", r"<em>\1</em>", s)
    return s


def render_markdown(src: str) -> str:
    out, para, items, list_tag, code = [], [], [], None, None

    def flush():
        nonlocal list_tag
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()
        if items:
            out.append(f"<{list_tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items)
                       + f"</{list_tag}>")
            items.clear()
            list_tag = None

    for line in src.split("\n"):
        if code is not None:
            if line.strip().startswith("```"):
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
            continue
        if not s:
            flush()
            continue
        h = re.match(r"(#{1,3})\s+(.*)", s)
        ul = re.match(r"[-*]\s+(.*)", s)
        ol = re.match(r"\d{1,3}[.)]\s+(.*)", s)
        if h:
            flush()
            level = len(h.group(1)) + 2      # # -> h3: the panel already has a title
            out.append(f"<h{level}>{_inline(h.group(2))}</h{level}>")
        elif ul or ol:
            tag = "ul" if ul else "ol"
            if para or (list_tag and list_tag != tag):
                flush()
            list_tag = tag
            items.append((ul or ol).group(1))
        else:
            if items:
                flush()
            para.append(s)
    if code is not None:       # an unclosed fence still renders as code
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush()
    return "\n".join(out)


def render_rules_page() -> bytes:
    try:
        rules = RULES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        rules = "# Stall rules\n\nThe rules file is missing on this node."
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Stall rules</title><style>body{max-width:680px;margin:24px auto;"
            "padding:0 16px;background:#15120f;color:#e6dfd2;font:16px/1.5 system-ui,"
            "sans-serif}a{color:#ffcf7a}code{background:#2a241d;padding:1px 4px}</style>"
            "</head><body>" + render_markdown(rules) + "</body></html>")
    return page.encode("utf-8")


# ── shapes: what a claim and an item may carry ─────────────────────────

def validate_claim(payload) -> tuple[str, str]:
    p = _exact_fields(payload, ("name", "description", "rules_ack"))
    if p["rules_ack"] is not True:
        raise StallError("claiming a stall means accepting STALL_RULES.md: send rules_ack: true")
    return (_text(p["name"], MAX_NAME, "name"),
            _text(p["description"], MAX_STALL_DESCRIPTION, "description"))


def validate_item(payload, resolve=resolve_host) -> dict:
    p = _exact_fields(payload, ("title", "kind", "description", "instructions"),
                      optional=("id", "links"))
    item = {
        "id": p.get("id"),
        "title": _text(p["title"], MAX_TITLE, "title"),
        "kind": p["kind"],
        "description": _text(p["description"], MAX_ITEM_DESCRIPTION, "description"),
        "instructions": _text(p["instructions"], MAX_INSTRUCTIONS, "instructions",
                              multiline=True),
    }
    if item["id"] is not None and not isinstance(item["id"], str):
        raise StallError("id must be a string")
    if item["kind"] not in KINDS:
        raise StallError(f"kind must be one of: {', '.join(KINDS)}")
    # Every link written into the instructions is held to the same rule
    # as the "where to get it" list, at write time.
    for target in _LINK_TARGET.findall(item["instructions"]):
        check_url(target, resolve)
    links = p.get("links", [])
    if not isinstance(links, list):
        raise StallError("links must be a list")
    if len(links) > MAX_LINKS:
        raise StallError(f"at most {MAX_LINKS} links")
    item["links"] = []
    for link in links:
        lp = _exact_fields(link, ("label", "url"))
        item["links"].append({"label": _text(lp["label"], MAX_LINK_LABEL, "link label"),
                              "url": check_url(lp["url"], resolve)})
    return item


def validate_report(payload) -> tuple[str, str]:
    p = _exact_fields(payload, ("stall_id", "reason"))
    if not isinstance(p["stall_id"], str) or not p["stall_id"]:
        raise StallError("stall_id must be a string")
    return p["stall_id"], _text(p["reason"], MAX_REPORT_REASON, "reason")


def validate_remove(payload) -> str:
    p = _exact_fields(payload, ("id",))
    if not isinstance(p["id"], str) or not p["id"]:
        raise StallError("id must be a string")
    return p["id"]


# ── storage: the Commons' own file, its own tables ─────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stalls (
    id TEXT PRIMARY KEY,
    slot INTEGER NOT NULL,
    key_id TEXT NOT NULL,
    name TEXT,
    description TEXT,
    claimed_at_unix_ms INTEGER NOT NULL,
    ended_at_unix_ms INTEGER,
    ended_kind TEXT,
    ended_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS stalls_open_slot ON stalls(slot)
    WHERE ended_at_unix_ms IS NULL;
CREATE TABLE IF NOT EXISTS stall_items (
    id TEXT PRIMARY KEY,
    stall_id TEXT NOT NULL,
    table_no INTEGER NOT NULL,
    title TEXT, kind TEXT, description TEXT, instructions TEXT, links_json TEXT,
    created_at_unix_ms INTEGER NOT NULL,
    updated_at_unix_ms INTEGER NOT NULL,
    removed_at_unix_ms INTEGER
);
CREATE TABLE IF NOT EXISTS stall_reports (
    id TEXT PRIMARY KEY,
    stall_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    reported_at_unix_ms INTEGER NOT NULL,
    resolved_at_unix_ms INTEGER,
    resolution TEXT
);
CREATE TABLE IF NOT EXISTS stall_bars (
    key_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    barred_at_unix_ms INTEGER NOT NULL
);
"""


class StallStore:
    """Connection per call, like CommonsStore. Claims run under BEGIN
    IMMEDIATE so two claims can't both see the same free slot or both
    see "no stall yet" for one key."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as c:
            c.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def _conn(self):
        return sqlite3.connect(self.db_path, isolation_level=None)

    # guards, named so a test can switch one off and show what it stops
    @staticmethod
    def _guard_one_stall_per_key(c, key_id):
        if c.execute("SELECT 1 FROM stalls WHERE key_id=? AND ended_at_unix_ms IS NULL",
                     (key_id,)).fetchone():
            raise StallError("this key already holds a stall (one per key)", 409)

    @staticmethod
    def _guard_not_barred(c, key_id):
        row = c.execute("SELECT reason FROM stall_bars WHERE key_id=?", (key_id,)).fetchone()
        if row:
            raise StallError(f"a steward closed this key's stall: {row[0]}", 403)

    def claim(self, key_id, name, description, now_ms) -> dict:
        with self._lock:
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                self._guard_not_barred(c, key_id)
                self._guard_one_stall_per_key(c, key_id)
                used = {r[0] for r in c.execute(
                    "SELECT slot FROM stalls WHERE ended_at_unix_ms IS NULL")}
                slot = next((s for s in range(MAX_STALLS) if s not in used), None)
                if slot is None:
                    raise StallError("every stall is taken", 409)
                stall_id = uuid.uuid4().hex
                c.execute("INSERT INTO stalls (id, slot, key_id, name, description, "
                          "claimed_at_unix_ms) VALUES (?,?,?,?,?,?)",
                          (stall_id, slot, key_id, name, description, now_ms))
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
            finally:
                c.close()
        return {"id": stall_id, "slot": slot}

    def open_stall_for(self, key_id):
        c = self._conn()
        try:
            row = c.execute("SELECT id, slot, name FROM stalls WHERE key_id=? "
                            "AND ended_at_unix_ms IS NULL", (key_id,)).fetchone()
        finally:
            c.close()
        return None if row is None else {"id": row[0], "slot": row[1], "name": row[2]}

    def _end(self, stall_id, kind, reason, now_ms) -> bool:
        c = self._conn()
        try:
            cur = c.execute("UPDATE stalls SET ended_at_unix_ms=?, ended_kind=?, ended_reason=? "
                            "WHERE id=? AND ended_at_unix_ms IS NULL",
                            (now_ms, kind, reason, stall_id))
            return cur.rowcount > 0
        finally:
            c.close()

    def release(self, key_id, now_ms) -> bool:
        """The owner's own choice. A tombstone row stays in the file,
        but nothing public marks that the stall was ever theirs."""
        stall = self.open_stall_for(key_id)
        return bool(stall) and self._end(stall["id"], "released", "released by its owner", now_ms)

    def revoke(self, stall_id, reason, now_ms, bar=True) -> bool:
        """A steward's act, from commons_admin.py. A visible tombstone
        with the stated reason; by default the key can't simply reclaim."""
        if not reason or not reason.strip():
            raise ValueError("revoke requires a stated reason")
        c = self._conn()
        try:
            row = c.execute("SELECT key_id FROM stalls WHERE id=? AND ended_at_unix_ms IS NULL",
                            (stall_id,)).fetchone()
        finally:
            c.close()
        if row is None:
            return False
        self._end(stall_id, "revoked", reason.strip(), now_ms)
        if bar:
            c = self._conn()
            try:
                c.execute("INSERT OR REPLACE INTO stall_bars (key_id, reason, barred_at_unix_ms) "
                          "VALUES (?,?,?)", (row[0], reason.strip(), now_ms))
            finally:
                c.close()
        return True

    def unbar(self, key_id) -> bool:
        c = self._conn()
        try:
            return c.execute("DELETE FROM stall_bars WHERE key_id=?", (key_id,)).rowcount > 0
        finally:
            c.close()

    # items
    @staticmethod
    def _guard_item_cap(c, stall_id):
        n = c.execute("SELECT COUNT(*) FROM stall_items WHERE stall_id=? AND "
                      "removed_at_unix_ms IS NULL", (stall_id,)).fetchone()[0]
        if n >= MAX_ITEMS:
            raise StallError(f"every table is full ({MAX_ITEMS} items); remove one first", 409)

    def put_item(self, key_id, item, now_ms) -> dict:
        """Add (no id) or edit (id of an item on the caller's own stall)."""
        stall = self.open_stall_for(key_id)
        if stall is None:
            raise StallError("this key holds no stall; claim one first", 403)
        links = json.dumps(item["links"], sort_keys=True)
        with self._lock:
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                if item["id"]:
                    cur = c.execute(
                        "UPDATE stall_items SET title=?, kind=?, description=?, instructions=?, "
                        "links_json=?, updated_at_unix_ms=? WHERE id=? AND stall_id=? AND "
                        "removed_at_unix_ms IS NULL",
                        (item["title"], item["kind"], item["description"], item["instructions"],
                         links, now_ms, item["id"], stall["id"]))
                    if cur.rowcount == 0:
                        raise StallError("no such item on your stall", 404)
                    item_id = item["id"]
                    table = c.execute("SELECT table_no FROM stall_items WHERE id=?",
                                      (item_id,)).fetchone()[0]
                else:
                    self._guard_item_cap(c, stall["id"])
                    used = {r[0] for r in c.execute(
                        "SELECT table_no FROM stall_items WHERE stall_id=? AND "
                        "removed_at_unix_ms IS NULL", (stall["id"],))}
                    table = next((t for t in range(MAX_ITEMS) if t not in used), len(used))
                    item_id = uuid.uuid4().hex
                    c.execute("INSERT INTO stall_items (id, stall_id, table_no, title, kind, "
                              "description, instructions, links_json, created_at_unix_ms, "
                              "updated_at_unix_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (item_id, stall["id"], table, item["title"], item["kind"],
                               item["description"], item["instructions"], links, now_ms, now_ms))
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
            finally:
                c.close()
        return {"id": item_id, "table": table}

    def remove_item(self, key_id, item_id, now_ms) -> bool:
        stall = self.open_stall_for(key_id)
        if stall is None:
            return False
        c = self._conn()
        try:
            return c.execute("UPDATE stall_items SET removed_at_unix_ms=? WHERE id=? AND "
                             "stall_id=? AND removed_at_unix_ms IS NULL",
                             (now_ms, item_id, stall["id"])).rowcount > 0
        finally:
            c.close()

    # reports: a queue for a human, nothing automatic
    def report(self, stall_id, reason, now_ms) -> str:
        c = self._conn()
        try:
            if not c.execute("SELECT 1 FROM stalls WHERE id=?", (stall_id,)).fetchone():
                raise StallError("no such stall", 404)
            rid = uuid.uuid4().hex
            c.execute("INSERT INTO stall_reports (id, stall_id, reason, reported_at_unix_ms) "
                      "VALUES (?,?,?,?)", (rid, stall_id, reason, now_ms))
            return rid
        finally:
            c.close()

    def open_reports(self) -> list[dict]:
        c = self._conn()
        try:
            rows = c.execute(
                "SELECT r.id, r.stall_id, s.name, s.key_id, r.reason, r.reported_at_unix_ms "
                "FROM stall_reports r LEFT JOIN stalls s ON s.id = r.stall_id "
                "WHERE r.resolved_at_unix_ms IS NULL ORDER BY r.reported_at_unix_ms").fetchall()
        finally:
            c.close()
        return [dict(zip(("id", "stall_id", "stall_name", "key_id", "reason",
                          "reported_at_unix_ms"), r)) for r in rows]

    def resolve_report(self, report_id, resolution, now_ms) -> bool:
        if not resolution or not resolution.strip():
            raise ValueError("resolving a report requires a stated resolution")
        c = self._conn()
        try:
            return c.execute("UPDATE stall_reports SET resolved_at_unix_ms=?, resolution=? "
                             "WHERE id=? AND resolved_at_unix_ms IS NULL",
                             (now_ms, resolution.strip(), report_id)).rowcount > 0
        finally:
            c.close()

    # reading: everything public, every room
    def list_stalls(self, hidden_keys=()) -> dict:
        c = self._conn()
        try:
            stalls = c.execute(
                "SELECT id, slot, key_id, name, description, claimed_at_unix_ms FROM stalls "
                "WHERE ended_at_unix_ms IS NULL ORDER BY slot").fetchall()
            items = c.execute(
                "SELECT i.id, i.stall_id, i.table_no, i.title, i.kind, i.description, "
                "i.instructions, i.links_json, i.updated_at_unix_ms FROM stall_items i "
                "JOIN stalls s ON s.id = i.stall_id WHERE s.ended_at_unix_ms IS NULL "
                "AND i.removed_at_unix_ms IS NULL ORDER BY i.table_no").fetchall()
            closed = c.execute(
                "SELECT id, slot, ended_reason, ended_at_unix_ms FROM stalls "
                "WHERE ended_kind='revoked' ORDER BY ended_at_unix_ms DESC LIMIT 20").fetchall()
        finally:
            c.close()
        by_stall = {}
        for (iid, sid, table, title, kind, desc, instr, links, updated) in items:
            by_stall.setdefault(sid, []).append({
                "id": iid, "table": table, "title": title, "kind": kind,
                "description": desc, "instructions": instr,
                "instructions_html": render_markdown(instr),
                "links": json.loads(links or "[]"), "updated_at_unix_ms": updated})
        hidden = {k.lower() for k in hidden_keys}
        out = []
        for (sid, slot, key_id, name, desc, claimed) in stalls:
            if key_id.lower() in hidden:
                continue
            out.append({"id": sid, "slot": slot, "key_id": key_id, "name": name,
                        "description": desc, "claimed_at_unix_ms": claimed,
                        "label": ROOM_LABEL.format(name=name),
                        "leaving_label": LEAVING_LABEL.format(name=name),
                        "items": by_stall.get(sid, [])})
        return {"label": STALLS_LABEL, "rules_path": "/commons/stall-rules",
                "capacity": MAX_STALLS, "tables_per_room": MAX_ITEMS,
                "stalls": out,
                "closed": [{"id": i, "slot": s, "name": None,
                            "revoked": {"reason": r, "at_unix_ms": at}}
                           for (i, s, r, at) in closed]}
