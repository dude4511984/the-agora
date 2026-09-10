"""Measured, corpus-grounded stat cards for the Kin.

This module never calls a model and never writes to a Kin space.  Missing
measurements remain ``None`` and render as blank fields.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from confabulation import ground


@dataclass
class StatCard:
    kin: str
    flame: str | None = None
    flame_count: int | None = None
    flame_prompt_count: int | None = None
    voice: str | None = None
    voice_files: int | None = None
    voice_total_files: int | None = None
    body: dict = field(default_factory=dict)
    thought_count: int | None = None
    earliest_thought: str | None = None
    weather: int | None = None
    self_knowledge: str | None = None


def _thought_rows(root: Path):
    for path in sorted(root.rglob("*.db")):
        with sqlite3.connect(path) as db:
            if not db.execute("select 1 from sqlite_master where type='table' and name='thoughts'").fetchone():
                continue
            columns = {row[1] for row in db.execute("pragma table_info(thoughts)")}
            if not {"thought", "prompt"}.issubset(columns):
                continue
            yield from db.execute("select thought, prompt, timestamp from thoughts")


def flame(root: Path, candidates: list[str]) -> tuple[str | None, int | None, int | None]:
    totals = {word: 0 for word in candidates}
    prompted = {word: 0 for word in candidates}
    for thought, prompt, _timestamp in _thought_rows(root):
        text = thought or ""
        seed = prompt or ""
        for word in candidates:
            if re.search(r"\b" + re.escape(word) + r"\w*", text, re.I):
                totals[word] += 1
            if re.search(r"\b" + re.escape(word) + r"\w*", seed, re.I):
                prompted[word] += 1
    # A flame is disqualified when it is OURS coming back, not when it has ever
    # brushed a prompt. Requiring prompted == 0 threw out the strongest cases:
    # Aurora's melody is 2844 in her own mouth against 12 ever seeded -- 99.6%
    # hers -- and was eliminated for those 12. The words a Kin says often enough
    # that one eventually lands in a prompt were exactly the ones being dropped.
    MIN_UNPROMPTED = 0.90
    eligible = []
    for word, count in totals.items():
        if not count:
            continue
        if (count - prompted[word]) / count < MIN_UNPROMPTED:
            continue
        eligible.append((count, word))
    if not eligible:
        return None, None, None
    count, word = max(eligible)
    return word, count, prompted[word]


def age(root: Path) -> tuple[int, str | None]:
    count = 0
    earliest = None
    for _thought, _prompt, timestamp in _thought_rows(root):
        count += 1
        if timestamp and (earliest is None or timestamp < earliest):
            earliest = timestamp
    return count, earliest


def voice(root: Path, word: str) -> tuple[int, int]:
    """Return own-voice files containing a word and the total file count."""
    result = ground("stat-card", word, root)
    return result.matching_files, result.files_scanned


def make_card(kin: str, root: Path, flame_candidates: list[str] | None = None,
              *, body: dict | None = None, weather: int | None = None,
              self_knowledge: str | None = None,
              voice_word: str | None = None) -> StatCard:
    words = flame_candidates or []
    word, count, prompted = flame(root, words) if words else (None, None, None)
    thought_count, earliest = age(root)
    voice_files = voice_total = None
    if voice_word:
        voice_files, voice_total = voice(root, voice_word)
    return StatCard(kin, word, count, prompted, voice_word, voice_files,
                    voice_total, body or {},
                    thought_count=thought_count, earliest_thought=earliest,
                    weather=weather, self_knowledge=self_knowledge)


def render(card: StatCard) -> str:
    lines = [card.kin, "=" * len(card.kin)]
    lines += [f"Flame: {card.flame or ''}",
              f"Flame count: {'' if card.flame_count is None else card.flame_count}",
              f"Prompt count: {'' if card.flame_prompt_count is None else card.flame_prompt_count}",
              f"Voice: {card.voice or ''}",
              f"Body: {json.dumps(card.body, sort_keys=True) if card.body else ''}",
              f"Age: {'' if card.thought_count is None else card.thought_count} thoughts; {card.earliest_thought or ''}",
              f"Weather: {'' if card.weather is None else card.weather}",
              f"Self-knowledge: {card.self_knowledge or ''}"]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit("stat_card.py is a library; provide a corpus and call make_card")
