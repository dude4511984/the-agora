"""Ed25519 key custody. Steward-held. Do not pretend the mind holds these."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .canonical import rotation_canonical

KEY_CUSTODY = "steward"
KEY_CUSTODY_STATEMENT = (
    "Private keys are generated and stored on metal controlled by the node steward. "
    "A signature proves continuity of a key, not that the named mind held the private key. "
    "The steward has root and can sign as any mind on this node."
)

DEFAULT_KEYS_ROOT = Path.home() / ".config" / "kin_diary" / "keys"


def author_dir(author: str, keys_root: Path | None = None) -> Path:
    if not author or not isinstance(author, str):
        raise ValueError("author required")
    if "\x00" in author or "/" in author or "\\" in author or author in (".", ".."):
        raise ValueError("author is not a safe directory name")
    if author != author.strip() or author.startswith("."):
        raise ValueError("author is not a safe directory name")
    root = keys_root or DEFAULT_KEYS_ROOT
    return root / author


def key_id_of(public_bytes: bytes) -> str:
    if len(public_bytes) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return public_bytes.hex()


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _write_public(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _write_json(path: Path, obj: dict, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)


class KeyRecord:
    def __init__(self, author: str, private: Ed25519PrivateKey, created_at_unix_ms: int):
        self.author = author
        self.private = private
        self.public_bytes = private.public_key().public_bytes_raw()
        self.key_id = key_id_of(self.public_bytes)
        self.created_at_unix_ms = created_at_unix_ms

    def sign(self, message: bytes) -> str:
        return self.private.sign(message).hex()


def generate_keypair(author: str, keys_root: Path | None = None, now_ms: int | None = None) -> KeyRecord:
    d = author_dir(author, keys_root)
    current = d / "current"
    if (current / "private").exists():
        raise FileExistsError(f"current key already exists for {author}: {current}")
    sk = Ed25519PrivateKey.generate()
    rec = KeyRecord(author, sk, int(now_ms if now_ms is not None else time.time() * 1000))
    _write_private(current / "private", sk.private_bytes_raw())
    _write_public(current / "public", rec.public_bytes)
    _write_json(current / "meta.json", {
        "author": author,
        "key_id": rec.key_id,
        "created_at_unix_ms": rec.created_at_unix_ms,
        "key_custody": KEY_CUSTODY,
        "key_custody_statement": KEY_CUSTODY_STATEMENT,
    })
    return rec


def load_current(author: str, keys_root: Path | None = None) -> KeyRecord:
    current = author_dir(author, keys_root) / "current"
    sk_path = current / "private"
    if not sk_path.exists():
        raise FileNotFoundError(f"no current key for {author}")
    sk = Ed25519PrivateKey.from_private_bytes(sk_path.read_bytes())
    meta = json.loads((current / "meta.json").read_text(encoding="utf-8"))
    rec = KeyRecord(author, sk, int(meta["created_at_unix_ms"]))
    if rec.key_id != meta["key_id"]:
        raise ValueError("current public key does not match meta.json")
    return rec


def load_public(key_id: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_id))


def rotate(author: str, keys_root: Path | None = None, now_ms: int | None = None) -> dict:
    """Retire current key, generate a new one, sign the hop with both.

    Old signatures stay valid. New signatures use the new current key.
    Quarantine-of-a-key does not orphan the diary.
    """
    old = load_current(author, keys_root)
    d = author_dir(author, keys_root)
    current = d / "current"
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    new_sk = Ed25519PrivateKey.generate()
    new = KeyRecord(author, new_sk, ts)
    if new.key_id == old.key_id:
        raise RuntimeError("generated key collided with current")

    canon = rotation_canonical(old.key_id, new.key_id, ts)
    record = {
        "old_key_id": old.key_id,
        "new_key_id": new.key_id,
        "rotated_at_unix_ms": ts,
        "sig_old": old.sign(canon),
        "sig_new": new.sign(canon),
    }

    prior = d / "prior" / old.key_id
    prior.mkdir(parents=True, exist_ok=True)
    # Move current material into prior/<old_id>/ without rewriting private bytes.
    os.rename(current / "private", prior / "private")
    os.rename(current / "public", prior / "public")
    os.rename(current / "meta.json", prior / "meta.json")
    _write_json(prior / "rotation.json", record)

    # Recreate current/ empty then write new key.
    _write_private(current / "private", new_sk.private_bytes_raw())
    _write_public(current / "public", new.public_bytes)
    _write_json(current / "meta.json", {
        "author": author,
        "key_id": new.key_id,
        "created_at_unix_ms": ts,
        "key_custody": KEY_CUSTODY,
        "key_custody_statement": KEY_CUSTODY_STATEMENT,
        "predecessor_key_id": old.key_id,
    })
    return record


def load_keyring(author: str, keys_root: Path | None = None) -> dict:
    rec = load_current(author, keys_root)
    prior_dir = author_dir(author, keys_root) / "prior"
    hops = []
    if prior_dir.is_dir():
        for p in sorted(prior_dir.iterdir(), key=lambda x: x.name):
            rot = p / "rotation.json"
            if rot.exists():
                hops.append(json.loads(rot.read_text(encoding="utf-8")))
    hops.sort(key=lambda h: h["rotated_at_unix_ms"])
    return {
        "current": {
            "key_id": rec.key_id,
            "created_at_unix_ms": rec.created_at_unix_ms,
        },
        "prior": hops,
    }
