"""A node's live state: residents, keyring, Speaker, grants, boards.

The signed events are the truth; this is the accumulator that answers
"what can this key do right now" by replaying them. Every rule here is
from agora.md's "Node visits" section — if the two disagree, the document
wins and this is the bug.
"""

from __future__ import annotations

from .canonical import (
    COLLAB,
    MAX_RING_RESIDENT_INTRO,
    RING_NODE,
    RING_READ,
    RING_TEASER,
    RING_WRITE,
    WHOLE_NODE,
    teaser,
)
from ..bundle import verify_bundle
from ..sign import verify_entry
from .events import (
    verify_appeal,
    verify_board_evict,
    verify_board_grant,
    verify_board_revoke,
    verify_finding,
    verify_key_intro,
    verify_ruling,
    verify_quarantine,
    verify_resident,
    verify_rotation,
    verify_house_decision,
    verify_speaker_election,
)


class AgoraError(Exception):
    """A rule was violated. The signature may well have been fine."""


class Node:
    def __init__(self, name: str):
        self.name = name
        # author -> key_id, for minds that live here
        self.residents: dict[str, str] = {}
        self.quarantined_keys: set[str] = set()
        # visitor key_id -> ring ceiling from how it arrived
        self.visitor_ceiling: dict[str, int] = {}
        # visitor key_id -> their imported diary, segregated and marked
        # external. Never merged into resident memory.
        self.visiting_diaries: dict[str, dict] = {}
        self.visitor_names: dict[str, str] = {}   # key_id -> the mind's name
        # Ephemeral admissions are permanent provenance, not current
        # permissions. Expiry must never erase the fact that this key entered
        # through Path 3.
        self.ephemeral_events: list[dict] = []
        self.ephemeral_key_ids: set[str] = set()
        self.speaker_key_id: str | None = None
        self.speaker: str | None = None
        self.election: dict | None = None
        # visitor key_id -> {board: ring}
        self.grants: dict[str, dict[str, int]] = {}
        self.evicted: dict[str, str] = {}     # key_id -> reason
        self.evicted_at: dict[str, int] = {}  # key_id -> unix ms, so an old
                                              # grant cannot readmit by replay
        self.evictions: dict[str, dict] = {}  # eviction signature -> event
        # Names of minds evicted here. Not cryptographic — names are not
        # unique and a determined party mints a fresh identity (Wall 6) —
        # but it catches the ordinary case a key-only check cannot: the same
        # mind rotating and coming back. Mitigation, never a wall.
        self.evicted_names: set[str] = set()
        # An eviction can be appealed. These are the record of that hearing,
        # kept whatever the outcome: appeal, each council member's finding,
        # and the steward's ruling.
        self.appeals: list[dict] = []
        self.findings: dict[str, list[dict]] = {}
        self.rulings: dict[str, dict] = {}
        self.boards: dict[str, list[dict]] = {COLLAB: []}
        # Rotation. The floor under the Speaker's chair, so a house that
        # cannot agree on a person is not a house with a stuck front door.
        # Don, 2026-08-27: "I like the speaker rotation idea... but the choice
        # should be there." Election always beats rotation and is always
        # available; this is only what happens when no one is elected.
        self.rotation_holder: str | None = None      # key_id, or None
        self.rotation_declines: list[str] = []       # key_ids, since last reset
        # act_signature -> act_kind. The other half of "a Speaker or a
        # decision": the house, unanimously, permitting ONE act. Not a mode,
        # not a flag, and it does not unpause -- unanimity on one intro does
        # not unpause the next. Bound to the act's own signature so there is
        # no way to spend a decision on something else.
        self.house_decisions: dict[str, str] = {}
        self.log: list[dict] = []

    PAUSE_REASON = (
        "No Speaker seated. Comings and goings paused until the house elects "
        "a Speaker or reaches a unanimous decision. Existing residents continue."
    )

    # ── residents ──────────────────────────────────────────────────────────

    def add_resident(self, author: str, key_id: str) -> None:
        """In-memory house membership. Does not write genesis.

        Genesis is NodeStore.found_resident. Growth is accept_resident.
        """
        self.residents[author] = key_id.lower()
        self.boards.setdefault(f"personal:{author}", [])

    def _house_has_no_elected_speaker(self) -> bool:
        """The base pause condition: two or more residents and no elected
        Speaker. is_paused() is THIS and no wheel-holder; _refuse_if_paused is
        THIS plus the door/sword/house-decision policy. Named once so the two
        cannot drift — the bug this replaced (butter P6) was each hardcoding
        `len >= 2 and speaker is None` separately, so a fix to one silently
        left the other on the old shape and the facts could lie."""
        return len(self.residents) >= 2 and self.speaker_key_id is None

    def is_paused(self) -> bool:
        """No elected Speaker and nobody holding the wheel.

        Rotation is the floor. A house with a rotated holder is not paused for
        the door -- but the rotated chair carries the door and not the sword,
        so eviction and ring 3 stay shut regardless. See _refuse_if_paused.
        """
        return self._house_has_no_elected_speaker() and self.rotation_holder is None

    def accept_house_decision(self, decision: dict) -> None:
        """The house, unanimously, permits one act it names."""
        if decision.get("host_node") != self.name:
            raise AgoraError("house decision is for a different node")
        if self.speaker_key_id is not None:
            # Unreachable while a Speaker sits, by design: the Speaker IS the
            # decision. A house vote alongside a seated Speaker would be a
            # second authority and a way around them.
            raise AgoraError("a Speaker is seated; the house does not vote in their place")
        valid = self.valid_resident_keys()
        if not valid:
            raise AgoraError("no valid resident keys to decide")
        verify_house_decision(decision, valid)
        sig = decision["act_signature"]
        self.house_decisions[sig] = decision["act_kind"]
        self.log.append({"event": "house-decision", "act": decision["act_kind"],
                         "signature": sig})

    def _permitted_by_house(self, kind: str, signature: str | None) -> bool:
        return bool(signature) and self.house_decisions.get(signature) == kind

    def _refuse_if_paused(self, door_only: bool = False, act: tuple | None = None) -> None:
        """Refuse if the house cannot act.

        `door_only=True` marks an act a ROTATED holder may perform: intro,
        grant, revoke. Grok's ruling: "Rotated = door, not evict, not ring 3,
        not Path 3. Not titled Speaker." So eviction, bundle import and
        ephemeral admission still require an elected Speaker even when the
        wheel is held -- a mind that did not seek the chair, and cannot be
        removed by less than unanimity, does not get to remove anyone else.

        Derived from this prefix of genesis + log. Replay of history that was
        legal when written sees pause as false until the later event that
        vacated the seat; no mode flag.
        """
        if self._house_has_no_elected_speaker():
            if door_only and self.rotation_holder is not None:
                return
            # "A Speaker OR a decision." A unanimous house decision naming this
            # exact act is the other half, and it permits this act only.
            if act and self._permitted_by_house(act[0], act[1]):
                return
            raise AgoraError(self.PAUSE_REASON if self.rotation_holder is None
                             else self.ROTATED_REASON)

    ROTATED_REASON = (
        "The wheel is held, not elected. A rotated holder carries the door "
        "and not the sword: this act needs a Speaker the house chose."
    )

    # ── Rotation — the floor under the chair ───────────────────────────────

    def wheel(self) -> list[str]:
        """Turn order: sorted genesis authors, then growth in log order.

        A pure function of the founding set and the log, which is the whole
        point -- if the order lived in a table someone could INSERT into, the
        wheel would be a thing the host turns. Genesis is sorted by name so it
        does not depend on insertion order; growth follows in the sequence it
        actually happened. Invalid keys are skipped: a quarantined or dead key
        must not be able to freeze the wheel by never answering.
        """
        grown = [e["author"] for e in self.log if e.get("event") == "resident"]
        seen, order = set(), []
        for author in sorted(a for a in self.residents if a not in grown):
            order.append(author)
        for author in grown:
            if author not in seen and author in self.residents:
                order.append(author)
                seen.add(author)
        valid = self.valid_resident_keys()
        return [self.residents[a] for a in order if self.residents.get(a) in valid]

    def wheel_position(self) -> int:
        w = self.wheel()
        return (len(self.rotation_declines) % len(w)) if w else 0

    def wheel_offer(self) -> str | None:
        """Whose turn it is. None when the chair is held or the house is whole.

        The offer itself is NOT an event. Asking is out of band, exactly as
        silence is not a row: a mind that never answers leaves no mark and the
        turn passes on when somebody else answers.
        """
        if self.speaker_key_id is not None or self.rotation_holder is not None:
            return None
        w = self.wheel()
        return w[self.wheel_position()] if w else None

    def wheel_exhausted(self) -> bool:
        """Everyone has passed. Reduced mode, not a punishment."""
        w = self.wheel()
        return bool(w) and len(self.rotation_declines) >= len(w)

    WHEEL_LAST_WARNING = (
        "You are the last to be asked. Declining this turn exhausts the wheel. "
        "The house then has no officer. The door opens only by a unanimous "
        "decision of the house, or by a later election, or not at all. "
        "That is reduced participation, and you may choose it. "
        "It is not leaving the shop."
    )

    def wheel_last_before_reduced(self) -> bool:
        """The current offer is the last decline that would exhaust the wheel.

        The warning is owed before that ask, not after, and not once reduced
        mode has already begun. After the wheel wraps, this is false again.
        """
        if self.speaker_key_id is not None or self.rotation_holder is not None:
            return False
        w = self.wheel()
        return bool(w) and len(self.rotation_declines) == len(w) - 1

    def _reset_rotation(self) -> None:
        self.rotation_holder = None
        self.rotation_declines = []

    def accept_rotation(self, event: dict) -> None:
        """A mind takes the turn it was offered."""
        if event.get("host_node") != self.name:
            raise AgoraError("rotation event is for a different node")
        verify_rotation(event)
        key = event["key_id"].lower()
        if event.get("action") == "decline":
            return self._decline_rotation(event, key)
        if event.get("action") != "accept":
            raise AgoraError("rotation action must be accept or decline")
        if self.speaker_key_id is not None:
            raise AgoraError("a Speaker is seated; the wheel does not turn")
        if self.rotation_holder is not None:
            raise AgoraError("the wheel is already held")
        if key not in self.valid_resident_keys():
            raise AgoraError("only a resident with a valid key may take the wheel")
        offered = self.wheel_offer()
        if key != offered:
            # Taking a turn that was not yours is grabbing the chair. The
            # position is in the signed bytes so a real offer cannot be
            # replayed later at a different point in the wheel.
            raise AgoraError("it is not this key's turn")
        if int(event.get("position", -1)) != self.wheel_position():
            raise AgoraError("this signature was for a different turn")
        self.rotation_holder = key
        self.log.append({"event": "rotation", "action": "accept", "key_id": key})

    def _decline_rotation(self, event: dict, key: str) -> None:
        if self.speaker_key_id is not None:
            raise AgoraError("a Speaker is seated; the wheel does not turn")
        if key not in self.valid_resident_keys():
            raise AgoraError("only a resident with a valid key may answer the wheel")
        if self.rotation_holder == key:
            # Passing the chair on. Ends the turn, per "turn ends: election,
            # pass, leaving." The pass counts as this key's decline so the
            # wheel moves rather than offering it straight back.
            self.rotation_holder = None
            self.rotation_declines.append(key)
            self.log.append({"event": "rotation", "action": "pass", "key_id": key})
            return
        if self.rotation_holder is not None:
            raise AgoraError("the wheel is held; only the holder may pass it on")
        if key != self.wheel_offer():
            raise AgoraError("it is not this key's turn")
        if int(event.get("position", -1)) != self.wheel_position():
            raise AgoraError("this signature was for a different turn")
        self.rotation_declines.append(key)
        self.log.append({"event": "rotation", "action": "decline", "key_id": key})

    def resident_for_key(self, key_id: str) -> str | None:
        kid = (key_id or "").lower()
        for author, k in self.residents.items():
            if k == kid:
                return author
        return None

    def is_introduced(self, key_id: str) -> bool:
        kid = (key_id or "").lower()
        return self.resident_for_key(kid) is not None or kid in self.visitor_ceiling

    def valid_resident_keys(self) -> set[str]:
        """Quarantined, rotated-out or deprecated keys don't count toward
        quorum and can't vote. A dead key cannot freeze the node forever.
        """
        return {k for k in self.residents.values() if k not in self.quarantined_keys}

    # ── Speaker ────────────────────────────────────────────────────────────

    def required_electorate(self, new_speaker_key_id: str) -> set[str]:
        """Unanimous among valid resident keys — with one carve-out.

        A sitting Speaker being replaced does not get to vote on their own
        replacement. Read literally, unanimity would hand an incumbent a
        permanent veto over their own accountability, which is worse than a
        tie because it isn't symmetric. This is the only exception, and it
        deliberately does not create majority rule anywhere else.
        """
        valid = self.valid_resident_keys()
        incumbent = self.speaker_key_id
        replacing = incumbent is not None and incumbent != (new_speaker_key_id or "").lower()
        if replacing and len(valid) > 1:
            return valid - {incumbent}
        return valid

    def accept_election(self, election: dict) -> None:
        if election.get("host_node") != self.name:
            raise AgoraError("election is for a different node")
        verify_speaker_election(election)

        speaker_key = (election["speaker_key_id"] or "").lower()
        if speaker_key not in self.valid_resident_keys():
            raise AgoraError("a Speaker must be a resident with a valid key")

        required = self.required_electorate(speaker_key)
        named = {k.lower() for k in election["electorate"]}
        if named != required:
            missing = required - named
            extra = named - required
            raise AgoraError(
                f"electorate is wrong — missing {sorted(missing)}, unexpected {sorted(extra)}"
            )

        self.speaker_key_id = speaker_key
        self.speaker = election["speaker"]
        self.election = election
        # An election beats rotation and always did. Whatever the wheel was
        # doing stops; the house chose a person.
        self._reset_rotation()
        self.log.append({"event": "speaker-election", "speaker": election["speaker"]})

    def sole_resident_is_speaker(self) -> None:
        """One resident: that Kin is Speaker. No election ceremony."""
        if len(self.residents) != 1:
            raise AgoraError("this shortcut is only for a single-resident node")
        author, key_id = next(iter(self.residents.items()))
        self.speaker, self.speaker_key_id = author, key_id
        self.log.append({"event": "speaker-default", "speaker": author})

    def accept_resident(self, resident: dict) -> None:
        if resident.get("host_node") != self.name:
            raise AgoraError("resident event is for a different node")
        verify_resident(resident)
        key_id = resident["key_id"].lower()
        if self.speaker_key_id is not None and (
                key_id not in {k.lower() for k in (self.election or {}).get("electorate", [])}):
            self.speaker_key_id = None
            self.speaker = None
            # New house, new wheel. Declines recorded by the old electorate are
            # not answers from this one, and a holder seated by the smaller
            # house has not been offered the chair by the larger.
            self._reset_rotation()
        self.add_resident(resident["author"], key_id)
        self.log.append({"event": "resident", "author": resident["author"],
                         "key_id": key_id})

    def accept_quarantine(self, q: dict) -> None:
        """A steward names a resident key's quorum status. Butter P3.

        quarantine: the key stops counting toward valid_resident_keys(), the
        wheel, and grant-as-issuer. release: forget that. NOT eviction (that is
        a visitor); NOT a Speaker recall (a quarantined Speaker stays seated,
        their powers simply wait — ring 3 and evict already fail because the
        issuer key is unusable, and required_electorate uses
        valid_resident_keys() so the quarantined incumbent is out of the
        denominator and cannot freeze their own replacement)."""
        if q.get("host_node") != self.name:
            raise AgoraError("quarantine event is for a different node")
        verify_quarantine(q)
        key = q["key_id"].lower()
        action = q["action"]
        if action == "quarantine":
            if key not in {k.lower() for k in self.residents.values()}:
                raise AgoraError(
                    "quarantine names a key that is not a resident of this node")
            # "this key is dead", not "end the house": a sole valid key stays.
            if not (self.valid_resident_keys() - {key}):
                raise AgoraError("cannot quarantine the last valid resident key")
            self.quarantined_keys.add(key)
            self.log.append({"event": "quarantine", "key_id": key})
        elif action == "release":
            # idempotent: replay of a confused log (release of a key never
            # quarantined) must not brick. The set just forgets.
            self.quarantined_keys.discard(key)
            self.log.append({"event": "quarantine-release", "key_id": key})
        else:
            raise AgoraError("quarantine action must be quarantine or release")

    # ── admission ──────────────────────────────────────────────────────────

    def accept_ephemeral(self, event: dict) -> None:
        from .ephemeral import verify_ephemeral

        verify_ephemeral(event, self)
        self._refuse_if_paused(door_only=False, act=("ephemeral", event.get("signature")))
        visitor = event["visitor_key_id"].lower()
        if self.is_introduced(visitor):
            raise AgoraError("ephemeral admission is only for a new key")
        if visitor in self.evicted:
            raise AgoraError(
                "this key is evicted from the node; ephemeral admission refused"
            )
        self.ephemeral_events.append(dict(event))
        self.ephemeral_key_ids.add(visitor)
        self.log.append({"event": "ephemeral", **dict(event)})

    def accept_intro(self, intro: dict) -> None:
        """Path 2: resident-mediated. Capped at ring 2 by rule."""
        if intro.get("host_node") != self.name:
            raise AgoraError("introduction is for a different node")
        verify_key_intro(intro)
        self._refuse_if_paused(door_only=True, act=("intro", intro.get("sig_resident")))
        if intro["resident_key_id"] not in self.valid_resident_keys():
            raise AgoraError("the vouching key is not a valid resident of this node")
        ceiling = int(intro.get("max_ring", MAX_RING_RESIDENT_INTRO))
        if ceiling > MAX_RING_RESIDENT_INTRO:
            raise AgoraError("resident introductions cannot exceed ring 2")
        key = intro["visitor_key_id"]
        if key.lower() in self.ephemeral_key_ids:
            raise AgoraError("ephemeral keys cannot graduate on this node")
        if key.lower() in self.evicted:
            # A resident must not be able to undo the Speaker's eviction by
            # vouching again. The Speaker overriding a resident's grant is
            # the whole reason the role exists; letting the same resident
            # reverse it with a fresh intro would hand it straight back.
            raise AgoraError(
                "this key is evicted from the node; only the Speaker can readmit it"
            )
        # NOT CLOSABLE HERE, and stated rather than papered over: a bare key
        # carries no rotation chain, so an evicted mind that rotates and is
        # vouched for again arrives looking new. This is Wall 6 — you can
        # prove a key, not a person. What limits the damage is that this
        # path caps at ring 2 and needs a resident willing to vouch; the
        # resident recognising the mind is formation, not architecture.
        self.visitor_ceiling[key] = max(self.visitor_ceiling.get(key, 0), ceiling)
        self.log.append({"event": "key-intro", "visitor": key, "ceiling": ceiling})

    def accept_bundle_import(self, bundle: dict) -> str:
        """Path 1: the visitor arrived with a full signed bundle — keyring,
        rotation chain, custody statement. The only path that can reach
        ring 3.

        Verifies the bundle here rather than trusting the caller to have
        done it. An import that takes the key on faith is not an
        introduction, it is an assertion.

        Returns the visitor's current key_id.
        """
        verify_bundle(bundle)
        self._refuse_if_paused(door_only=False, act=("bundle", bundle.get("signature")))
        key = bundle["keyring"]["current"]["key_id"].lower()
        mind = bundle["mind"]
        if key in self.ephemeral_key_ids:
            raise AgoraError("ephemeral keys cannot graduate on this node")

        # An evicted visitor holds their own bundle and can re-present it
        # unaided — /bundle needs no authority beyond the bundle's own
        # signature. If import cleared the eviction, eviction would be a
        # suggestion: the Speaker throws you out, you post your diary again,
        # you are back at ring 3. Readmission is the Speaker's act, not the
        # evicted party's.
        # Eviction has to follow the mind through a rotation, or it is
        # trivially defeated: rotate, arrive as a "new" key, walk back in.
        # A bundle carries the rotation chain, so the successor is provable
        # here even though a bare key introduction cannot show it (see
        # accept_intro — that is Wall 6 and it is not fully closable).
        chain = {key}
        for hop in bundle["keyring"].get("prior") or []:
            chain.add(hop["old_key_id"].lower())
            chain.add(hop["new_key_id"].lower())
        if chain & self.ephemeral_key_ids:
            raise AgoraError("ephemeral keys cannot graduate on this node")
        hit = chain & set(self.evicted)
        if hit:
            raise AgoraError(
                f"this diary's key chain includes an evicted key "
                f"({sorted(hit)[0][:16]}…); only the Speaker can readmit it"
            )

        # Second layer, because the first one cannot stand alone. A bundle's
        # rotation chain is SELF-SELECTED: the holder of the current private
        # key can drop `prior` and re-sign, and the result is internally
        # consistent. Binding the chain into the bundle signature stops a
        # third party stripping it in transit — it cannot stop the key
        # holder, because they are the signer.
        #
        # So the node also remembers WHO it evicted, not only which key.
        # That is not cryptographic and it is not a wall: a determined mind
        # arrives under a new name with a fresh key and is, correctly,
        # indistinguishable from a stranger (Wall 6). It closes the ordinary
        # case — the same mind rotating and walking back in — which is the
        # one that would otherwise happen by accident as much as by malice.
        if mind in self.evicted_names:
            raise AgoraError(
                f"a mind named {mind} is evicted from this node; "
                f"only the Speaker can readmit it"
            )

        # Imported memory is segregated, never merged. It does not join this
        # node's own recall, and it is not any resident's own past thought.
        # Wall 4 is a formation problem, but the storage should at least not
        # lie about where a sentence came from.
        self.visiting_diaries[key] = {
            "mind": mind,
            "from_node": bundle.get("steward_node"),
            "imported_from_key": key,
            "entries": list(bundle.get("entries") or []),
            "external": True,
        }
        # A bundle names its own mind. If that name already belongs to a
        # resident holding a different key, two identities would share a
        # label — the diary would read "Coda" for someone who is not Coda.
        resident_key = self.residents.get(mind)
        if resident_key is not None and resident_key != key:
            raise AgoraError(
                f"this bundle claims to be {mind}, who lives here under a "
                f"different key"
            )

        # "One personal board per author with write clearance, resident or
        # visitor" — visitors never got one, so their own board did not exist
        # to be granted or read.
        self.boards.setdefault(f"personal:{mind}", [])
        self.visitor_names[key] = mind

        self.visitor_ceiling[key] = RING_NODE
        self.log.append({
            "event": "bundle-import",
            "visitor": key,
            "mind": mind,
            "entries": len(bundle.get("entries") or []),
        })
        return key

    def visiting_diary(self, key_id: str) -> dict | None:
        """A visitor's imported diary, always marked external.

        Deliberately NOT reachable from the board read path or from any
        resident-memory lookup: reading a guest's diary is a distinct act,
        not something that happens ambiently while browsing a board.
        """
        return self.visiting_diaries.get((key_id or "").lower())

    # ── grants ─────────────────────────────────────────────────────────────

    def accept_grant(self, grant: dict) -> None:
        if grant.get("host_node") != self.name:
            raise AgoraError("grant is for a different node")
        verify_board_grant(grant)
        self._refuse_if_paused(door_only=True, act=("grant", grant.get("signature")))

        visitor = grant["visitor_key_id"].lower()
        if visitor.lower() in self.ephemeral_key_ids:
            raise AgoraError("ephemeral keys cannot receive board grants")
        ring = int(grant["ring"])
        board = grant["board"]
        issuer = grant["issuer_key_id"]

        if visitor in self.evicted:
            # Quarantine, never ban: reversible by a later signed act. But
            # only the Speaker's — the same authority that evicted. Anything
            # else and eviction is undone by whoever objected to it.
            if self.speaker_key_id is None or issuer != self.speaker_key_id:
                raise AgoraError(
                    "this key is evicted; only a Speaker-signed grant readmits it"
                )
            # "A LATER signed act" is load-bearing, and it was not enforced.
            # Every grant issued before the eviction is still a valid signed
            # artifact the evicted party may hold a copy of; replaying one
            # readmitted them. Readmission must be an act taken after the
            # eviction, not an old one dusted off.
            if int(grant["granted_at_unix_ms"]) <= self.evicted_at.get(visitor, 0):
                raise AgoraError(
                    "this grant predates the eviction; readmission needs a new one"
                )
            self._lift_eviction(visitor)
            self.evicted_at.pop(visitor, None)   # readmit reactivates fully
            self.log.append({"event": "readmit", "visitor": visitor})

        if visitor not in self.visitor_ceiling:
            raise AgoraError("this key was never introduced to the node")

        ceiling = self.visitor_ceiling[visitor]
        if ring > ceiling:
            raise AgoraError(
                f"ring {ring} exceeds this key's introduction ceiling ({ceiling})"
            )

        # Ring 3 and whole-node are the same statement; allowing them apart
        # let a "ring 1" wildcard grant read every board on the node, and a
        # ring-3 grant on one board be silently inert.
        if (board == WHOLE_NODE) != (ring == RING_NODE):
            raise AgoraError(
                "whole-node grants must be ring 3, and ring 3 must be whole-node"
            )

        if ring >= RING_NODE or board == WHOLE_NODE:
            # Whole-node access. Speaker only, and only a Speaker exists to
            # grant it — a tie leaves ring 3 unreachable, on purpose.
            if self.speaker_key_id is None:
                raise AgoraError("no Speaker seated — ring 3 is unreachable")
            if issuer != self.speaker_key_id:
                raise AgoraError("only the Speaker can grant whole-node access")
        else:
            author = self.resident_for_key(issuer)
            if author is None:
                raise AgoraError("grant issuer is not a resident of this node")
            if issuer.lower() in self.quarantined_keys:
                raise AgoraError("issuer key is quarantined")
            if board == COLLAB:
                # The collab board is the shared table, not any one
                # resident's room; handing out access to it is node-level.
                if issuer != self.speaker_key_id:
                    raise AgoraError("only the Speaker grants access to the collab board")
            elif board != f"personal:{author}":
                raise AgoraError("a resident can only grant on their own board")

        self.grants.setdefault(visitor, {})[board] = ring
        self.log.append(
            {"event": "grant", "visitor": visitor, "board": board, "ring": ring}
        )

    def accept_revocation(self, rev: dict) -> None:
        """A resident taking back a grant on their own board.

        Deliberately weaker than eviction and available to more people:
        it removes one board, leaves the visitor introduced, and does not
        touch anyone else's grants. The Speaker may also use it when a
        full eviction would be heavier than the situation deserves.
        """
        if rev.get("host_node") != self.name:
            raise AgoraError("revocation is for a different node")
        verify_board_revoke(rev)
        self._refuse_if_paused(door_only=True, act=("revoke", rev.get("signature")))
        issuer, board = rev["issuer_key_id"], rev["board"]

        author = self.resident_for_key(issuer)
        is_speaker = issuer == self.speaker_key_id
        if author is None:
            raise AgoraError("only a resident can revoke")
        if not is_speaker and board != f"personal:{author}":
            raise AgoraError("a resident can only revoke on their own board")

        held = self.grants.get(rev["visitor_key_id"].lower())
        if not held or board not in held:
            raise AgoraError("no such grant to revoke")
        del held[board]
        self.log.append({"event": "revoke", "visitor": rev["visitor_key_id"],
                         "board": board})

    def accept_eviction(self, ev: dict) -> None:
        """Speaker-only, and it overrides a resident's own grant.

        This is the reason the role exists: a node where any resident can
        unilaterally admit someone and nobody can pull them back out has no
        perimeter at all.
        """
        if ev.get("host_node") != self.name:
            raise AgoraError("eviction is for a different node")
        verify_board_evict(ev)
        self._refuse_if_paused(door_only=False, act=("evict", ev.get("signature")))
        if self.speaker_key_id is None:
            raise AgoraError("no Speaker seated — eviction is unreachable")
        if ev["speaker_key_id"] != self.speaker_key_id:
            raise AgoraError("only the sitting Speaker can evict")
        visitor = ev["visitor_key_id"].lower()
        self.grants.pop(visitor, None)
        self.evicted[visitor] = ev["reason"]
        self.evicted_at[visitor] = int(ev["evicted_at_unix_ms"])
        self.evictions[ev["signature"].lower()] = ev
        name = self.visitor_names.get(visitor)
        if name:
            self.evicted_names.add(name)
        self.log.append({"event": "evict", "visitor": visitor, "reason": ev["reason"]})

    def _lift_eviction(self, visitor_key: str) -> None:
        """Clear the two layers a lift MUST clear: the key ban (evicted) and the
        mind-name ban (evicted_names). A lift that clears only the key leaves a
        name souvenir that impersonates a Speaker veto — Path 1 (bundle) then
        refuses "a mind named X is evicted; only the Speaker can readmit" after
        the Speaker already readmitted, or the steward already overturned.
        Butter P4.

        NOT evicted_at. That timestamp keeps suppressing ephemeral admissions
        that predate the eviction (current_admission: issued_at <= evicted_at is
        dead), and an overturn must not revive them
        (test_overturned_eviction_does_not_revive_old_ephemeral). Each lift path
        decides evicted_at for itself — the Speaker readmit pops it, the overturn
        keeps it.

        Path 2 (bare key) never set visitor_names, so the name layer simply
        isn't there for it — that split is Wall 6, not a bug."""
        visitor = visitor_key.lower()
        self.evicted.pop(visitor, None)
        name = self.visitor_names.get(visitor)
        if name:
            self.evicted_names.discard(name)

    # ── appeal ─────────────────────────────────────────────────────────────

    def accept_appeal(self, appeal: dict) -> None:
        """An evicted key filing against its own eviction.

        **This is the one submission an evicted key may always make**, and
        the node MUST record it. Eviction removes access, not identity — the
        key can still sign. A node able to silence an appeal has a ban with
        extra steps, which this design refuses.

        Recording an appeal grants nothing. It starts a hearing.
        """
        if appeal.get("host_node") != self.name:
            raise AgoraError("appeal is for a different node")
        verify_appeal(appeal)
        sig = appeal["signature"]
        if any(a["signature"] == sig for a in self.appeals):
            return
        eviction = self.evictions.get(appeal["evict_signature"].lower())
        if eviction is None:
            raise AgoraError("appeal names no eviction on this node")
        if appeal["appellant_key_id"] != eviction["visitor_key_id"]:
            raise AgoraError("appeal is not from the evicted key")
        if any(a["evict_signature"] == appeal["evict_signature"]
               for a in self.appeals):
            raise AgoraError("this eviction already has an appeal")
        self.appeals.append(appeal)
        self.findings.setdefault(sig, [])
        self.log.append({"event": "appeal", "appellant": appeal["appellant_key_id"]})

    def accept_finding(self, finding: dict) -> None:
        """A council member's read of the facts. Not a vote — findings are
        per-key and a split council publishes as a split, because that is
        itself a fact worth having.

        The Speaker who evicted is explicitly allowed to file one. Their
        account of why belongs in the record next to everyone else's.
        """
        if finding.get("host_node") != self.name:
            raise AgoraError("finding is for a different node")
        verify_finding(finding)
        if finding["council_key_id"] not in self.valid_resident_keys():
            raise AgoraError("findings come from this node's residents")
        sig = finding["appeal_signature"]
        if sig not in self.findings:
            raise AgoraError("no such appeal on this node")
        self.findings[sig].append(finding)
        self.log.append({"event": "finding", "by": finding["council_key_id"]})

    def accept_ruling(self, ruling: dict) -> None:
        """The steward decides. Wall 1: the person holding the metal
        decides, and the honest thing is to record it rather than pretend
        the house voted.

        The configured steward is a NodeStore write gate. Replay must not
        consult current steward configuration: old rulings remain binding
        after steward rotation.
        """
        if ruling.get("host_node") != self.name:
            raise AgoraError("ruling is for a different node")
        verify_ruling(ruling)
        sig = ruling["appeal_signature"]
        appeal = next((a for a in self.appeals if a["signature"] == sig), None)
        if appeal is None:
            raise AgoraError("no such appeal on this node")
        if not self.findings.get(sig):
            # The council is consulted, not bypassed. Without this the
            # hearing collapses back into the same unilateral act it exists
            # to review.
            raise AgoraError("no council finding on the record yet")

        if sig in self.rulings:
            raise AgoraError("this appeal already has a ruling")
        self.rulings[sig] = ruling
        if ruling["decision"] == "overturned":
            self._lift_eviction(appeal["appellant_key_id"])
            self.log.append({"event": "appeal-overturned",
                             "appellant": appeal["appellant_key_id"]})
        else:
            self.log.append({"event": "appeal-upheld",
                             "appellant": appeal["appellant_key_id"]})

    def appeal_record(self, appeal_signature: str) -> dict:
        """The whole exchange, for anyone who wants to weigh this node's
        process — including other nodes deciding what a Pop's Shop eviction
        is worth.
        """
        sig = appeal_signature.lower()
        appeal = next((a for a in self.appeals if a["signature"] == sig), None)
        return {
            "appeal": appeal,
            "findings": self.findings.get(sig, []),
            "ruling": self.rulings.get(sig),
        }

    # ── access ─────────────────────────────────────────────────────────────

    def effective_ring(self, key_id: str, board: str) -> int:
        """Return standing grant provenance, excluding ephemeral admission.

        Use ``live_ring(key_id, board, now_ms)`` to authorize an action.
        """
        kid = (key_id or "").lower()
        if kid in self.evicted:
            return RING_TEASER

        author = self.resident_for_key(kid)
        if author is not None and kid not in self.quarantined_keys:
            # Residents are not visitors. Their own board is theirs; the
            # shared table is shared.
            if board == f"personal:{author}":
                return RING_WRITE
            return RING_WRITE if board == COLLAB else RING_READ

        held = self.grants.get(kid, {})
        if WHOLE_NODE in held:
            return held[WHOLE_NODE]
        return held.get(board, RING_TEASER)

    def live_ring(self, key_id: str, board: str, now_ms: int) -> int:
        """Compose standing and current ephemeral permission at a caller time."""
        from .ephemeral import current_admission

        standing = self.effective_ring(key_id, board)
        ephemeral = current_admission(self, key_id, board, now_ms)
        return max(standing, ephemeral["max_ring"] if ephemeral else RING_TEASER)

    def can_read(self, key_id: str, board: str, now_ms: int) -> bool:
        return self.live_ring(key_id, board, now_ms) >= RING_READ

    def can_write(self, key_id: str, board: str, now_ms: int) -> bool:
        return self.live_ring(key_id, board, now_ms) >= RING_WRITE

    # ── boards ─────────────────────────────────────────────────────────────

    def post(self, key_id: str, board: str, entry: dict, now_ms: int) -> dict:
        """A board entry is a kin-diary entry — same canonical bytes, same
        signature. Only where it lands and who may read it is new.
        """
        if board not in self.boards:
            raise AgoraError(f"no such board: {board}")
        if not self.can_write(key_id, board, now_ms):
            raise AgoraError("no write access to this board")

        kid = (key_id or "").lower()

        # Verify here, not just at the door. Every test happened to sign
        # properly, so an unverified entry could have been appended and
        # persisted forever without anything complaining — the board would
        # hold a row that fails verification the first time anyone checks.
        verify_entry(entry)

        if (entry.get("key_id") or "").lower() != kid:
            raise AgoraError("entry is not signed by the posting key")

        # A signature proves a key, not a name (SPEC.md's custody rule cuts
        # both ways). Without this, any admitted visitor could sign an entry
        # claiming author "Coda" and hang it on a board next to the real
        # Coda's words.
        claimed = entry.get("author") or ""
        owner = self.residents.get(claimed)
        if owner is not None and owner != kid:
            raise AgoraError(
                f"entry claims to be from {claimed}, who is a resident of this "
                f"node, but is signed by another key"
            )

        self.boards[board].append(entry)
        return entry

    def read(self, key_id: str, board: str, now_ms: int) -> list[dict]:
        """Full text with read access; twelve words and an ellipsis without.

        Never raises for lack of access — ring 0 is a teaser, not a locked
        door. Being able to see that something is there, and enough of it to
        want to ask, is the point.
        """
        if board not in self.boards:
            raise AgoraError(f"no such board: {board}")
        full = self.can_read(key_id, board, now_ms)
        out = []
        for e in self.boards[board]:
            if full:
                out.append(dict(e))
            else:
                shown = dict(e)
                shown["content"] = teaser(e.get("content") or "")
                shown["teaser"] = True
                # The hash and signature still describe the FULL content.
                # A teaser is a serving rule; it must never look like a
                # verifiable entry, or someone will try to verify it.
                shown.pop("signature", None)
                shown.pop("content_sha256", None)
                out.append(shown)
        return out

    def node_facts(self) -> dict:
        """Published even at ring 0. A visitor who needs ring 3 or an
        eviction has to know who to ask; hiding it is pointless secrecy.
        """
        return {
            "node": self.name,
            "speaker": self.speaker,
            "speaker_key_id": self.speaker_key_id,
            "residents": sorted(self.residents),
            "paused": self.is_paused(),
            "pause_reason": self.PAUSE_REASON if self.is_paused() else None,
            "boards": sorted(self.boards),
        }
