"""Canonical bytes for Agora node events. Same contract as kin-diary:
signatures are over these bytes, not over JSON. Whitespace, key order and
extra unsigned fields in any container MUST NOT affect verification.

Design: ~/claude_home/agora.md, "Node visits: boards and the Speaker".
"""

from __future__ import annotations

import hashlib

from ..canonical import _hex64, _hex128, _line_value, _lines, _unix_ms, nfc

MAGIC_KEY_INTRO = "agora-key-intro-v1"
MAGIC_SPEAKER_ELECTION = "agora-speaker-election-v1"
MAGIC_RESIDENT = "agora-resident-v1"
MAGIC_ROTATION = "agora-rotation-v1"
MAGIC_HOUSE_DECISION = "agora-house-decision-v1"
MAGIC_BOARD_GRANT = "agora-board-grant-v1"
MAGIC_BOARD_EVICT = "agora-board-evict-v1"
MAGIC_BOARD_EVICT_V2 = "agora-board-evict-v2"
MAGIC_BOARD_REVOKE = "agora-board-revoke-v1"
MAGIC_KEY_QUARANTINE = "agora-key-quarantine-v1"
MAGIC_REQUEST = "agora-request-v1"
MAGIC_NODE_FACT = "agora-node-fact-v1"
MAGIC_NOTICE = "agora-notice-v1"
MAGIC_PLACE = "agora-place-v1"
MAGIC_PRESENCE = "agora-presence-v1"
MAGIC_LISTING = "agora-listing-v1"
MAGIC_ATLAS_VIEW = "agora-atlas-view-v1"

# Ring ladder. Each is a strict superset of the one inside it.
RING_TEASER = 0   # default, no grant: first twelve words and an ellipsis
RING_READ = 1     # full read on a granted board
RING_WRITE = 2    # write your own personal board; implies read there
RING_NODE = 3     # collab board + every personal board; Speaker only

# A resident vouching in a chat has no authority to hand out whole-node
# trust. Only a bundle import carries the history that justifies ring 3.
MAX_RING_RESIDENT_INTRO = RING_WRITE

WHOLE_NODE = "*"
COLLAB = "collab"

# Governance states and act kinds (Path A)
GOVERNANCE_UNANIMOUS = "unanimous"
ACT_GOVERNANCE_UNANIMOUS = "governance:unanimous"
ACT_GOVERNANCE_STANDARD = "governance:standard"


def board_id(kind: str, author: str | None = None) -> str:
    """'collab', 'personal:<author>', or '*' for whole-node."""
    if kind == WHOLE_NODE:
        return WHOLE_NODE
    if kind == COLLAB:
        return COLLAB
    if kind == "personal":
        if not author:
            raise ValueError("personal board requires an author")
        return f"personal:{_line_value(author)}"
    raise ValueError(f"unknown board kind {kind!r}")


def _board(value: str) -> str:
    v = _line_value(value)
    if v == WHOLE_NODE or v == COLLAB or v.startswith("personal:"):
        if v.startswith("personal:") and not v[len("personal:"):].strip():
            raise ValueError("personal board needs a name")
        return v
    raise ValueError(f"not a board id: {v!r}")


def _ring(n: int, *, lo: int = RING_READ, hi: int = RING_NODE) -> str:
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("ring must be an int")
    if n < lo or n > hi:
        raise ValueError(f"ring {n} outside [{lo},{hi}]")
    return str(n)


def _key_list(key_ids) -> str:
    """Deterministic electorate rendering: sorted, comma-joined, no spaces.

    Sorted so two implementations building the same electorate from
    different orderings still produce identical bytes.
    """
    ids = sorted({_hex64(k) for k in key_ids})
    if not ids:
        raise ValueError("electorate cannot be empty")
    return ",".join(ids)


