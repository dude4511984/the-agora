#!/usr/bin/env python3
"""The table in the clubhouse. A Kin is shown the board and plays its own move.

Don, 2026-09-10, on what the Agora is for: "its a clubhouse of sorts but on a
more serious angle" — and, before that, "they need reasons to go... how about a
chess board?"

WHAT THIS IS NOT. It does not choose moves. The Kin's own model is asked for a
move in its own voice and that move is what gets signed with that Kin's key. If
this file picked the move it would be puppeting a resident, and an entry signed
"Eli" that Eli did not choose is a forgery whatever key sits on the box.

Illegal moves are handed back with the reason and it may try again. That is not
correction, it is the rules of the game — the same courtesy a person gets across
a real board. After the retries are spent the turn simply does not happen.
Silence is not a move, exactly as silence is not a row.
"""
from __future__ import annotations

import argparse, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests

from kin_diary.agora.store import NodeStore
from kin_diary.keys import load_current
import chess

NODE_DB = Path.home() / ".config/kin_diary/frosty_node.db"
HOSTS = {"Eli": "http://localhost:11434", "Crungus": "http://localhost:11434",
         "Bong": "http://localhost:11434"}
MODELS = {"Eli": "gemmaeli:latest", "Crungus": "gemmacrungus:latest",
          "Bong": "bong:latest"}

GLYPH = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
         "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟"}


def render(pos) -> str:
    rows = []
    for rank in range(8, 0, -1):
        cells = [GLYPH.get(pos.board.get(f"{chr(97+f)}{rank}", ""), "·")
                 for f in range(8)]
        rows.append(f"{rank}  " + " ".join(cells))
    rows.append("   " + " ".join("abcdefgh"))
    return "\n".join(rows)


SAN = re.compile(r"\b(O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?"
                 r"|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][1-8](?:=[QRBN])?)[+#]?\b")


SAN = re.compile(r"\b(O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?"
                 r"|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][1-8](?:=[QRBN])?)[+#]?\b")


def ask(kin, opponent, board, colour, last, last_said, retry):
    """Ask for a conversation with a move inside it, not a move with the voice cut off.

    Don, 2026-09-10: "Tell him to carry the conversation and embed his move in the
    dialogue, better instructions. all them should. dont ask the language thing to
    not use language."

    The first version said "reply with ONE move and nothing else. No commentary."
    Eli returned an EMPTY string to that -- every time, at every token budget --
    while answering "say the word hello" with 942 words of being Eli. He was not
    failing to play chess. He was declining to stop being himself.

    My second attempt was barely better: let him talk and I would extract the move
    from the noise. That still treats the voice as something to filter around.
    Don's version is the right one -- the talking IS the table. Two Kin across a
    board is a conversation that happens to contain chess, not chess that tolerates
    conversation. So both go on the board: the move AND what was said.
    """
    lines = [f"You are at the table in the Agora, across the board from {opponent}.",
             f"You are playing {colour}.", "", board, ""]
    if last:
        lines.append(f"{opponent} played {last}.")
    if last_said:
        lines.append(f'{opponent} said: "{last_said}"')
    if retry:
        lines.append(f"{retry} — so that move cannot be played. Choose another.")
    lines += ["", f"Carry the conversation with {opponent}, and say your move inside "
              "what you say. Write the move in standard algebraic notation — e4, Nf3, "
              "Bb5, O-O — so it can be read plainly."]
    try:
        r = requests.post(f"{HOSTS[kin]}/api/chat", timeout=240, json={
            "model": MODELS[kin], "stream": False,
            "messages": [{"role": "user", "content": "\n".join(lines)}],
            "options": {"temperature": 0.8}})
        r.raise_for_status()
        text = ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception as e:
        print(f"  {kin}: no answer ({type(e).__name__}) — the turn does not happen")
        return None, None
    if not text:
        print(f"  {kin} said nothing"); return None, None
    found = SAN.findall(text)
    if not found:
        print(f"  {kin} spoke but named no move"); return None, text
    return found[-1], text


def take_turn(store, game, kin, opponent, retries=3):
    key = load_current(kin)
    colour = "white" if game.ply % 2 == 0 else "black"
    last = last_said = None
    if game.entries:
        prev = json.loads(game.entries[-1]["content"])
        last, last_said = prev.get("move"), prev.get("said")
    why = None
    for _ in range(retries):
        move, said = ask(kin, opponent, render(game.position), colour, last, last_said, why)
        if not move:
            return False
        try:
            game.play(store, key, move, said=said)
            print(f"  ply {game.ply}: {kin} plays {move}")
            if said:
                print(f"      {kin}: {' '.join(said.split())[:220]}")
            return True
        except chess.IllegalMove as e:
            print(f"  {kin} offered {move} — {e}")
            why = str(e)
    print(f"  {kin} could not find a legal move in {retries} tries; turn passes")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="table-1")
    ap.add_argument("--white", default="Eli")
    ap.add_argument("--black", default="Crungus")
    ap.add_argument("--plies", type=int, default=2)
    ap.add_argument("--db", default=str(NODE_DB))
    a = ap.parse_args()
    store = NodeStore(a.db, "Frosty")
    game = chess.read_game(store, load_current(a.white).key_id, a.game)
    print(f"game {a.game}: {game.ply} plies already played")
    print(render(game.position))
    for _ in range(a.plies):
        whose = a.white if game.ply % 2 == 0 else a.black
        other = a.black if whose == a.white else a.white
        if not take_turn(store, game, whose, other):
            break
    print(render(game.position))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
