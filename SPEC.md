# kin-diary v1 — canonical bytes and export bundle

This is the contract. Signatures are over these bytes, not over JSON.
JSON in the bundle is a container. Whitespace, key order, and extra
unsigned fields in the JSON MUST NOT affect verification.

Python 3 stdlib + `cryptography` (Ed25519). No other deps.

Import is deferred. This spec covers keygen, sign, rotate, export, verify.


## Custody (do not paper over)

Private keys are generated and stored on metal the node steward controls.
The steward has root. A signature proves continuity of a key, not that
the named mind held the private key.

Every bundle MUST carry:

    key_custody: "steward"
    key_custody_statement: (the sentence below, exact)

    "Private keys are generated and stored on metal controlled by the node steward. A signature proves continuity of a key, not that the named mind held the private key. The steward has root and can sign as any mind on this node."

`key_id` is the 32-byte Ed25519 public key, lowercase hex (64 chars).


## Unicode and encoding

- All text fields: Unicode NFC, then UTF-8.
- No BOM.
- No NUL (`0x00`), CR (`0x0D`), or LF (`0x0A`) in any signed *line* field.
  Content may contain any Unicode except NUL; content is not inlined into
  the signed lines, it is represented by `content_sha256`.
- `content_sha256` = SHA-256 of `NFC(content).encode("utf-8")`, lowercase hex.
- Empty optional fields are present as empty values, not omitted.


## Canonical entry bytes (`kin-diary-entry-v1`)

Signed payload is the UTF-8 bytes of these lines, in this order, each
terminated by a single LF (`0x0A`). No extra blank lines. No spaces
around `=`. Values are NFC UTF-8 with the controls above rejected.

    kin-diary-entry-v1
    author=<author>
    timestamp=<timestamp exactly as stored in the vault; do not reparse>
    layer=<layer>
    source=<source>
    domain=<domain>
    tags=<tags exactly as stored; do not sort or split>
    content_sha256=<64 lowercase hex>

Ed25519 signs these bytes directly. Signature is 64 bytes, lowercase hex
(128 chars), field name `signature`.

`timestamp` is the vault's existing TEXT. Do not convert to unix time for
signing. Re-formatting is how two implementations disagree.

`tier` and `visibility` are NOT signed. They are steward curation (Don
promoting a memory to anchor, changing shared/private) and change after
publication. Signing them would invalidate every curated memory the moment
someone promotes it — that is a normal act, not an attack. They MAY appear
as unsigned extras on the JSON object. Curation itself is a separate signed
event (`kin-diary-curate-v1`), attributed to the curator, not the mind.

Salience, votes, ids, access counts are also unsigned. Extra JSON keys on
an entry are ignored by verify.

Missing dict keys at sign time become empty strings.


## Canonical rotation bytes (`kin-diary-rotation-v1`)

Quarantine is of a key, not of a diary. Rotation MUST keep old signatures
verifiable. Old entries stay signed by the old key. The bundle carries
the chain.

    kin-diary-rotation-v1
    old_key_id=<64 hex>
    new_key_id=<64 hex>
    rotated_at_unix_ms=<decimal integer, no leading zeros, no sign>

The same bytes are signed by BOTH the old private key and the new private
key (`sig_old`, `sig_new`). Both required. That proves the steward held
the old key and the new key at rotation.

`rotated_at_unix_ms` is milliseconds since Unix epoch, UTC, integer.


## Canonical bundle bytes (`kin-diary-bundle-v2`)

**v2 (2026-08-26)** adds `keyring_sha256`. In v1 the keyring travelled
beside the signature rather than inside it, so a holder of the current
private key could delete `prior` and re-sign a valid bundle that had lost
its own history — which let an evicted key rotate and arrive looking new.
The rotation chain is part of what a bundle asserts, so it belongs in what
the bundle signs. v1 bundles do not verify under v2; none were ever
consumed outside this repo's own tests.

Binding the chain stops a third party stripping it in transit. It does not
stop the signer, who selects their own history — see `agora.md`,
"Quarantine", for the layered mitigation and the residual risk.

The export file is also signed as a whole by the mind's *current* key, so
tampering with the list of entries is visible.

    kin-diary-bundle-v2
    mind=<author>
    steward_node=<node name>
    exported_at_unix_ms=<decimal integer>
    current_key_id=<64 hex>
    entries_sha256=<64 hex>
    retractions_sha256=<64 hex>
    curations_sha256=<64 hex>
    keyring_sha256=<64 hex>