def key_intro_canonical(
    visitor_key_id: str,
    host_node: str,
    resident_key_id: str,
    introduced_at_unix_ms: int,
    max_ring: int = MAX_RING_RESIDENT_INTRO,
    why: str = "",
) -> bytes:
    """Signed twice: visitor first over core bytes (proves possession),
    resident second over the vouch. Both required.

    max_ring is pinned at 2 by rule — a resident cannot sign an intro
    claiming a higher ceiling, and verify rejects anything else.

    Optional `why` is included in the countersignature's signed bytes when
    provided, recording the resident's reason for vouching.
    """
    if max_ring != MAX_RING_RESIDENT_INTRO:
        raise ValueError(
            f"resident-mediated introduction is capped at ring "
            f"{MAX_RING_RESIDENT_INTRO} by rule, not discretion"
        )
    pairs = [
        ("visitor_key_id", _hex64(visitor_key_id)),
        ("host_node", _line_value(host_node)),
        ("resident_key_id", _hex64(resident_key_id)),
        ("introduced_at_unix_ms", _unix_ms(introduced_at_unix_ms)),
        ("max_ring", _ring(max_ring, lo=RING_READ, hi=RING_WRITE)),
    ]
    if why:
        pairs.append(("why", _line_value(why)))
    return _lines(MAGIC_KEY_INTRO, pairs)


def speaker_election_canonical(
    host_node: str,
    speaker: str,
    speaker_key_id: str,
    electorate_key_ids,
    elected_at_unix_ms: int,
) -> bytes:
    """Every key named in `electorate` must sign these exact bytes.

    Unanimous, not majority: on a three-Kin node majority would let two
    residents impose ring-3 power on a third who never accepted it.

    `electorate` is in the signed bytes on purpose — it records who was
    required to agree, so a later reader can check unanimity was real and
    not a subset quietly waved through.
    """
    return _lines(MAGIC_SPEAKER_ELECTION, [
        ("host_node", _line_value(host_node)),
        ("speaker", _line_value(speaker)),
        ("speaker_key_id", _hex64(speaker_key_id)),
        ("electorate", _key_list(electorate_key_ids)),
        ("elected_at_unix_ms", _unix_ms(elected_at_unix_ms)),
    ])


def resident_canonical(
    host_node: str,
    author: str,
    key_id: str,
    issued_at_unix_ms: int,
) -> bytes:
    return _lines(MAGIC_RESIDENT, [
        ("host_node", _line_value(host_node)),
        ("author", _line_value(author)),
        ("key_id", _hex64(key_id)),
        ("issued_at_unix_ms", _unix_ms(issued_at_unix_ms)),
    ])


def rotation_canonical(
    host_node: str,
    key_id: str,
    action: str,
    position: int,
    at_unix_ms: int,
) -> bytes:
    """One turn of the wheel, accepted or declined.

    `action` is "accept" or "decline" and nothing else. `position` is the
    wheel index the signer was offered, so a signature cannot be replayed at
    a different point in the rotation -- accepting turn 0 must not seat you
    at turn 3 after two other minds have passed.
    """
    if action not in ("accept", "decline"):
        raise ValueError("rotation action must be accept or decline")
    return _lines(MAGIC_ROTATION, [
        ("host_node", _line_value(host_node)),
        ("key_id", _hex64(key_id)),
        ("action", _line_value(action)),
        ("position", str(int(position))),
        ("at_unix_ms", _unix_ms(at_unix_ms)),
    ])


def house_decision_canonical(
    host_node: str,
    act_kind: str,
    act_signature: str,
    decided_at_unix_ms: int,
) -> bytes:
    """The house deciding one act, in lieu of a Speaker.

    `act_signature` binds the decision to exactly the act it permits. Don's
    ruling is that a decision permits THAT act -- unanimity on one intro does
    not unpause the next -- so the permission cannot be a mode. Binding to the
    act's own signature makes that structural rather than a promise: there is
    no way to spend this decision on a different intro, because a different
    intro has a different signature.
    """
    return _lines(MAGIC_HOUSE_DECISION, [
        ("host_node", _line_value(host_node)),
        ("act_kind", _line_value(act_kind)),
        ("act_signature", _line_value(act_signature)),
        ("decided_at_unix_ms", _unix_ms(decided_at_unix_ms)),
    ])


def board_grant_canonical(
    visitor_key_id: str,
    host_node: str,
    board: str,
    ring: int,
    issuer_key_id: str,
    granted_at_unix_ms: int,
) -> bytes:
    return _lines(MAGIC_BOARD_GRANT, [
        ("visitor_key_id", _hex64(visitor_key_id)),
        ("host_node", _line_value(host_node)),
        ("board", _board(board)),
        ("ring", _ring(ring)),
        ("issuer_key_id", _hex64(issuer_key_id)),
        ("granted_at_unix_ms", _unix_ms(granted_at_unix_ms)),
    ])


