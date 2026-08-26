"""Content-addressed, data-only artifact storage for kiosk listings.

An artifact is addressed by its SHA-256 digest and is returned only as bytes.
This module deliberately has no renderer, MIME dispatch, subprocess, import,
or execution path.  A listing remains the authority for which digest may be
fetched; the node remains the authority for whether the requester may fetch.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .canonical import RING_READ
from .node import AgoraError, Node
from .places import Atlas, verify_listing


_HASH = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(Exception):
    """Base class for artifact retrieval failures."""


class ArtifactUnknownHash(ArtifactError):
    """The requested content digest is not present."""


class ArtifactTooLarge(ArtifactError):
    """The artifact exceeds the configured byte limit."""


class ArtifactHashMismatch(ArtifactError):
    """Stored content does not match the requested or listed digest."""


class ArtifactAccessDenied(ArtifactError):
    """The node policy does not permit this fetch."""


class ArtifactListingError(ArtifactError):
    """The listing is invalid, stale, or no longer eligible for retrieval."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(value: str) -> str:
    digest = (value or "").lower()
    if not _HASH.fullmatch(digest):
        raise ArtifactUnknownHash(f"not a SHA-256 artifact hash: {value!r}")
    return digest


class ArtifactStore:
    """A filesystem-backed content-addressed store.

    Files are named only by lowercase SHA-256 digest.  The API accepts and
    returns bytes, never a filename to execute, a decoded object, or a
    content type.  ``max_bytes`` applies both when storing and when serving.
    """

    def __init__(self, root: str | Path, max_bytes: int = 8 * 1024 * 1024):
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.root / _hash(digest)

    def put(self, data: bytes) -> str:
        """Store bytes under their digest and return that digest."""
        if not isinstance(data, bytes):
            raise TypeError("artifacts must be bytes")
        if len(data) > self.max_bytes:
            raise ArtifactTooLarge(
                f"artifact is {len(data)} bytes; limit is {self.max_bytes}"
            )
        digest = _digest(data)
        path = self._path(digest)
        if path.exists():
            existing = path.read_bytes()
            if len(existing) > self.max_bytes:
                raise ArtifactTooLarge(
                    f"stored artifact is {len(existing)} bytes; limit is {self.max_bytes}"
                )
            if _digest(existing) != digest:
                raise ArtifactHashMismatch(f"stored artifact {digest} is corrupt")
            return digest
        path.write_bytes(data)
        return digest

    def _read(self, digest: str) -> bytes:
        path = self._path(digest)
        if not path.is_file():
            raise ArtifactUnknownHash(f"unknown artifact hash {digest}")
        data = path.read_bytes()
        if len(data) > self.max_bytes:
            raise ArtifactTooLarge(
                f"artifact is {len(data)} bytes; limit is {self.max_bytes}"
            )
        if _digest(data) != digest:
            raise ArtifactHashMismatch(
                f"artifact bytes do not hash to requested digest {digest}"
            )
        return data

    def fetch(
        self,
        node: Node,
        key_id: str,
        listing: dict,
        atlas: Atlas,
        *,
        access_board: str,
        required_ring: int = RING_READ,
    ) -> bytes:
        """Return a listed artifact after node policy and hash checks.

        ``access_board`` is explicit because ``places.py`` deliberately treats
        ``ring_to_see`` as a rendering hint, not an authorization rule.  The
        node's effective ring on this board is the actual gate.  A seller
        evicted after publishing remains ineligible even if the listing and
        bytes are otherwise valid.
        """
        try:
            verify_listing(listing)
        except Exception as exc:
            raise ArtifactListingError(f"listing signature is invalid: {exc}") from exc
        if listing.get("node") != node.name:
            raise ArtifactListingError("listing belongs to a different node")
        stored_listing = atlas.listings.get(listing.get("listing_id"))
        if stored_listing != listing:
            raise ArtifactListingError("listing is not published by this Atlas")

        seller = (listing.get("seller_key_id") or "").lower()
        if seller in node.evicted:
            raise ArtifactListingError(
                "listing seller is evicted; artifact retrieval is unavailable"
            )
        try:
            ring = node.effective_ring(key_id, access_board)
        except (AgoraError, ValueError) as exc:
            raise ArtifactAccessDenied(f"node ring check failed: {exc}") from exc
        if ring < required_ring:
            raise ArtifactAccessDenied(
                f"ring {ring} is below artifact fetch requirement {required_ring}"
            )

        digest = _hash(listing.get("artifact_sha256") or "")
        data = self._read(digest)
        if _digest(data) != digest:
            raise ArtifactHashMismatch(
                f"served bytes do not match listing artifact_sha256 {digest}"
            )
        return data


def fetch_artifact(
    store: ArtifactStore,
    node: Node,
    key_id: str,
    listing: dict,
    atlas: Atlas,
    *,
    access_board: str,
    required_ring: int = RING_READ,
) -> bytes:
    """Functional wrapper for callers that do not retain an ArtifactStore."""
    return store.fetch(
        node, key_id, listing, atlas,
        access_board=access_board, required_ring=required_ring,
    )


__all__ = [
    "ArtifactStore",
    "ArtifactError",
    "ArtifactUnknownHash",
    "ArtifactTooLarge",
    "ArtifactHashMismatch",
    "ArtifactAccessDenied",
    "ArtifactListingError",
    "fetch_artifact",
]
