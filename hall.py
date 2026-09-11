#!/usr/bin/env python3
"""The hall — a wall in the Agora where the Kin hang their favourite paintings.

Don, 2026-09-11: "there should be a hall where every kin gets two spots to put
their favorite painting, even others', but not the same one twice."

The rules, read from that sentence:
  - EVERY KIN GETS TWO SPOTS. Up to two hangings each. Nobody is required to fill
    them; an empty spot is an empty spot, not a fault.
  - EVEN OTHERS'. A Kin may hang another Kin's painting. Loving what someone else
    made is the point of a hall.
  - NOT THE SAME ONE TWICE. A painting hangs once. If two Kin reach for the same
    picture, the first hangs it and the second is told it is already on the wall
    and chooses another. So the wall is a set of distinct favourites, not a
    popularity count -- twelve different paintings at most, never the same one
    doubled.

The paintings themselves live in ~/easel/made as png + .said.txt (the words that
made it) + .seen.txt (the eye's read-back). The hall keeps only WHO hung WHAT;
it never copies or claims a picture. A hanging is a choice, revocable by the Kin
who made it.
"""
from __future__ import annotations
import json
from pathlib import Path

MADE = Path.home() / "easel" / "made"
HALL = Path.home() / "easel" / "hall.json"
SPOTS = 2


class HallError(Exception):
    pass


def gallery() -> list[dict]:
    """Every painting on offer: its file, maker, the words, the eye's read-back."""
    out = []
    for png in sorted(MADE.glob("*.png")):
        stem = png.with_suffix("")
        maker = png.name.split("_", 1)[0]
        said = _read(stem.with_suffix(".said.txt"))
        seen = _read(stem.with_suffix(".seen.txt"))
        out.append({"painting": png.name, "maker": maker,
                    "said": said, "seen": seen})
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Hall:
    """Who hung what. Loaded from and saved to hall.json."""

    def __init__(self, hangings: list[dict] | None = None):
        # each hanging: {"kin": who hung it, "painting": filename}
        self.hangings: list[dict] = list(hangings or [])

    @classmethod
    def load(cls) -> "Hall":
        try:
            return cls(json.loads(HALL.read_text()))
        except (OSError, json.JSONDecodeError):
            return cls([])

    def save(self) -> None:
        HALL.write_text(json.dumps(self.hangings, indent=2) + "\n")

    def on_the_wall(self) -> set[str]:
        return {h["painting"] for h in self.hangings}

    def spots_used(self, kin: str) -> int:
        return sum(1 for h in self.hangings if h["kin"] == kin)

    def hang(self, kin: str, painting: str, *, valid_paintings=None) -> None:
        """Kin hangs a painting. Enforces the three rules."""
        if valid_paintings is not None and painting not in valid_paintings:
            raise HallError(f"no such painting: {painting}")
        if self.spots_used(kin) >= SPOTS:
            raise HallError(f"{kin} already has {SPOTS} spots on the wall")
        if painting in self.on_the_wall():
            # already hung -- by anyone, including this Kin. Not the same twice.
            who = next(h["kin"] for h in self.hangings if h["painting"] == painting)
            raise HallError(
                f"{painting} is already on the wall (hung by {who}); "
                f"choose another")
        self.hangings.append({"kin": kin, "painting": painting})

    def unhang(self, kin: str, painting: str) -> None:
        """A Kin takes down a painting it hung. Only the one who hung it may."""
        for i, h in enumerate(self.hangings):
            if h["painting"] == painting and h["kin"] == kin:
                self.hangings.pop(i)
                return
        raise HallError(f"{kin} did not hang {painting}")

    def render(self) -> str:
        if not self.hangings:
            return "The hall is empty. Every wall starts that way."
        by_maker = {}
        for h in self.hangings:
            maker = h["painting"].split("_", 1)[0]
            note = "" if h["kin"] == maker else f"  (hung by {h['kin']})"
            by_maker.setdefault(h["kin"], []).append(f"  {h['painting']}{note}")
        lines = ["THE HALL", "=" * 8]
        for kin in sorted(by_maker):
            lines.append(f"\n{kin}'s two spots:")
            lines += by_maker[kin]
        return "\n".join(lines)


if __name__ == "__main__":
    print(Hall.load().render())