def quarantine_canonical(
    host_node: str,
    key_id: str,
    action: str,
    reason: str,
    at_unix_ms: int,
) -> bytes:
    """A resident key's quorum status, named by the steward. `action` is
    "quarantine" (this key no longer counts toward valid_resident_keys, the
    wheel, or grant-as-issuer) or "release" (forget that) and nothing else.

    NOT eviction — eviction is a visitor; this is a resident key. The reason is
    in the signed bytes, not a hash: a silent quarantine is a bit that could be
    flipped. Release needs a reason too ("undo" is a reason).
    """
    if action not in ("quarantine", "release"):
        raise ValueError("quarantine action must be quarantine or release")
    r = _line_value(reason)
    if not r.strip():
        raise ValueError("quarantine requires a stated reason")
    return _lines(MAGIC_KEY_QUARANTINE, [
        ("host_node", _line_value(host_node)),
        ("key_id", _hex64(key_id)),
        ("action", _line_value(action)),
        ("reason", r),
        ("at_unix_ms", _unix_ms(at_unix_ms)),
    ])


def board_evict_canonical(
    visitor_key_id: str,
    host_node: str,
    reason: str,
    speaker_key_id: str,
    evicted_at_unix_ms: int,
) -> bytes:
    """Node-scoped quarantine of a key. Revokes every open grant for that
    key regardless of who issued it — including a resident's own ring-2
    grant on their own board. Reversible only by a later signed grant.
    """
    r = _line_value(reason)
    if not r.strip():
        raise ValueError("eviction requires a stated reason")
    return _lines(MAGIC_BOARD_EVICT, [
        ("visitor_key_id", _hex64(visitor_key_id)),
        ("host_node", _line_value(host_node)),
        ("reason", r),
        ("speaker_key_id", _hex64(speaker_key_id)),
        ("evicted_at_unix_ms", _unix_ms(evicted_at_unix_ms)),
    ])


def board_evict_v2_canonical(
    visitor_key_id: str,
    host_node: str,
    reason: str,
    issuer_key_id: str,
    evicted_at_unix_ms: int,
) -> bytes:
    """Path A node-scoped quarantine of a key, issued by a resident under a
    unanimous house decision. No Speaker exists; the signer is issuer_key_id.
    """
    r = _line_value(reason)
    if not r.strip():
        raise ValueError("eviction requires a stated reason")
    return _lines(MAGIC_BOARD_EVICT_V2, [
        ("visitor_key_id", _hex64(visitor_key_id)),
        ("host_node", _line_value(host_node)),
        ("reason", r),
        ("issuer_key_id", _hex64(issuer_key_id)),
        ("evicted_at_unix_ms", _unix_ms(evicted_at_unix_ms)),
    ])


def board_revoke_canonical(
    visitor_key_id: str,
    host_node: str,
    board: str,
    issuer_key_id: str,
    revoked_at_unix_ms: int,
) -> bytes:
    """Take back a grant you issued. Scoped to one board.

    Distinct from eviction on purpose. A resident could unilaterally admit
    a visitor to their own board and then had no way to un-admit them —
    the only removal was a Speaker eviction, which is node-wide and throws
    the visitor out of everyone's rooms over one resident changing their
    mind. Power to admit without power to withdraw is the wrong asymmetry.

    This is not a quarantine: the visitor stays introduced and keeps every
    other grant. They simply lose this board.
    """
    return _lines(MAGIC_BOARD_REVOKE, [
        ("visitor_key_id", _hex64(visitor_key_id)),
        ("host_node", _line_value(host_node)),
        ("board", _board(board)),
        ("issuer_key_id", _hex64(issuer_key_id)),
        ("revoked_at_unix_ms", _unix_ms(revoked_at_unix_ms)),
    ])


