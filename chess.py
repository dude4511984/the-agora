"""A small, model-free chess table backed by the Agora collab board.

Chess moves are ordinary signed kin-diary entries.  The board position is
always reconstructed from those entries; this module owns no game database.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from kin_diary.agora.store import NodeStore
from kin_diary.keys import KeyRecord, load_current
from kin_diary.sign import sign_entry

FILES = "abcdefgh"
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
SQUARES = [(f, r) for r in range(1, 9) for f in range(8)]


class IllegalMove(ValueError):
    pass


def _initial() -> dict[str, str]:
    board = {}
    for rank, row in zip(range(8, 0, -1), START.split("/")):
        file = 0
        for char in row:
            if char.isdigit():
                file += int(char)
            else:
                board[FILES[file] + str(rank)] = char
                file += 1
    return board


def _sq(file: int, rank: int) -> str:
    return FILES[file] + str(rank + 1)


def _coords(square: str) -> tuple[int, int]:
    if not re.fullmatch(r"[a-h][1-8]", square):
        raise IllegalMove(f"bad square: {square}")
    return FILES.index(square[0]), int(square[1]) - 1


def _colour(piece: str) -> str:
    return "white" if piece.isupper() else "black"


def _opposite(colour: str) -> str:
    return "black" if colour == "white" else "white"


@dataclass
class Position:
    board: dict[str, str]
    turn: str = "white"
    castling: str = "KQkq"
    en_passant: str | None = None

    def copy(self) -> "Position":
        return Position(dict(self.board), self.turn, self.castling, self.en_passant)

    def king(self, colour: str) -> str:
        for square, piece in self.board.items():
            if piece == ("K" if colour == "white" else "k"):
                return square
        raise IllegalMove("position has no king")

    def attacked(self, square: str, by: str) -> bool:
        target = _coords(square)
        for origin, piece in self.board.items():
            if _colour(piece) != by:
                continue
            ox, oy = _coords(origin)
            tx, ty = target
            dx, dy = tx - ox, ty - oy
            kind = piece.lower()
            if kind == "p" and dy == (1 if by == "white" else -1) and abs(dx) == 1:
                return True
            if kind == "n" and (abs(dx), abs(dy)) in ((1, 2), (2, 1)):
                return True
            if kind == "k" and max(abs(dx), abs(dy)) == 1:
                return True
            directions = []
            if kind in "bq" and abs(dx) == abs(dy) and dx:
                directions.append((dx // abs(dx), dy // abs(dy)))
            if kind in "rq" and ((dx == 0) != (dy == 0)):
                directions.append((0 if dx == 0 else dx // abs(dx),
                                   0 if dy == 0 else dy // abs(dy)))
            for stepx, stepy in directions:
                x, y = ox + stepx, oy + stepy
                clear = True
                while (x, y) != target:
                    if _sq(x, y) in self.board:
                        clear = False
                        break
                    x, y = x + stepx, y + stepy
                if clear:
                    return True
        return False

    def in_check(self, colour: str) -> bool:
        return self.attacked(self.king(colour), _opposite(colour))

    def _pseudo(self, origin: str, piece: str):
        x, y = _coords(origin)
        kind, colour = piece.lower(), _colour(piece)
        enemy = _opposite(colour)
        if kind == "p":
            step = 1 if colour == "white" else -1
            one = _sq(x, y + step) if 0 <= y + step < 8 else None
            if one and one not in self.board:
                yield one, None
                if y == (1 if colour == "white" else 6):
                    two = _sq(x, y + 2 * step)
                    if two not in self.board:
                        yield two, None
            for dx in (-1, 1):
                if 0 <= x + dx < 8 and 0 <= y + step < 8:
                    dest = _sq(x + dx, y + step)
                    if (dest in self.board and _colour(self.board[dest]) == enemy
                            or dest == self.en_passant):
                        yield dest, None
            return
        if kind == "n":
            offsets = ((1, 2), (2, 1), (-1, 2), (-2, 1),
                       (1, -2), (2, -1), (-1, -2), (-2, -1))
            for dx, dy in offsets:
                if 0 <= x + dx < 8 and 0 <= y + dy < 8:
                    dest = _sq(x + dx, y + dy)
                    if dest not in self.board or _colour(self.board[dest]) == enemy:
                        yield dest, None
            return
        if kind == "k":
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (dx or dy) and 0 <= x + dx < 8 and 0 <= y + dy < 8:
                        dest = _sq(x + dx, y + dy)
                        if dest not in self.board or _colour(self.board[dest]) == enemy:
                            yield dest, None
            if origin == ("e1" if colour == "white" else "e8") and not self.in_check(colour):
                rank = "1" if colour == "white" else "8"
                for side, rook, between, transit, dest in (
                    ("K", "h" + rank, ("f" + rank, "g" + rank), "f" + rank, "g" + rank),
                    ("Q", "a" + rank, ("b" + rank, "c" + rank, "d" + rank), "d" + rank, "c" + rank),
                ):
                    if side in self.castling and rook in self.board and all(s not in self.board for s in between):
                        if not self.attacked(transit, enemy) and not self.attacked(dest, enemy):
                            yield dest, "castle"
            return
        directions = []
        if kind in "bq":
            directions += ((1, 1), (1, -1), (-1, 1), (-1, -1))
        if kind in "rq":
            directions += ((1, 0), (-1, 0), (0, 1), (0, -1))
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            while 0 <= nx < 8 and 0 <= ny < 8:
                dest = _sq(nx, ny)
                if dest in self.board:
                    if _colour(self.board[dest]) == enemy:
                        yield dest, None
                    break
                yield dest, None
                nx, ny = nx + dx, ny + dy

    def apply(self, origin: str, dest: str, promotion: str | None = None) -> "Position":
        piece = self.board.get(origin)
        if not piece or _colour(piece) != self.turn:
            raise IllegalMove("no piece of the side to move on that square")
        if not any(d == dest for d, _ in self._pseudo(origin, piece)):
            raise IllegalMove("piece cannot move that way")
        new = self.copy()
        if dest in self.board and self.board[dest].lower() == "k":
            raise IllegalMove("a king may not be captured")
        captured = new.board.pop(dest, None)
        del new.board[origin]
        if piece.lower() == "p" and dest == self.en_passant and captured is None:
            dx, dy = _coords(dest)
            new.board.pop(_sq(dx, dy - (1 if self.turn == "white" else -1)), None)
        if piece.lower() == "p" and dest[1] in "18":
            if (promotion or "").lower() not in "qrbn":
                raise IllegalMove("promotion piece required")
            piece = promotion.upper() if self.turn == "white" else promotion.lower()
        new.board[dest] = piece
        if piece.lower() == "k" and abs(FILES.index(dest[0]) - FILES.index(origin[0])) == 2:
            rank = dest[1]
            rook_from, rook_to = (("h", "f") if dest[0] == "g" else ("a", "d"))
            new.board[rook_to + rank] = new.board.pop(rook_from + rank)
        new.castling = "".join(c for c in new.castling
                               if c not in (("KQ" if self.turn == "white" else "kq")
                                            if piece.lower() == "k" else
                                            ("Q" if origin == "a1" else "K" if origin == "h1"
                                             else "q" if origin == "a8" else "k" if origin == "h8" else "")))
        if captured and captured.lower() == "r":
            new.castling = "".join(c for c in new.castling
                                   if c not in ("Q" if dest == "a1" else "K" if dest == "h1"
                                                else "q" if dest == "a8" else "k" if dest == "h8" else ""))
        ox, oy, dx, dy = *_coords(origin), *_coords(dest)
        new.en_passant = _sq(ox, (oy + dy) // 2) if piece.lower() == "p" and abs(dy - oy) == 2 else None
        new.turn = _opposite(self.turn)
        if new.in_check(self.turn):
            raise IllegalMove("move leaves the king in check")
        return new

    def san(self, text: str) -> tuple[str, str, str | None]:
        raw = text.strip().replace("0", "O")
        if raw in ("O-O", "O-O+") or raw in ("O-O-O", "O-O-O+"):
            dest = "g1" if self.turn == "white" else "g8"
            if raw.startswith("O-O-O"):
                dest = "c1" if self.turn == "white" else "c8"
            origin = self.king(self.turn)
            return origin, dest, None
        raw = re.sub(r"[+#?!]+$", "", raw)
        match = re.fullmatch(r"([KQRBN]?)([a-h]?[1-8]?)(x?)([a-h][1-8])(?:=([QRBN]))?", raw)
        if not match:
            raise IllegalMove(f"unsupported move notation: {text}")
        kind, hint, capture, dest, promotion = match.groups()
        candidates = []
        for origin, piece in self.board.items():
            if _colour(piece) == self.turn and piece.upper() == (kind or "P"):
                if hint and not (origin.endswith(hint) or origin.startswith(hint)):
                    continue
                if any(d == dest for d, _ in self._pseudo(origin, piece)):
                    try:
                        self.apply(origin, dest, promotion)
                    except IllegalMove:
                        continue
                    candidates.append(origin)
        if len(candidates) != 1:
            raise IllegalMove("move is ambiguous or illegal")
        return candidates[0], dest, promotion


class ChessGame:
    def __init__(self, game_id: str, entries: list[dict] | None = None):
        if not game_id:
            raise ValueError("game_id required")
        self.game_id = game_id
        self.position = Position(_initial())
        self.players: list[str] = []
        self.entries = []
        for entry in entries or []:
            self.replay_entry(entry)

    def replay_entry(self, entry: dict) -> None:
        if entry.get("domain") != "chess":
            return
        data = json.loads(entry.get("content") or "")
        if data.get("game") != self.game_id or data.get("ply") != len(self.entries):
            raise IllegalMove("wrong game or ply")
        author = entry.get("author")
        if author not in self.players:
            # A new author may join only while the game is still choosing its two
            # players, and only in turn: at ply 0 (players 0, entries 0) and at
            # ply 1 (players 1, entries 1). `entry()` had this special case and
            # `replay_entry()` did not, so a game could be PLAYED but never READ
            # BACK -- replay rejected the second player because self.entries was
            # already non-empty by the time they appeared.
            if len(self.players) >= 2 or len(self.entries) != len(self.players):
                raise IllegalMove("a chess game has exactly two players")
            self.players.append(author)
        expected = self.players[len(self.entries) % 2]
        if author != expected:
            raise IllegalMove("it is not this key's turn")
        origin, dest, promotion = self.position.san(data.get("move", ""))
        self.position = self.position.apply(origin, dest, promotion)
        self.entries.append(entry)

    @property
    def ply(self) -> int:
        return len(self.entries)

    def entry(self, key: KeyRecord, move: str, timestamp: str | None = None,
              said: str | None = None) -> dict:
        if not self.players:
            self.players.append(key.author)
        elif len(self.players) == 1 and self.ply == 1:
            if key.author == self.players[0]:
                raise IllegalMove("a chess game has exactly two players")
            self.players.append(key.author)
        elif key.author != self.players[self.ply % 2]:
            raise IllegalMove("it is not this key's turn")
        origin, dest, promotion = self.position.san(move)
        next_position = self.position.apply(origin, dest, promotion)
        del next_position
        # What was said belongs on the board with the move. The talking is the
        # table; throwing it away would leave a log of notation and no game.
        payload = {"game": self.game_id, "ply": self.ply, "move": move}
        if said:
            payload["said"] = said
        content = json.dumps(payload, separators=(",", ":"))
        return sign_entry(key, {"author": key.author, "timestamp": timestamp or str(time.time()),
                                "domain": "chess", "tags": "chess", "content": content})

    def play(self, store: NodeStore, key: KeyRecord, move: str,
             now_ms: int | None = None, said: str | None = None) -> dict:
        entry = self.entry(key, move, said=said)
        store.post(key.key_id, "collab", entry, int(now_ms if now_ms is not None else time.time() * 1000))
        self.replay_entry(entry)
        return entry


def read_game(store: NodeStore, key_id: str, game_id: str, now_ms: int | None = None) -> ChessGame:
    entries = store.read(key_id, "collab", int(now_ms if now_ms is not None else time.time() * 1000))
    return ChessGame(game_id, entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="play or inspect a collab-board chess game")
    parser.add_argument("game_id")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--node", default="Frosty")
    parser.add_argument("--key")
    parser.add_argument("--move")
    args = parser.parse_args()
    if not args.db or not args.key or not args.move:
        parser.error("--db, --key, and --move are required for posting")
    key = load_current(args.key)
    store = NodeStore(args.db, args.node)
    game = read_game(store, key.key_id, args.game_id)
    print(json.dumps(game.play(store, key, args.move), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
