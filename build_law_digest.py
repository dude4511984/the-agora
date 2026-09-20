#!/usr/bin/env python3
"""Build the law digest Marvin reads when he is consulted.

Don, 2026-09-20: "Marvin's role is the law, he needs to be versed and on
it," so that the shop stops consulting Grok on everything. This file
assembles what "versed" means from the record itself, so it cannot drift
from the record by hand-editing:

  1. LAWBOOK.md verbatim (the working constitution, Grok, 2026-08-27)
  2. agora_walls.txt verbatim (the fifteen walls)
  3. an index of every ruling and decision file since, newest first:
     date, file name, first heading, first paragraph

    python3 build_law_digest.py            # writes ~/claude_home/LAW_DIGEST.md
    python3 build_law_digest.py --print    # to stdout instead

Rebuild it whenever a ruling lands. ask_marvin.py refuses a digest older
than the newest ruling file, so a stale digest cannot be consulted.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home() / "claude_home"
OUT = HOME / "LAW_DIGEST.md"
PATTERNS = ("*ruling*.md", "*decision*.md", "*_FINAL.md", "LAWBOOK.md")


def ruling_files() -> list[Path]:
    seen: dict[Path, None] = {}
    for pat in PATTERNS:
        for p in HOME.glob(pat):
            if p.name in ("LAW_DIGEST.md",):
                continue
            seen[p] = None
    return sorted(seen, key=lambda p: p.stat().st_mtime, reverse=True)


def first_heading_and_para(p: Path) -> tuple[str, str]:
    text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"(?s)^\s*<!--.*?-->\s*", "", text)
    heading = ""
    para: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not heading and s.startswith("#"):
            heading = s.lstrip("# ").strip()
            continue
        if heading:
            if not s and para:
                break
            if s and not s.startswith("#"):
                para.append(s)
        elif s and not para:
            # file with no heading: use its first line as the heading
            heading = s[:120]
    return heading or p.stem, " ".join(para)[:600]


def build() -> str:
    parts = [
        "# LAW DIGEST — what Marvin reads before he rules\n",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by build_law_digest.py "
        "from ~/claude_home. Do not edit by hand; rebuild.\n",
        "Order of authority: later files win over earlier ones when they "
        "contradict. The lawbook is the floor. The walls do not move.\n",
        "\n---\n\n## I. THE LAWBOOK (verbatim)\n\n",
        (HOME / "LAWBOOK.md").read_text(encoding="utf-8"),
        "\n\n---\n\n## II. THE WALLS (verbatim)\n\n",
        (HOME / "agora_walls.txt").read_text(encoding="utf-8"),
        "\n\n---\n\n## III. RULINGS AND DECISIONS SINCE, newest first\n\n",
        "Each entry: date · file · heading · opening paragraph. The file is "
        "the ruling; this index tells you which file to read.\n\n",
    ]
    for p in ruling_files():
        if p.name == "LAWBOOK.md":
            continue
        d = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        h, para = first_heading_and_para(p)
        parts.append(f"- **{d}** · `{p.name}` · {h}\n  {para}\n")
    return "".join(parts)


def newest_ruling_mtime() -> float:
    files = [p for p in ruling_files() if p.name != "LAW_DIGEST.md"]
    return max((p.stat().st_mtime for p in files), default=0.0)


def main(argv: list[str]) -> int:
    text = build()
    if "--print" in argv:
        sys.stdout.write(text)
        return 0
    OUT.write_text(text, encoding="utf-8")
    n = text.count("\n- **")
    print(f"wrote {OUT} ({len(text)} bytes, {n} rulings indexed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