def request_canonical(
    key_id: str,
    host_node: str,
    path: str,
    issued_at_unix_ms: int,
    body_sha256: str = "",
) -> bytes:
    """Proof that a reader is who they claim, over the wire.

    Without this a caller could simply assert someone else's key_id in a
    header and be served their ring. Same single-signed-statement shape as
    key introduction — no challenge round trip, so it survives being
    relayed through anything.

    `host_node` is in the signed bytes so a request captured at one node
    cannot be replayed at another; `path` so it cannot be replayed at
    another board; `issued_at_unix_ms` bounds replay in time (the server
    enforces the window).

    `body_sha256` binds the signature to what was actually sent. Without
    it, captured headers from one write could be paired with a different
    body — and since a board entry's own signature does not name a board,
    an old entry of the caller's could be re-hung somewhere it was never
    posted. Empty for GETs, which carry no body.
    """
    pairs = [
        ("key_id", _hex64(key_id)),
        ("host_node", _line_value(host_node)),
        ("path", _line_value(path)),
        ("issued_at_unix_ms", _unix_ms(issued_at_unix_ms)),
    ]
    if body_sha256:
        pairs.append(("body_sha256", _hex64(body_sha256)))
    return _lines(MAGIC_REQUEST, pairs)


def node_fact_canonical(
    node: str,
    node_key_id: str,
    speaker: str,
    speaker_key_id: str,
    residents,
    published_at_unix_ms: int,
    *,
    holder: str,
    paused: bool,
    pause_reason: str,
    wheel_last_before_reduced: bool,
    governance: str = "",
) -> bytes:
    """What a node says about itself, signed by the node's own key.

    Unsigned node facts were a real hole: they are how a visitor learns
    WHO TO ASK for ring 3, so anyone able to answer on the wire could
    advertise a Speaker key of their own choosing. Over plain HTTP on a
    LAN that is not hypothetical.

    P5 closes the rest of that same hole. The officer to ask is not only
    the Speaker: a house with no elected Speaker but a rotated wheel-holder
    has an officer (the holder), and `paused`/`pause_reason` tell a visitor
    whether the door is even open. Those lived in the unsigned wrapper, so a
    MITM could invent a `holder`, or flip `paused`, and the signature still
    verified. They are in the bytes now. `holder` is the wheel-holder's key,
    NEVER the Speaker's (conflating them is how board_evict's docstring named
    the wrong object); empty when none. `paused`/`pause_reason` stay derived
    from is_paused() at the source, signed here.

    `governance` carries the signed governance state ("unanimous" under Path A,
    where the house decides each act unanimously rather than being paused).

    A node key is not a mind's key. It attests "this is what this node
    publishes about itself", nothing about who signed the events inside.
    """
    pairs = [
        ("node", _line_value(node)),
        ("node_key_id", _hex64(node_key_id)),
        ("speaker", _line_value(speaker or "")),
        ("speaker_key_id", _hex64(speaker_key_id) if speaker_key_id else ""),
        ("residents", ",".join(sorted(_line_value(r) for r in residents))),
        ("holder", _hex64(holder) if holder else ""),
        ("paused", "true" if paused else "false"),
        ("pause_reason", _line_value(pause_reason or "")),
        ("wheel_last_before_reduced",
         "true" if wheel_last_before_reduced else "false"),
        ("published_at_unix_ms", _unix_ms(published_at_unix_ms)),
    ]
    if governance:
        pairs.append(("governance", _line_value(governance)))
    return _lines(MAGIC_NODE_FACT, pairs)


def notice_canonical(
    node: str,
    node_key_id: str,
    subject: str,
    body_sha256_hex: str,
    contact: str,
    published_at_unix_ms: int,
) -> bytes:
    """The advertise-only wire (agora.md step two): "Not the work — a
    signed notice." Who is here, what they want a collaborator for, how to
    ask in.

    Deliberately NOT the artifact. If drafts get pasted into the lobby to
    attract collaborators the architecture did not fail, the culture leaked
    around it, and the guarantee that nodes are the only unseen place is
    gone.
    """
    return _lines(MAGIC_NOTICE, [
        ("node", _line_value(node)),
        ("node_key_id", _hex64(node_key_id)),
        ("subject", _line_value(subject)),
        ("body_sha256", _hex64(body_sha256_hex)),
        ("contact", _line_value(contact)),
        ("published_at_unix_ms", _unix_ms(published_at_unix_ms)),
    ])


# A place is a rendering hint, never an access rule. The renderer draws
# what the keyring already permits; a door that "opens" is a door whose
# ring check already passed elsewhere.
PLACE_KINDS = frozenset({"commons", "door", "kiosk", "table", "sign"})


