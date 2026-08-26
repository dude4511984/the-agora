"""The Agora: node visits, boards, rings, and the Speaker.

Design doc: ~/claude_home/agora.md
"""

from .canonical import (
    COLLAB,
    RING_NODE,
    RING_READ,
    RING_TEASER,
    RING_WRITE,
    WHOLE_NODE,
    board_id,
    teaser,
)
from .events import (
    countersign_key_intro,
    open_speaker_election,
    sign_board_evict,
    sign_board_revoke,
    sign_board_grant,
    sign_speaker_election,
    start_key_intro,
    verify_board_evict,
    verify_board_revoke,
    verify_board_grant,
    verify_key_intro,
    verify_speaker_election,
)
from .node import AgoraError, Node

__all__ = [
    "Node",
    "AgoraError",
    "board_id",
    "teaser",
    "COLLAB",
    "WHOLE_NODE",
    "RING_TEASER",
    "RING_READ",
    "RING_WRITE",
    "RING_NODE",
    "start_key_intro",
    "countersign_key_intro",
    "verify_key_intro",
    "open_speaker_election",
    "sign_speaker_election",
    "verify_speaker_election",
    "sign_board_grant",
    "verify_board_grant",
    "sign_board_evict",
    "sign_board_revoke",
    "verify_board_evict",
    "verify_board_revoke",
]
