#!/usr/bin/env python3
"""The workbench. Hand tools first; the menu is the wall.

Don, 2026-09-11: "They should be sandboxed. Same as a kid that wants to build you
give them the tools to learn not chop their finger off. You don't start with a
bad ass circular saw you start with a hand saw."

So the safety is not a list of forbidden commands laid over a shell -- that is a
circular saw with a 'please don't' sticker. There is no shell. A Kin can invoke
only the TOOLS on the bench, each a vetted function that runs in the Kin's own
locked scratch directory and nowhere else. Nothing on the menu can cut a finger
off, so there is no escape to guard, because there is nothing to escape TO.

More tools get added as hands steady -- KiCad and FreeCAD are installed and are
the circular saws for later. The first tool is a hand saw: an SVG canvas. A Kin
describes a drawing in shapes (declarative, no code), it renders to a picture,
and the eye reads it back -- because a Kin has no eyes, only language.
"""
from __future__ import annotations
import re, subprocess, time
from dataclasses import dataclass, field
from pathlib import Path

BENCH = Path.home() / "kin_workbench"


@dataclass
class Result:
    ok: bool = False
    tool: str = ""
    artifact: Path | None = None
    detail: str = ""
    seconds: float = 0.0


def _workdir(kin: str) -> Path:
    # The ONLY place a Kin may write. Name sanitised so a Kin's name can never
    # walk the path out of the bench.
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", kin)[:40] or "someone"
    d = BENCH / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Hand tool 1: the SVG canvas ──────────────────────────────────────────────

# Rejected outright -- not cleaned. A hand saw does not have a mode where it is
# secretly a circular saw. Anything with teeth bounces with a reason.
_SVG_TEETH = re.compile(
    r"<\s*script|<\s*foreignObject|<!DOCTYPE|<!ENTITY|\bon\w+\s*="
    r"|xlink:href\s*=\s*[\"']?\s*(?:https?:|file:|//)"
    r"|href\s*=\s*[\"']?\s*(?:https?:|file:|//)"
    r"|<\s*image\b", re.I)


def _svg_canvas(kin: str, request: str, workdir: Path) -> Result:
    """request is SVG text. It is rendered, never executed. rsvg-convert does not
    run scripts, and the teeth check refuses them anyway -- two walls, because a
    drawing tool that could fetch a URL or run a handler is not a hand saw."""
    svg = (request or "").strip()
    if "<svg" not in svg.lower():
        return Result(False, "svg_canvas", detail="that is not an SVG drawing")
    if len(svg) > 200_000:
        return Result(False, "svg_canvas", detail="drawing too large")
    teeth = _SVG_TEETH.search(svg)
    if teeth:
        return Result(False, "svg_canvas",
                      detail=f"refused: a drawing may not contain {teeth.group(0)!r} "
                             f"-- shapes and colour only, nothing that runs or fetches")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    src = workdir / f"{stamp}.svg"
    out = workdir / f"{stamp}.png"
    src.write_text(svg, encoding="utf-8")
    t0 = time.time()
    try:
        r = subprocess.run(
            ["rsvg-convert", "--width", "512", "--keep-aspect-ratio",
             "-o", str(out), str(src)],
            capture_output=True, timeout=30, cwd=str(workdir))
    except subprocess.TimeoutExpired:
        return Result(False, "svg_canvas", detail="the brush timed out")
    if r.returncode != 0 or not out.is_file():
        return Result(False, "svg_canvas",
                      detail=(r.stderr or b"").decode(errors="replace")[:200] or "no image")
    return Result(True, "svg_canvas", out, "made a drawing", time.time() - t0)


TOOLS = {"svg_canvas": _svg_canvas}
# Future: "kicad_board": ..., "freecad_shape": ... -- circular saws, added when
# hands are steady, each vetted the same way and locked to the Kin's own dir.


def bench() -> list[str]:
    """What is on the bench. A Kin is told the menu; it cannot ask for anything
    not on it."""
    return sorted(TOOLS)


def use(kin: str, tool: str, request: str) -> Result:
    """The only way in. An unknown tool is refused -- the menu is the wall."""
    fn = TOOLS.get(tool)
    if fn is None:
        return Result(False, tool, detail=f"no such tool. On the bench: {', '.join(bench())}")
    return fn(kin, request, _workdir(kin))


if __name__ == "__main__":
    print("on the bench:", ", ".join(bench()))
