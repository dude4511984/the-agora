"""Sign and verify entries / retractions. Does not talk to the vault."""

from __future__ import annotations

from .canonical import (
    content_sha256,
    curate_canonical,
    curate_unsigned_canonical,
    entry_canonical,
    retract_canonical,
)
from .keys import KeyRecord, load_public


def sign_entry(key: KeyRecord, entry: dict) -> dict:
    payload = {
        "author": entry.get("author") or key.author,
        "timestamp": entry.get("timestamp") or "",
        "layer": entry.get("layer") or "",
        "source": entry.get("source") or "",
        "domain": entry.get("domain") or "",
        "tags": entry.get("tags") or "",
        "content": entry.get("content") or "",
    }
    # Curation lives unsigned on the object so callers can still carry current
    # live values. They are not in the signed bytes.
    if "visibility" in entry:
        payload["visibility"] = entry.get("visibility") or ""
    if "tier" in entry:
        payload["tier"] = entry.get("tier") or ""
    if payload["author"] != key.author:
        raise ValueError("entry author does not match key author")
    digest = content_sha256(payload["content"])
    payload["content_sha256"] = digest
    payload["key_id"] = key.key_id
    payload["signature"] = key.sign(entry_canonical(payload))
    return payload


def verify_entry(entry: dict) -> None:
    """Raise InvalidSignature / ValueError if the entry is not sound."""
    digest = content_sha256(entry.get("content") or "")
    if (entry.get("content_sha256") or "").lower() != digest:
        raise ValueError("content_sha256 does not match content")
    pub = load_public(entry["key_id"])
    pub.verify(bytes.fromhex(entry["signature"]), entry_canonical(entry))


def sign_retract(key: KeyRecord, entry_signature: str, retracted_at_unix_ms: int) -> dict:
    payload = {
        "entry_signature": entry_signature.lower(),
        "retracted_at_unix_ms": retracted_at_unix_ms,
        "key_id": key.key_id,
    }
    payload["signature"] = key.sign(
        retract_canonical(payload["entry_signature"], retracted_at_unix_ms)
    )
    return payload


def verify_retract(obj: dict) -> None:
    pub = load_public(obj["key_id"])
    pub.verify(
        bytes.fromhex(obj["signature"]),
        retract_canonical(obj["entry_signature"], int(obj["retracted_at_unix_ms"])),
    )


def sign_curate(
    key: KeyRecord,
    entry_signature: str,
    curated_field: str,
    curated_value: str,
    curated_at_unix_ms: int,
    curator: str | None = None,
) -> dict:
    who = curator if curator is not None else key.author
    payload = {
        "entry_signature": entry_signature.lower(),
        "curated_field": curated_field,
        "curated_value": curated_value,
        "curator": who,
        "curated_at_unix_ms": curated_at_unix_ms,
        "key_id": key.key_id,
    }
    payload["signature"] = key.sign(
        curate_canonical(
            payload["entry_signature"],
            payload["curated_field"],
            payload["curated_value"],
            payload["curator"],
            curated_at_unix_ms,
        )
    )
    return payload


def verify_curate(obj: dict) -> None:
    pub = load_public(obj["key_id"])
    sig = obj.get("entry_signature") or ""
    if sig:
        pub.verify(
            bytes.fromhex(obj["signature"]),
            curate_canonical(
                sig,
                obj["curated_field"],
                obj["curated_value"],
                obj["curator"],
                int(obj["curated_at_unix_ms"]),
            ),
        )
        return
    pub.verify(
        bytes.fromhex(obj["signature"]),
        curate_unsigned_canonical(
            obj["author"],
            obj["timestamp"],
            obj["content_sha256"],
            int(obj["origin_id"]),
            obj["curated_field"],
            obj["curated_value"],
            obj["curator"],
            int(obj["curated_at_unix_ms"]),
        ),
    )


def sign_curate_unsigned(
    key: KeyRecord,
    *,
    author: str,
    timestamp: str,
    content_sha256_hex: str,
    origin_id: int,
    curated_field: str,
    curated_value: str,
    curated_at_unix_ms: int,
    curator: str | None = None,
) -> dict:
    who = curator if curator is not None else key.author
    payload = {
        "author": author,
        "timestamp": timestamp,
        "content_sha256": content_sha256_hex.lower(),
        "origin_id": origin_id,
        "curated_field": curated_field,
        "curated_value": curated_value,
        "curator": who,
        "curated_at_unix_ms": curated_at_unix_ms,
        "key_id": key.key_id,
    }
    payload["signature"] = key.sign(
        curate_unsigned_canonical(
            payload["author"],
            payload["timestamp"],
            payload["content_sha256"],
            origin_id,
            payload["curated_field"],
            payload["curated_value"],
            payload["curator"],
            curated_at_unix_ms,
        )
    )
    return payload