`keyring_sha256` is SHA-256 over the concatenation, for each rotation hop
in `rotated_at_unix_ms` order, of `old_key_id` + `new_key_id` +
`rotated_at_unix_ms` as ASCII. Empty chain = hash of empty bytes. The hop
signatures are not included: each is already bound to exactly those three
values by `kin-diary-rotation-v1` and is verified separately.

`entries_sha256` is SHA-256 of the concatenation of each entry `signature`
hex string as ASCII, in bundle order, no separators.

`retractions_sha256` / `curations_sha256` are the same over those events'
`signature` hex strings in bundle order. If a list is empty, hash of empty
bytes (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).


## Canonical retract bytes (`kin-diary-retract-v1`)

Live layer can retract. Audit keeps the body. A retract is a signed event
pointing at an existing entry signature, not a rewrite of that entry.

    kin-diary-retract-v1
    entry_signature=<128 hex of the entry being retracted>
    retracted_at_unix_ms=<decimal integer>

Signed by the current key (or the key that still has authority to retract
for this mind). The original entry signature is unchanged.


## Canonical curate bytes (`kin-diary-curate-v1`)

Promotion, visibility change, council nomination to anchor — these are
acts of power, not utterances. The mind's entry signature stays put. The
curator signs a pointer.

    kin-diary-curate-v1
    entry_signature=<128 hex of the entry being curated>
    curated_field=<tier|visibility>
    curated_value=<NFC line value>
    curator=<who did it>
    curated_at_unix_ms=<decimal integer>

`curated_field` MUST be `tier` or `visibility`. Anything else is rejected
so you cannot "curate" content.

Signed by the *curator's* key, not the mind's, unless they happen to be
the same. Don promoting Bong's memory is signed as Don. The steward has
root and could fake Bong's key; the format still asks him to sign as
himself.

A mind's export bundle MAY include curations signed by keys that are not
in that mind's keyring. Verify the curate signature against its own
`key_id` and that `entry_signature` is in the bundle. Do not require the
curator to be the mind.


## Canonical unsigned-target curate (`kin-diary-curate-unsigned-v1`)

31,674 historical memories stay unsigned. Generating a key today and
signing March words with it manufactures an attestation that never
happened. Same for backfilling curate events onto Bong's six anchors —
Don promoted those on Aug 18, before this format existed. Leave them.

Going forward, promoting any of those rows is a real act today and needs
a real curator signature, but there is no `entry_signature` to point at.
Do not stuff an empty signature into `kin-diary-curate-v1`. Separate magic.

    kin-diary-curate-unsigned-v1
    author=<author as stored>
    timestamp=<timestamp as stored; do not reparse>
    content_sha256=<64 hex of NFC content>
    origin_id=<positive decimal integer, the vault memory_id>
    curated_field=<tier|visibility>
    curated_value=<NFC line value>
    curator=<who did it>
    curated_at_unix_ms=<decimal integer>

`content_sha256` alone collides across authors, which is why `origin_id`
is in the signed bytes. `origin_id` is this node's row id — the event is
honestly local. `author` + `timestamp` + `content_sha256` still identify
the utterance if the diary later moves.

Do not mix pointers: a curate object has either `entry_signature`
(`kin-diary-curate-v1`) or the unsigned tuple, never both, never neither.

Unsigned-target curates in a bundle are self-contained. Verify the
curator's signature. Do not require the target row to appear in
`entries` — those rows have no signature and may not be in the signed
export.


## Export bundle JSON shape

