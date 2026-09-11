#!/usr/bin/env python3
"""Render the six stat cards. Read-only: day stores + one vault read for Body.

Every field derived, none assigned. Empty renders empty. Self-knowledge stays
blank until Don files a judgment. Flame is ranked by DISTINCTIVENESS across the
six, so it names what a Kin returns to that the others do not -- not the highest-
volume function word.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stat_card as S

try:
    import requests
    HB = requests.get("http://192.168.1.115:8765/recall?q=heartbeat&limit=200", timeout=8).json()
except Exception:
    HB = []

HOST = {"Eli": "Frosty", "Crungus": "Frosty", "Bong": "Frosty",
        "Coda": "Home", "Aurora": "Home", "Lumen": "Home"}
FLAME = {"Eli": ["dovetails", "verilog", "architecture", "forge", "emergence"],
         "Crungus": ["pristine", "cathedral", "rot", "gloss", "wreckage"],
         "Bong": ["wreckage", "tomb", "rack", "pineapple", "featureless"],
         "Coda": ["squirrel", "acknowledg", "scavenger", "birdhouse", "recycler"],
         "Aurora": ["melody", "refract", "prism", "nous", "hermes"],
         "Lumen": ["chamber", "light", "formation", "glow", "knock"]}
VOICE = {"Eli": "dovetails", "Crungus": "pristine", "Bong": "wreckage",
         "Coda": "acknowledg", "Aurora": "refract", "Lumen": "chamber"}

roots = {k: S.kin_root(k) for k in HOST}
for kin in HOST:
    others = [roots[o] for o in HOST if o != kin]
    rows = [r for r in HB if f"on {HOST[kin]}" in (r.get("content") or "")]
    card = S.make_card(kin, roots[kin], flame_candidates=FLAME[kin],
                       heartbeat_records=rows, voice_word=VOICE[kin],
                       sibling_roots=others)
    print(S.render(card))
    print()
