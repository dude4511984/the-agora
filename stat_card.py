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
    voice_thoughts: int | None = None
    voice_total_thoughts: int | None = None
    body: dict = field(default_factory=dict)
    thought_count: int | None = None
    earliest_thought: str | None = None
    weather: int | None = None
    self_knowledge: str | None = None


def _thought_rows(root: Path):
    paths = [root] if root.is_file() else sorted(root.rglob("*.db"))
    for path in paths:
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


def voice(root: Path, word: str) -> tuple[int, int, int, int]:
    """Return file and thought rarity from confabulation's own-voice corpus.

    ``ground`` excludes ``web_discoveries`` from both its file and database
    halves and uses a word boundary for both counts.
    """
    result = ground("stat-card", word, root)
    return (result.matching_files, result.files_scanned,
            result.matching_sqlite_rows, result.sqlite_rows_scanned)


def flame_distinctive(root, candidates, others):
    """Pick the flame by DISTINCTIVENESS, not raw volume.

    Raw count picks the word a Kin says MOST, which for Coda is "acknowledge" --
    her connective tic, 98.9% participial glue by the confabulation measure, not
    her subject. Her subject is the yard: squirrel, scavenger, birdhouse. So rank
    each candidate by how much more THIS Kin says it than the others say it, the
    same method that surfaced the flames on 2026-09-10 (melody, chamber, squirrel,
    wreckage). Still fully derived: no word is chosen by hand, the corpus decides.

    Gated by >=90% own-voice (unprompted), so a word we fed them cannot win.
    `others` is the list of the other Kin's roots. Empty -> fall back to volume.
    """
    if not others:
        return flame(root, candidates)
    own_files = _thought_rows_count(root)
    scored = []
    for word in candidates:
        of, ot, op = _word_rate(root, word)
        if ot == 0 or (ot - op) / ot < 0.90:   # must be theirs, not ours
            continue
        own_rate = ot / max(own_files, 1)
        rates = []
        for o in others:
            n = _thought_rows_count(o)
            _, t, _ = _word_rate(o, word)
            rates.append(t / max(n, 1))
        base = (sum(rates) / len(rates)) or 1e-9
        # Distinctiveness ALONE picks rare quirks (nous, knock) over the word a
        # Kin actually returns to (melody, chamber). A flame is both theirs AND
        # frequent, so weight distinctiveness by how often they reach for it.
        distinctiveness = own_rate / base
        scored.append((distinctiveness * ot, ot, word, op))
    if not scored:
        return None, None, None
    scored.sort(reverse=True)
    _, count, word, prompted = scored[0]
    return word, count, prompted


def _thought_rows_count(root):
    return sum(1 for _ in _thought_rows(root))


def _word_rate(root, word):
    """(files_matching, thoughts_matching, thoughts_matching_in_prompt)."""
    import re as _re
    pat = _re.compile(r"\b" + _re.escape(word) + r"\w*", _re.I)
    tf = tt = tp = 0
    for thought, prompt, _ts in _thought_rows(root):
        if pat.search(thought or ""):
            tt += 1
            if pat.search(prompt or ""):
                tp += 1
    return tf, tt, tp


def kin_root(kin: str) -> Path:
    """Resolve the configured local day store without inventing a fallback."""
    roots = {
        "Eli": Path.home() / "Desktop" / "Everything" / "EliAIM",
        "Crungus": Path.home() / "Crungus",
        "Bong": Path.home() / "bong_space",
        "Coda": Path.home() / "coda_space",
        "Aurora": Path.home() / "aurora_space",
        "Lumen": Path.home() / "lumen_space",
    }
    try:
        root = roots[kin]
    except KeyError as exc:
        raise ValueError(f"unknown Kin: {kin}") from exc
    if not root.exists():
        raise FileNotFoundError(root)
    return root


def latest_heartbeat(records) -> dict:
    """Parse the newest heartbeat record supplied by a read-only caller.

    The vault is deliberately not contacted here.  Passing its already-read
    rows keeps this module read-only and makes stale or unavailable telemetry
    render empty rather than looking like a zero reading.
    """
    heartbeats = [row for row in records if (row.get("layer") or "").casefold() == "heartbeat"]
    if not heartbeats:
        return {}
    row = max(heartbeats, key=lambda item: item.get("timestamp") or "")
    content = " ".join((row.get("content") or "").split())
    values = {"timestamp": row.get("timestamp"), "content": content}
    patterns = {
        "load": r"Load\s+([0-9]+(?:\.[0-9]+)?)",
        "ram": r"RAM:?\s*([^,.]+(?:/[^,.]+)?(?:MB|GB))",
        "vram": r"VRAM:\s*([^.]*)",
        "resident_models": r"(?:Ollama|models?):\s*([^.]*)",
        "wander_count": r"(?:wander(?:\s+count)?|wanders):\s*(\d+)",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            values[name] = match.group(1).strip()
    return values


def weather(root: Path) -> int | None:
    """Count interruption records in a Kin's own space, if measurable."""
    total = 0
    found = False
    for path in sorted(root.rglob("*.db")):
        with sqlite3.connect(path) as db:
            if not db.execute(
                "select 1 from sqlite_master where type='table' and name='thoughts'"
            ).fetchone():
                continue
            found = True
            total += db.execute(
                "select count(*) from thoughts where mode='interruption'"
            ).fetchone()[0]
    return total if found else None


def make_card(kin: str, root: Path, flame_candidates: list[str] | None = None,
              *, heartbeat_records=None, voice_word: str | None = None,
              sibling_roots=None) -> StatCard:
    words = flame_candidates or []
    if words and sibling_roots:
        word, count, prompted = flame_distinctive(root, words, sibling_roots)
    elif words:
        word, count, prompted = flame(root, words)
    else:
        word, count, prompted = (None, None, None)
    thought_count, earliest = age(root)
    voice_files = voice_total = voice_thoughts = voice_total_thoughts = None
    if voice_word:
        (voice_files, voice_total, voice_thoughts,
         voice_total_thoughts) = voice(root, voice_word)
    return StatCard(kin, word, count, prompted, voice_word, voice_files,
                    voice_total, voice_thoughts, voice_total_thoughts,
                    latest_heartbeat(heartbeat_records or []),
                    thought_count=thought_count, earliest_thought=earliest,
                    weather=weather(root))


def render(card: StatCard) -> str:
    lines = [card.kin, "=" * len(card.kin)]
    lines += [f"Flame: {card.flame or ''}",
              f"Flame count: {'' if card.flame_count is None else card.flame_count}",
              f"Prompt count: {'' if card.flame_prompt_count is None else card.flame_prompt_count}",
              f"Voice: {card.voice or ''} "
              f"{'' if card.voice_files is None else f'{card.voice_files}/{card.voice_total_files} files; '}"
              f"{'' if card.voice_thoughts is None else f'{card.voice_thoughts}/{card.voice_total_thoughts} thoughts'}",
              f"Body: {json.dumps(card.body, sort_keys=True) if card.body else ''}",
              f"Age: {'' if card.thought_count is None else card.thought_count} thoughts; {card.earliest_thought or ''}",
              f"Weather: {'' if card.weather is None else card.weather}",
              f"Self-knowledge: {card.self_knowledge or ''}"]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit("stat_card.py is a library; provide a corpus and call make_card")