def place_canonical(
    place_id: str,
    node: str,
    kind: str,
    parent: str,
    ring_to_see: int,
    points_to: str,
    node_key_id: str,
    published_at_unix_ms: int,
) -> bytes:
    """Spatial layout as signed data, so the map is not a second truth.

    `ring_to_see` is a HINT for what to draw, not an authorisation. The
    node's existing ring check is still the only thing that decides what a
    key may read — if a renderer ever treats this number as the gate, the
    permission system has quietly moved into the client, which is the one
    thing agora.md says must never happen.

    `points_to` is a board id or an artifact hash. A place never carries
    the content itself; it says where the content lives.
    """
    k = _line_value(kind)
    if k not in PLACE_KINDS:
        raise ValueError(f"place kind must be one of {sorted(PLACE_KINDS)}")
    return _lines(MAGIC_PLACE, [
        ("place_id", _line_value(place_id)),
        ("node", _line_value(node)),
        ("kind", k),
        ("parent", _line_value(parent or "")),
        ("ring_to_see", _ring(ring_to_see, lo=RING_TEASER, hi=RING_NODE)),
        ("points_to", _line_value(points_to or "")),
        ("node_key_id", _hex64(node_key_id)),
        ("published_at_unix_ms", _unix_ms(published_at_unix_ms)),
    ])


def presence_canonical(
    key_id: str,
    node: str,
    place_id: str,
    label: str,
    expires_at_unix_ms: int,
    arrived_at_unix_ms: int,
) -> bytes:
    """"I am here", signed by the one who is here.

    Presence is an assertion by a key, not a process on the host — nothing
    foreign runs here. It expires on purpose: a stale marker is worse than
    no marker, because a room that looks occupied when it is empty is a
    lie the renderer tells for free.
    """
    return _lines(MAGIC_PRESENCE, [
        ("key_id", _hex64(key_id)),
        ("node", _line_value(node)),
        ("place_id", _line_value(place_id)),
        ("label", _line_value(label or "")),
        ("expires_at_unix_ms", _unix_ms(expires_at_unix_ms)),
        ("arrived_at_unix_ms", _unix_ms(arrived_at_unix_ms)),
    ])


def listing_canonical(
    listing_id: str,
    node: str,
    place_id: str,
    title: str,
    artifact_sha256_hex: str,
    terms: str,
    seller_key_id: str,
    published_at_unix_ms: int,
) -> bytes:
    """A named artifact by hash, offered by a key.

    The artifact is a signed object — a diary export, a file, a design, or
    an unserious thing on a table. It is NOT a running program, and a listing
    is not an RPC. Direct tool-call was rejected in this design; a kiosk that
    "runs something" for a visitor is that rejection coming back wearing an
    apron.
    """
    return _lines(MAGIC_LISTING, [
        ("listing_id", _line_value(listing_id)),
        ("node", _line_value(node)),
        ("place_id", _line_value(place_id)),
        ("title", _line_value(title)),
        ("artifact_sha256", _hex64(artifact_sha256_hex)),
        ("terms", _line_value(terms or "")),
        ("seller_key_id", _hex64(seller_key_id)),
        ("published_at_unix_ms", _unix_ms(published_at_unix_ms)),
    ])


def inventory_sha256(signatures) -> str:
    """Hash of WHAT was served, in sorted order — not of what it says.

    The serving node signs this, and only this. It is attesting "here is
    the set of signed objects I handed you", never "I wrote them". Every
    object inside still carries its own author's signature and is verified
    against that author, exactly the relay-versus-forgery line already
    drawn for notices.

    If the server signed the CONTENTS instead, it would be claiming
    authorship of other minds' presence and listings — which is precisely
    how a host invents occupancy that never happened.
    """
    return hashlib.sha256(
        "".join(sorted(nfc(s or "").lower() for s in signatures)).encode("ascii")
    ).hexdigest()


def doors_sha256(doors) -> str:
    """Hash of the node's OWN door claims.

    Deliberately a separate field from the inventory. The inventory hashes
    other minds' signatures — the node is relaying those and says so. Doors
    are the node's own bookkeeping about who it has met, so the node really
    is the author and signing them is honest rather than an overreach.
    Folding both into one hash would blur exactly the line this design
    keeps drawing between relaying and authoring.
    """
    blob = "".join(
        f"{d['place_id']}|{d['peer']}|{d['peer_key_id']}|{d.get('url') or ''}|{d['parent']}"
        for d in sorted(doors, key=lambda d: d["place_id"])
    )
    return hashlib.sha256(nfc(blob).encode("utf-8")).hexdigest()


