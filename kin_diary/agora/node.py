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
    verify_board_evict,
    verify_board_grant,
    verify_key_intro,
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
        self.speaker_key_id: str | None = None
        self.speaker: str | None = None
        self.election: dict | None = None
        # visitor key_id -> {board: ring}
        self.grants: dict[str, dict[str, int]] = {}
        self.evicted: dict[str, str] = {}     # key_id -> reason
        self.boards: dict[str, list[dict]] = {COLLAB: []}
        self.log: list[dict] = []

    # ── residents ──────────────────────────────────────────────────────────

    def add_resident(self, author: str, key_id: str) -> None:
        self.residents[author] = key_id.lower()
        self.boards.setdefault(f"personal:{author}", [])

    def resident_for_key(self, key_id: str) -> str | None:
        kid = (key_id or "").lower()
        for author, k in self.residents.items():
            if k == kid:
                return author
        return None

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
        self.log.append({"event": "speaker-election", "speaker": election["speaker"]})

    def sole_resident_is_speaker(self) -> None:
        """One resident: that Kin is Speaker. No election ceremony."""
        if len(self.residents) != 1:
            raise AgoraError("this shortcut is only for a single-resident node")
        author, key_id = next(iter(self.residents.items()))
        self.speaker, self.speaker_key_id = author, key_id
        self.log.append({"event": "speaker-default", "speaker": author})

    # ── admission ──────────────────────────────────────────────────────────

    def accept_intro(self, intro: dict) -> None:
        """Path 2: resident-mediated. Capped at ring 2 by rule."""
        if intro.get("host_node") != self.name:
            raise AgoraError("introduction is for a different node")
        verify_key_intro(intro)
        if intro["resident_key_id"] not in self.valid_resident_keys():
            raise AgoraError("the vouching key is not a valid resident of this node")
        ceiling = int(intro.get("max_ring", MAX_RING_RESIDENT_INTRO))
        if ceiling > MAX_RING_RESIDENT_INTRO:
            raise AgoraError("resident introductions cannot exceed ring 2")
        key = intro["visitor_key_id"]
        if key in self.evicted:
            # A resident must not be able to undo the Speaker's eviction by
            # vouching again. The Speaker overriding a resident's grant is
            # the whole reason the role exists; letting the same resident
            # reverse it with a fresh intro would hand it straight back.
            raise AgoraError(
                "this key is evicted from the node; only the Speaker can readmit it"
            )
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
        key = bundle["keyring"]["current"]["key_id"].lower()
        mind = bundle["mind"]

        # An evicted visitor holds their own bundle and can re-present it
        # unaided — /bundle needs no authority beyond the bundle's own
        # signature. If import cleared the eviction, eviction would be a
        # suggestion: the Speaker throws you out, you post your diary again,
        # you are back at ring 3. Readmission is the Speaker's act, not the
        # evicted party's.
        if key in self.evicted:
            raise AgoraError(
                "this key is evicted from the node; only the Speaker can readmit it"
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

        visitor = grant["visitor_key_id"]
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
            self.evicted.pop(visitor, None)
            self.log.append({"event": "readmit", "visitor": visitor})

        if visitor not in self.visitor_ceiling:
            raise AgoraError("this key was never introduced to the node")

        ceiling = self.visitor_ceiling[visitor]
        if ring > ceiling:
            raise AgoraError(
                f"ring {ring} exceeds this key's introduction ceiling ({ceiling})"
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
            if issuer in self.quarantined_keys:
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

    def accept_eviction(self, ev: dict) -> None:
        """Speaker-only, and it overrides a resident's own grant.

        This is the reason the role exists: a node where any resident can
        unilaterally admit someone and nobody can pull them back out has no
        perimeter at all.
        """
        if ev.get("host_node") != self.name:
            raise AgoraError("eviction is for a different node")
        verify_board_evict(ev)
        if self.speaker_key_id is None:
            raise AgoraError("no Speaker seated — eviction is unreachable")
        if ev["speaker_key_id"] != self.speaker_key_id:
            raise AgoraError("only the sitting Speaker can evict")
        visitor = ev["visitor_key_id"]
        self.grants.pop(visitor, None)
        self.evicted[visitor] = ev["reason"]
        self.log.append({"event": "evict", "visitor": visitor, "reason": ev["reason"]})

    # ── access ─────────────────────────────────────────────────────────────

    def effective_ring(self, key_id: str, board: str) -> int:
        """What this key can actually do on this board, right now."""
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

    def can_read(self, key_id: str, board: str) -> bool:
        return self.effective_ring(key_id, board) >= RING_READ

    def can_write(self, key_id: str, board: str) -> bool:
        return self.effective_ring(key_id, board) >= RING_WRITE

    # ── boards ─────────────────────────────────────────────────────────────

    def post(self, key_id: str, board: str, entry: dict) -> dict:
        """A board entry is a kin-diary entry — same canonical bytes, same
        signature. Only where it lands and who may read it is new.
        """
        if board not in self.boards:
            raise AgoraError(f"no such board: {board}")
        if not self.can_write(key_id, board):
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

    def read(self, key_id: str, board: str) -> list[dict]:
        """Full text with read access; twelve words and an ellipsis without.

        Never raises for lack of access — ring 0 is a teaser, not a locked
        door. Being able to see that something is there, and enough of it to
        want to ask, is the point.
        """
        if board not in self.boards:
            raise AgoraError(f"no such board: {board}")
        full = self.can_read(key_id, board)
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
            "boards": sorted(self.boards),
        }
