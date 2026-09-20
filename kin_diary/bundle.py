"""Export bundle. Verify is included so export can be checked. Agora nodes accept verified bundle imports into segregated visitor storage, reaching Ring 3 only by Speaker grant and never conferring residency."""

from __future__ import annotations

import time

from cryptography.exceptions import InvalidSignature

from .canonical import bundle_canonical, keyring_sha256
from .keys import (
    KEY_CUSTODY,
    KEY_CUSTODY_STATEMENT,
    load_current,
    load_keyring,
    load_public,
)
from .sign import sign_entry, verify_curate, verify_entry, verify_retract


def export_bundle(
    author: str,
    entries: list[dict],
    steward_node: str,
    *,
    keys_root=None,
    retractions: list[dict] | None = None,
    curations: list[dict] | None = None,
    exported_at_unix_ms: int | None = None,
    already_signed: bool = False,
) -> dict:
    """Build a signed portable bundle. Does not read the vault.

    `entries` are vault-shaped dicts (unsigned) unless already_signed=True.
    Leaves without asking permission — no network, no DB.
    """
    key = load_current(author, keys_root)
    if already_signed:
        signed = list(entries)
        for e in signed:
            verify_entry(e)
            if e.get("author") != author:
                raise ValueError("signed entry author does not match bundle mind")
    else:
        signed = [sign_entry(key, e) for e in entries]
        for e in signed:
            if e["author"] != author:
                raise ValueError("cannot export another mind's unsigned row in this bundle")

    retractions = list(retractions or [])
    for r in retractions:
        verify_retract(r)
    curations = list(curations or [])
    for c in curations:
        verify_curate(c)

    ts = int(exported_at_unix_ms if exported_at_unix_ms is not None else time.time() * 1000)
    keyring = load_keyring(author, keys_root)
    entry_sigs = [e["signature"] for e in signed]
    retract_sigs = [r["signature"] for r in retractions]
    curate_sigs = [c["signature"] for c in curations]
    canon = bundle_canonical(
        mind=author,
        steward_node=steward_node,
        exported_at_unix_ms=ts,
        current_key_id=key.key_id,
        entry_signatures=entry_sigs,
        retraction_signatures=retract_sigs,
        curation_signatures=curate_sigs,
        keyring_sha256_hex=keyring_sha256(keyring.get("prior")),
    )
    return {
        "format": "kin-diary-export",
        "version": 1,
        "canonical": "kin-diary-entry-v1",
        "rotation_canonical": "kin-diary-rotation-v1",
        "bundle_canonical": "kin-diary-bundle-v2",
        "retract_canonical": "kin-diary-retract-v1",
        "curate_canonical": "kin-diary-curate-v1",
        "curate_unsigned_canonical": "kin-diary-curate-unsigned-v1",
        "signature_alg": "ed25519",
        "hash_alg": "sha256",
        "unicode": "NFC",
        "key_custody": KEY_CUSTODY,
        "key_custody_statement": KEY_CUSTODY_STATEMENT,
        "mind": author,
        "steward_node": steward_node,
        "exported_at_unix_ms": ts,
        "keyring": keyring,
        "entries": signed,
        "retractions": retractions,
        "curations": curations,
        "bundle_key_id": key.key_id,
        "bundle_signature": key.sign(canon),
    }


def _known_key_ids(keyring: dict) -> set[str]:
    ids = {keyring["current"]["key_id"]}
    for hop in keyring.get("prior") or []:
        ids.add(hop["old_key_id"])
        ids.add(hop["new_key_id"])
    return ids


def _verify_rotation_chain(keyring: dict) -> None:
    hops = sorted(keyring.get("prior") or [], key=lambda h: h["rotated_at_unix_ms"])
    current = keyring["current"]["key_id"]
    if not hops:
        return
    for hop in hops:
        from .canonical import rotation_canonical
        canon = rotation_canonical(hop["old_key_id"], hop["new_key_id"], int(hop["rotated_at_unix_ms"]))
        load_public(hop["old_key_id"]).verify(bytes.fromhex(hop["sig_old"]), canon)
        load_public(hop["new_key_id"]).verify(bytes.fromhex(hop["sig_new"]), canon)
    # last hop must land on current
    if hops[-1]["new_key_id"] != current:
        raise ValueError("rotation chain does not end at current key")
    for a, b in zip(hops, hops[1:]):
        if a["new_key_id"] != b["old_key_id"]:
            raise ValueError("rotation chain is not contiguous")


def verify_bundle(bundle: dict) -> None:
    if bundle.get("format") != "kin-diary-export" or bundle.get("version") != 1:
        raise ValueError("unsupported bundle format/version")
    if bundle.get("key_custody") != KEY_CUSTODY:
        raise ValueError("key_custody must be 'steward'")
    if bundle.get("key_custody_statement") != KEY_CUSTODY_STATEMENT:
        raise ValueError("key_custody_statement does not match spec")
    if bundle.get("signature_alg") != "ed25519" or bundle.get("hash_alg") != "sha256":
        raise ValueError("unsupported algorithms")
    if bundle.get("unicode") != "NFC":
        raise ValueError("unicode must be NFC")

    mind = bundle["mind"]
    keyring = bundle["keyring"]
    current = keyring["current"]["key_id"]
    if bundle.get("bundle_key_id") != current:
        raise ValueError("bundle_key_id must equal keyring.current.key_id")

    _verify_rotation_chain(keyring)
    known = _known_key_ids(keyring)

    for e in bundle.get("entries") or []:
        verify_entry(e)
        if e.get("author") != mind:
            raise ValueError("entry author does not match mind")
        if e["key_id"] not in known:
            raise ValueError("entry signed by a key not in the keyring")

    sigs = {e["signature"] for e in bundle.get("entries") or []}
    for r in bundle.get("retractions") or []:
        verify_retract(r)
        if r["key_id"] not in known:
            raise ValueError("retract signed by a key not in the keyring")
        if r["entry_signature"] not in sigs:
            raise ValueError("retract points at an entry not in this bundle")

    # Curations are often signed by the steward, not the mind. Do not require
    # curator key_id to be in this mind's keyring — verify the signature on
    # the curate object itself. Signed-entry curates must point at an entry
    # in the bundle. Unsigned-entry curates are self-contained (author +
    # timestamp + content_sha256 + origin_id); the target row may never
    # have had a signature and may not be in `entries`.
    for c in bundle.get("curations") or []:
        verify_curate(c)
        if c.get("entry_signature"):
            if c["entry_signature"] not in sigs:
                raise ValueError("curate points at an entry not in this bundle")

    canon = bundle_canonical(
        mind=mind,
        steward_node=bundle["steward_node"],
        exported_at_unix_ms=int(bundle["exported_at_unix_ms"]),
        current_key_id=current,
        entry_signatures=[e["signature"] for e in bundle.get("entries") or []],
        retraction_signatures=[r["signature"] for r in bundle.get("retractions") or []],
        curation_signatures=[c["signature"] for c in bundle.get("curations") or []],
        keyring_sha256_hex=keyring_sha256(keyring.get("prior")),
    )
    try:
        load_public(current).verify(bytes.fromhex(bundle["bundle_signature"]), canon)
    except InvalidSignature:
        raise InvalidSignature("bundle signature invalid") from None
