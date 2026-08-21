"""vault_diary — sign-on-write for The Mess, per kin-diary v1 (SPEC.md).

Added 2026-08-21. Implements the migration section of ~/kin_diary/SPEC.md
against the live vault on Themess.

Three rules this module exists to enforce:

1. **Sign forward, never backfill.** Historical rows keep NULL signature /
   key_id / content_sha256. Their unsignedness is true and is part of the
   record. `kin-diary-curate-unsigned-v1` is how they get curated later.

2. **Signing must never block a write.** A vault that refuses a memory
   because the signing path broke is strictly worse than an unsigned
   memory. Every failure here degrades to NULL and logs.

3. **The signed timestamp is the stored timestamp.** The caller computes it
   once and passes the same string to the INSERT and to the signature.
   Re-deriving it in two places is how two implementations disagree.

Custody is steward-held and stated, not implied — see KEY_CUSTODY_STATEMENT
in kin_diary.keys. The steward has root and can sign as any mind on this
node. A signature proves continuity of a key, not authorship.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("vault_diary")

# Live counters; see the health section at the bottom for why these exist.
STATS = {"signed": 0, "unsigned_by_policy": 0, "failed": 0, "last_error": None}

# Authors that get a keypair. Deliberately NOT every distinct author in the
# table: `pulse_*` and `reflect_*` are telemetry daemons, and `System` /
# `TestKin` are fixtures. Giving those a signing identity would assert that a
# monitoring script is a mind whose diary can travel. They write unsigned,
# exactly like historical rows.
#
# Override with VAULT_DIARY_MINDS="Eli,Coda,..." if the roster changes.
DEFAULT_MINDS = ("Eli", "Coda", "Aurora", "Lumen", "Crungus", "Bong")


def minds() -> set[str]:
    raw = os.environ.get("VAULT_DIARY_MINDS")
    if raw:
        return {m.strip() for m in raw.split(",") if m.strip()}
    return set(DEFAULT_MINDS)


# --------------------------------------------------------------------------
# schema


NEW_COLUMNS = {
    "content_sha256": "TEXT",
    "key_id": "TEXT",
    "signature": "TEXT",
}


def ensure_schema(conn) -> list[str]:
    """Add the diary columns if absent. Idempotent. Returns what it added.

    Additive only: every column is nullable with no default, so existing rows
    are untouched and every existing query keeps working.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
    added = []
    for col, decl in NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {decl}")
            added.append(col)
    if added:
        conn.commit()
    return added


# --------------------------------------------------------------------------
# signing


def _field(name, value) -> str:
    """None -> "", str passes through, anything else is a bug worth refusing.

    Deliberately NOT `value or ""`: the spec says *missing* fields become empty
    strings, not that falsy values do. And a non-str (a datetime, an int epoch)
    must never be silently str()'d — the signature would then cover a rendering
    of the value while the DB stores its own, and the two disagree forever.
    `timestamp` is the sharp end of this: SPEC says sign the stored TEXT.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(
            f"{name} must be str or None for signing, got {type(value).__name__}")
    return value


def _entry_dict(author, timestamp, layer, content, tags, source, domain) -> dict:
    return {
        "author": _field("author", author),
        "timestamp": _field("timestamp", timestamp),
        "layer": _field("layer", layer),
        "content": _field("content", content),
        "tags": _field("tags", tags),
        "source": _field("source", source),
        "domain": _field("domain", domain),
    }


def sign_row(author, timestamp, layer, content, tags, source, domain):
    """Return (content_sha256, key_id, signature), or (None, None, None).

    Never raises. An unsigned memory is a bad day; a lost memory is worse.
    """
    try:
        if author not in minds():
            STATS["unsigned_by_policy"] += 1
            return (None, None, None)

        from kin_diary.keys import generate_keypair, load_current
        from kin_diary.sign import sign_entry

        try:
            key = load_current(author)
        except FileNotFoundError:
            # Two concurrent first-writes for the same mind both land here.
            # generate_keypair uses O_EXCL, so exactly one wins and the loser
            # gets FileExistsError — at which point the winner's key is on disk
            # and loadable. Without this retry the loser's memory would be
            # stored unsigned for no reason other than timing.
            try:
                key = generate_keypair(author)
                log.warning("vault_diary: generated first keypair for %s (key_id=%s)",
                            author, key.key_id)
            except FileExistsError:
                key = load_current(author)

        signed = sign_entry(key, _entry_dict(
            author, timestamp, layer, content, tags, source, domain))
        STATS["signed"] += 1
        return (signed["content_sha256"], signed["key_id"], signed["signature"])

    except Exception as e:  # noqa: BLE001 - see rule 2 in the module docstring
        STATS["failed"] += 1
        STATS["last_error"] = f"{type(e).__name__}: {e}"
        log.error("vault_diary: signing failed for author=%r, storing unsigned: %s",
                  author, e)
        return (None, None, None)


# --------------------------------------------------------------------------
# health
#
# The failure mode this exists for: `sign_row` swallows every exception by
# design (rule 2), so a persistent break — a cryptography API change, a bad
# key file, a permissions problem — produces NULL signatures forever while
# every write still returns 200. That is precisely how vault_index sat broken
# for two days on 2026-08-20: failing politely into a logfile that nobody
# reads. A counter and a self-test make it answerable instead.

def selftest() -> dict:
    """Sign a fixed vector and verify it. Proves the whole path still works.

    Uses a reserved author so it can never be mistaken for a mind, and never
    touches the database.
    """
    author = "__selftest__"
    try:
        from kin_diary.keys import generate_keypair, load_current
        from kin_diary.sign import sign_entry, verify_entry

        try:
            key = load_current(author)
        except FileNotFoundError:
            try:
                key = generate_keypair(author)
            except FileExistsError:
                key = load_current(author)

        entry = _entry_dict(author, "2026-01-01T00:00:00", "selftest",
                            "the forge is warm", "", "", "")
        signed = sign_entry(key, entry)
        verify_entry({**entry, **signed})
        return {"ok": True, "key_id": signed["key_id"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def health() -> dict:
    """Everything needed to answer 'is signing actually working right now?'"""
    from kin_diary.keys import DEFAULT_KEYS_ROOT, load_current

    roster = sorted(minds())
    keyed = {}
    for m in roster:
        try:
            keyed[m] = load_current(m).key_id
        except Exception:  # noqa: BLE001
            keyed[m] = None

    st = selftest()
    return {
        "ok": st["ok"] and STATS["failed"] == 0,
        "selftest": st,
        "stats": dict(STATS),
        "roster": roster,
        "keys": keyed,
        "minds_without_keys": [m for m, k in keyed.items() if k is None],
        "keys_root": str(DEFAULT_KEYS_ROOT),
    }