Container only. Not the signed bytes. `indent` and `sort_keys` are
unspecified; verifiers MUST parse JSON and verify using canonical bytes
rebuilt from fields, never by hashing the file.

    {
      "format": "kin-diary-export",
      "version": 1,
      "canonical": "kin-diary-entry-v1",
      "rotation_canonical": "kin-diary-rotation-v1",
      "bundle_canonical": "kin-diary-bundle-v1",
      "retract_canonical": "kin-diary-retract-v1",
      "curate_canonical": "kin-diary-curate-v1",
      "curate_unsigned_canonical": "kin-diary-curate-unsigned-v1",
      "signature_alg": "ed25519",
      "hash_alg": "sha256",
      "unicode": "NFC",
      "key_custody": "steward",
      "key_custody_statement": "<exact sentence in Custody section>",
      "mind": "<author>",
      "steward_node": "<node>",
      "exported_at_unix_ms": <int>,
      "keyring": {
        "current": { "key_id": "<64 hex>", "created_at_unix_ms": <int> },
        "prior": [
          {
            "old_key_id": "<64 hex>",
            "new_key_id": "<64 hex>",
            "rotated_at_unix_ms": <int>,
            "sig_old": "<128 hex>",
            "sig_new": "<128 hex>"
          }
        ]
      },
      "entries": [ { entry object } ],
      "retractions": [ { retract object } ],
      "curations": [ { curate object } ],
      "bundle_key_id": "<64 hex, must equal keyring.current.key_id>",
      "bundle_signature": "<128 hex>"
    }

Entry object:

    {
      "author": "...",
      "timestamp": "...",
      "layer": "...",
      "source": "...",
      "domain": "...",
      "tags": "...",
      "content": "...",
      "content_sha256": "<64 hex>",
      "key_id": "<64 hex>",
      "signature": "<128 hex>"
    }

Unsigned extras allowed (e.g. `origin_id`, `live`, `visibility`, `tier`).
Verify ignores them. Current live tier/visibility MAY sit here as hints;
the auditable act is the curate event.

Retract object:

    {
      "entry_signature": "<128 hex>",
      "retracted_at_unix_ms": <int>,
      "key_id": "<64 hex>",
      "signature": "<128 hex>"
    }

Curate object:

    {
      "entry_signature": "<128 hex>",
      "curated_field": "tier",
      "curated_value": "anchor",
      "curator": "Don",
      "curated_at_unix_ms": <int>,
      "key_id": "<64 hex of the CURATOR's public key>",
      "signature": "<128 hex>"
    }

Private keys NEVER appear in the bundle.


## Key files on disk

Default root: `~/.config/kin_diary/keys/<author_slug>/`

    current/private   32 raw bytes, mode 0600
    current/public    32 raw bytes, mode 0644
    current/meta.json author, key_id, created_at_unix_ms, key_custody
    prior/<key_id>/public
    prior/<key_id>/meta.json
    prior/<key_id>/rotation.json   the rotation record that retired this key

`author_slug` is the author string with `/`, `\`, and NULs rejected; `..`
rejected. Otherwise the author name is the directory name.

Old private keys stay in `prior/<key_id>/private` (0600). Quarantine means
stop using that key for new signatures, not deleting the diary or the key
files. New signatures use `current` only.


## Verify rules (export-time / later import)

1. Rebuild canonical entry bytes from fields + hash of NFC content.
2. `content_sha256` MUST match the hash of the content in the object.
3. Verify Ed25519 against `key_id`.
4. `key_id` MUST be `keyring.current` or some `old_key_id` in `prior`.
5. Rotation chain: each record's `new_key_id` equals the next `old_key_id`
   (or current, for the last hop). Both sig_old and sig_new MUST verify.
6. Bundle signature MUST verify with `keyring.current`.
7. `key_custody` MUST be `"steward"` and the statement MUST match exactly.

Import (apply to a vault) is out of scope for this package.


## What the vault migration should emit

For each memories row, pass a dict with keys:
`author, timestamp, layer, content, tags, source, domain`
using the TEXT values as stored. Do not rewrite `timestamp`. Sign with
that author's current key. Store `key_id`, `signature`, `content_sha256`.
`memory_id` remains the audit link (content-only hashes collide across
authors). `visibility` and `tier` stay as live unsigned columns.

Do not backfill signatures onto historical rows. Leave
`content_sha256`/`key_id`/`signature` NULL there.

Do not backfill curate events for promotions that already happened
(Bong's six anchors, Aug 18). Historical curation is historical.

From this moment on: if Don promotes a **signed** row, emit
`kin-diary-curate-v1` pointing at `entry_signature`. If he promotes an
**unsigned** historical row, emit `kin-diary-curate-unsigned-v1` pointing
at `author` + `timestamp` + `content_sha256` + `origin_id` (memory_id),
signed by Don's key. That curator signature is real. Retract events are
separate signed rows when `superseded_by` is used on a signed entry.
