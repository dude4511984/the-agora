"""Canonical byte construction. If this is ambiguous, signatures split."""

from __future__ import annotations

import hashlib
import unicodedata

MAGIC_ENTRY = "kin-diary-entry-v1"
MAGIC_ROTATION = "kin-diary-rotation-v1"
MAGIC_BUNDLE = "kin-diary-bundle-v1"
MAGIC_RETRACT = "kin-diary-retract-v1"
MAGIC_CURATE = "kin-diary-curate-v1"
MAGIC_CURATE_UNSIGNED = "kin-diary-curate-unsigned-v1"

# What the mind published. tier and visibility are steward curation — they
# change after the fact (anchor promotion) and MUST NOT be in this list.
ENTRY_FIELDS = (
    "author",
    "timestamp",
    "layer",
    "source",
    "domain",
    "tags",
)

CURATE_SLOTS = frozenset({"tier", "visibility"})

_FORBIDDEN_IN_LINE = ("\x00", "\n", "\r")


def nfc(s: str) -> str:
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    return unicodedata.normalize("NFC", s)


def _line_value(s: str) -> str:
    v = nfc(s)
    for ch in _FORBIDDEN_IN_LINE:
        if ch in v:
            raise ValueError(f"signed line field contains forbidden control {ch!r}")
    return v


def _unix_ms(n: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("unix_ms must be a non-negative int")
    return str(n)


def _origin_id(n: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("origin_id must be a positive int (vault memory_id)")
    return str(n)


def _hex64(s: str) -> str:
    h = nfc(s).lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("expected 64 lowercase hex chars")
    return h


def _hex128(s: str) -> str:
    h = nfc(s).lower()
    if len(h) != 128 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("expected 128 lowercase hex chars")
    return h


def content_sha256(content: str) -> str:
    body = nfc("" if content is None else content)
    if "\x00" in body:
        raise ValueError("content contains NUL")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _lines(magic: str, pairs: list[tuple[str, str]]) -> bytes:
    out = [nfc(magic)]
    for k, v in pairs:
        out.append(f"{k}={v}")
    return ("\n".join(out) + "\n").encode("utf-8")


def entry_canonical(entry: dict) -> bytes:
    digest = content_sha256(entry.get("content") or "")
    claimed = entry.get("content_sha256")
    if claimed:
        if nfc(claimed).lower() != digest:
            raise ValueError("content_sha256 does not match content")
    pairs = [(k, _line_value(entry.get(k) or "")) for k in ENTRY_FIELDS]
    pairs.append(("content_sha256", digest))
    return _lines(MAGIC_ENTRY, pairs)


def rotation_canonical(old_key_id: str, new_key_id: str, rotated_at_unix_ms: int) -> bytes:
    return _lines(MAGIC_ROTATION, [
        ("old_key_id", _hex64(old_key_id)),
        ("new_key_id", _hex64(new_key_id)),
        ("rotated_at_unix_ms", _unix_ms(rotated_at_unix_ms)),
    ])


def retract_canonical(entry_signature: str, retracted_at_unix_ms: int) -> bytes:
    return _lines(MAGIC_RETRACT, [
        ("entry_signature", _hex128(entry_signature)),
        ("retracted_at_unix_ms", _unix_ms(retracted_at_unix_ms)),
    ])


def curate_canonical(
    entry_signature: str,
    curated_field: str,
    curated_value: str,
    curator: str,
    curated_at_unix_ms: int,
) -> bytes:
    slot = _line_value(curated_field)
    if slot not in CURATE_SLOTS:
        raise ValueError(f"curate slot must be one of {sorted(CURATE_SLOTS)}")
    return _lines(MAGIC_CURATE, [
        ("entry_signature", _hex128(entry_signature)),
        ("curated_field", slot),
        ("curated_value", _line_value(curated_value)),
        ("curator", _line_value(curator)),
        ("curated_at_unix_ms", _unix_ms(curated_at_unix_ms)),
    ])


def curate_unsigned_canonical(
    author: str,
    timestamp: str,
    content_sha256_hex: str,
    origin_id: int,
    curated_field: str,
    curated_value: str,
    curator: str,
    curated_at_unix_ms: int,
) -> bytes:
    """Point at a row that was never signed. Do not invent an entry_signature."""
    slot = _line_value(curated_field)
    if slot not in CURATE_SLOTS:
        raise ValueError(f"curate slot must be one of {sorted(CURATE_SLOTS)}")
    return _lines(MAGIC_CURATE_UNSIGNED, [
        ("author", _line_value(author)),
        ("timestamp", _line_value(timestamp)),
        ("content_sha256", _hex64(content_sha256_hex)),
        ("origin_id", _origin_id(origin_id)),
        ("curated_field", slot),
        ("curated_value", _line_value(curated_value)),
        ("curator", _line_value(curator)),
        ("curated_at_unix_ms", _unix_ms(curated_at_unix_ms)),
    ])


def bundle_canonical(
    mind: str,
    steward_node: str,
    exported_at_unix_ms: int,
    current_key_id: str,
    entry_signatures: list[str],
    retraction_signatures: list[str],
    curation_signatures: list[str] | None = None,
) -> bytes:
    entries_sha = hashlib.sha256("".join(_hex128(s) for s in entry_signatures).encode("ascii")).hexdigest()
    retracts_sha = hashlib.sha256("".join(_hex128(s) for s in retraction_signatures).encode("ascii")).hexdigest()
    curations_sha = hashlib.sha256(
        "".join(_hex128(s) for s in (curation_signatures or [])).encode("ascii")
    ).hexdigest()
    return _lines(MAGIC_BUNDLE, [
        ("mind", _line_value(mind)),
        ("steward_node", _line_value(steward_node)),
        ("exported_at_unix_ms", _unix_ms(exported_at_unix_ms)),
        ("current_key_id", _hex64(current_key_id)),
        ("entries_sha256", entries_sha),
        ("retractions_sha256", retracts_sha),
        ("curations_sha256", curations_sha),
    ])