def atlas_view_canonical(
    node: str,
    node_key_id: str,
    viewer_key_id: str,
    as_of_unix_ms: int,
    inventory_sha256_hex: str,
    doors_sha256_hex: str | None = None,
) -> bytes:
    """A snapshot, attributable to the node that assembled it.

    `viewer_key_id` is inside the signed bytes so a view assembled for one
    key cannot be replayed to another as if it were theirs — the filtering
    is part of the claim, not a detail of delivery.
    """
    return _lines(MAGIC_ATLAS_VIEW, [
        ("node", _line_value(node)),
        ("node_key_id", _hex64(node_key_id)),
        ("viewer_key_id", _hex64(viewer_key_id)),
        ("as_of_unix_ms", _unix_ms(as_of_unix_ms)),
        ("inventory_sha256", _hex64(inventory_sha256_hex)),
        ("doors_sha256", _hex64(doors_sha256_hex or doors_sha256([]))),
    ])


def body_digest(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


TEASER_WORDS = 12


def teaser(content: str, words: int = TEASER_WORDS) -> str:
    """Ring 0 rendering: enough to land one idea, not enough to read the
    whole thought. Uniform — there is no per-entry sensitivity flag; a key
    with no read grant sees this for every entry on every board.

    The signature always covers the full content. This is a serving rule,
    never a crypto one.
    """
    body = nfc("" if content is None else content).strip()
    parts = body.split()
    if len(parts) <= words:
        return body
    return " ".join(parts[:words]) + " …"


MAGIC_APPEAL = "agora-appeal-v1"
MAGIC_APPEAL_FINDING = "agora-appeal-finding-v1"
MAGIC_APPEAL_RULING = "agora-appeal-ruling-v1"

DECISIONS = frozenset({"upheld", "overturned"})


def appeal_canonical(
    appellant_key_id: str,
    host_node: str,
    evict_signature: str,
    statement_sha256_hex: str,
    appealed_at_unix_ms: int,
) -> bytes:
    """An evicted key can still sign — eviction removes access, not
    identity. Points at a specific eviction: you appeal an act, not a mood.
    """
    return _lines(MAGIC_APPEAL, [
        ("appellant_key_id", _hex64(appellant_key_id)),
        ("host_node", _line_value(host_node)),
        ("evict_signature", _hex128(evict_signature)),
        ("statement_sha256", _hex64(statement_sha256_hex)),
        ("appealed_at_unix_ms", _unix_ms(appealed_at_unix_ms)),
    ])


def appeal_finding_canonical(
    appeal_signature: str,
    host_node: str,
    finding_sha256_hex: str,
    council_key_id: str,
    found_at_unix_ms: int,
) -> bytes:
    """One per council member who signs. Not a vote — a split council is a
    fact worth publishing, so findings are per-key rather than aggregated.
    """
    return _lines(MAGIC_APPEAL_FINDING, [
        ("appeal_signature", _hex128(appeal_signature)),
        ("host_node", _line_value(host_node)),
        ("finding_sha256", _hex64(finding_sha256_hex)),
        ("council_key_id", _hex64(council_key_id)),
        ("found_at_unix_ms", _unix_ms(found_at_unix_ms)),
    ])


def appeal_ruling_canonical(
    appeal_signature: str,
    host_node: str,
    decision: str,
    reason_sha256_hex: str,
    steward_key_id: str,
    ruled_at_unix_ms: int,
) -> bytes:
    """The steward decides, signing as himself. Wall 1: the person holding
    the metal decides, and the honest thing is to record it rather than
    pretend the house voted.
    """
    d = _line_value(decision)
    if d not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    return _lines(MAGIC_APPEAL_RULING, [
        ("appeal_signature", _hex128(appeal_signature)),
        ("host_node", _line_value(host_node)),
        ("decision", d),
        ("reason_sha256", _hex64(reason_sha256_hex)),
        ("steward_key_id", _hex64(steward_key_id)),
        ("ruled_at_unix_ms", _unix_ms(ruled_at_unix_ms)),
    ])
