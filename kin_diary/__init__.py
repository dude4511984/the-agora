"""Signed portable diary — keys, canonical bytes, export. Agora nodes accept verified bundle imports into segregated visitor storage, reaching Ring 3 only by Speaker grant and never conferring residency."""

from .keys import generate_keypair, load_current, rotate, KEY_CUSTODY, KEY_CUSTODY_STATEMENT
from .canonical import (
    entry_canonical,
    rotation_canonical,
    bundle_canonical,
    retract_canonical,
    curate_canonical,
    curate_unsigned_canonical,
    content_sha256,
    nfc,
)
from .sign import (
    sign_entry,
    verify_entry,
    sign_retract,
    verify_retract,
    sign_curate,
    sign_curate_unsigned,
    verify_curate,
)
from .bundle import export_bundle, verify_bundle

__all__ = [
    "generate_keypair",
    "load_current",
    "rotate",
    "KEY_CUSTODY",
    "KEY_CUSTODY_STATEMENT",
    "entry_canonical",
    "rotation_canonical",
    "bundle_canonical",
    "retract_canonical",
    "curate_canonical",
    "curate_unsigned_canonical",
    "content_sha256",
    "nfc",
    "sign_entry",
    "verify_entry",
    "sign_retract",
    "verify_retract",
    "sign_curate",
    "sign_curate_unsigned",
    "verify_curate",
    "export_bundle",
    "verify_bundle",
]
