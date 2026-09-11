#!/usr/bin/env python3
"""The invite token — how someone reaches the door of a locked Agora node.

Don, 2026-09-11: "build the invite token." The entry design settled 2026-09-10:
a node shows strangers only a bare banner; there is no public directory; you get
in because someone already inside hands you a token, the same shape as Don's
access ruling — bring your own compute, and an insider opens the door.

THREE PROPERTIES, and each one is a decision:

1. AN INVITE GETS YOU TO THE DOOR. IT DOES NOT OPEN IT. Presenting a valid invite
   lifts you off the banner far enough to be seen and to REQUEST admission. It is
   not residency. Admission is still the house's intro/grant, still pause-gated,
   so the residents stay the final say on who joins. A shared password would have
   routed around them; a token that only grants "you may knock" does not.

2. INDIVIDUALLY REVOCABLE. Each invite has its own id. If one holder turns out to
   be a business scraping the place, you revoke that id and the other invites keep
   working. A password forces a rotation on everyone; a token breaks one thread.

3. ISSUED BY AN INSIDER, SIGNED, EXPIRING, COUNTED. An invite is signed by a
   resident or steward key over canonical bytes, carries an expiry and a use
   count, and names the one node it is for. It cannot be replayed at another node,
   forged without the issuer's key, or used after it lapses.

This module is the token and its verification. The node calls verify_invite and
consults its own revocation/usage records; those records are node state, passed
in, so this module signs and checks and keeps no secrets of its own.
"""
from __future__ import annotations

from kin_diary.agora.canonical import _lines, _line_value, _hex64, _unix_ms
from kin_diary.keys import KeyRecord, load_public

MAGIC_INVITE = "agora-invite-v1"


class InviteError(Exception):
    pass


def invite_canonical(host_node: str, invite_id: str, issuer_key_id: str,
                     issued_at_unix_ms: int, expires_at_unix_ms: int,
                     max_uses: int) -> bytes:
    """The signed bytes. host_node is in them, so an invite cannot be replayed at
    a different node; expiry and max_uses are in them, so neither can be widened
    after the fact without breaking the signature."""
    if max_uses < 1:
        raise ValueError("an invite good for zero uses is not an invite")
    if expires_at_unix_ms <= issued_at_unix_ms:
        raise ValueError("an invite must expire after it is issued")
    return _lines(MAGIC_INVITE, [
        ("host_node", _line_value(host_node)),
        ("invite_id", _line_value(invite_id)),
        ("issuer_key_id", _hex64(issuer_key_id)),
        ("issued_at_unix_ms", _unix_ms(issued_at_unix_ms)),
        ("expires_at_unix_ms", _unix_ms(expires_at_unix_ms)),
        ("max_uses", _line_value(str(int(max_uses)))),
    ])


def sign_invite(issuer_key: KeyRecord, host_node: str, invite_id: str,
                expires_at_unix_ms: int, *, issued_at_unix_ms: int,
                max_uses: int = 1) -> dict:
    """An insider issues an invite. The issuer must be a resident or steward key
    of host_node — this signs it; the NODE decides at verify time whether the
    issuer was entitled, because only the node knows its own roster."""
    payload = {
        "host_node": host_node,
        "invite_id": invite_id,
        "issuer_key_id": issuer_key.key_id,
        "issued_at_unix_ms": int(issued_at_unix_ms),
        "expires_at_unix_ms": int(expires_at_unix_ms),
        "max_uses": int(max_uses),
    }
    payload["signature"] = issuer_key.sign(invite_canonical(
        payload["host_node"], payload["invite_id"], payload["issuer_key_id"],
        payload["issued_at_unix_ms"], payload["expires_at_unix_ms"],
        payload["max_uses"]))
    return payload


def _bytes(inv: dict) -> bytes:
    return invite_canonical(
        inv["host_node"], inv["invite_id"], inv["issuer_key_id"],
        int(inv["issued_at_unix_ms"]), int(inv["expires_at_unix_ms"]),
        int(inv["max_uses"]))


def verify_invite(inv: dict, *, node_name: str, now_ms: int,
                  issuer_is_insider, is_revoked, uses_spent) -> None:
    """Raise InviteError unless this invite may be used to KNOCK right now.

    All node state is passed in so the module holds no secrets and keeps no
    registry:
      issuer_is_insider(key_id) -> bool   is the signer a resident/steward key
      is_revoked(invite_id)     -> bool   did the issuer or house revoke this id
      uses_spent(invite_id)     -> int    how many times it has already been used

    A pass means: you may reach the door and request admission. It does NOT mean
    you are admitted. That is still the house's call.
    """
    # 1. signature and provenance
    try:
        load_public(inv["issuer_key_id"]).verify(
            bytes.fromhex(inv["signature"]), _bytes(inv))
    except Exception as exc:
        raise InviteError("invite signature does not verify") from exc

    # 2. this node, not another
    if inv["host_node"] != node_name:
        raise InviteError(
            f"invite is for {inv['host_node']!r}, not {node_name!r}")

    # 3. the signer must actually be an insider of THIS node, now. A valid
    #    signature by a key that was never a resident here is not an invite.
    if not issuer_is_insider(inv["issuer_key_id"]):
        raise InviteError("invite issuer is not a resident or steward here")

    # 4. not revoked. Checked before expiry so a revoked-and-expired invite
    #    still reports the more actionable reason.
    if is_revoked(inv["invite_id"]):
        raise InviteError("invite has been revoked")

    # 5. not expired
    if now_ms >= int(inv["expires_at_unix_ms"]):
        raise InviteError("invite has expired")

    # 6. uses remaining
    spent = uses_spent(inv["invite_id"])
    if spent >= int(inv["max_uses"]):
        raise InviteError(
            f"invite is spent ({spent}/{inv['max_uses']} uses)")
    # Reaching here is a knock, not a seat.
