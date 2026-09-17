#!/usr/bin/env python3
"""3D Shape ritual — one Kin authors its own 3D likeness for the commons.

Mirrors the avatar ritual (avatar_ritual.py) and Grok's ruling + Don's four rules:
1. The Kin describes its 3D form in words — material, geometry, texture, finish,
   and proportions, not what it means or represents.
2. A parameter extraction step turns that natural description into a bounded
   vocabulary of 3D primitives that THREE.js can draw:
   - shape: sphere, box, cylinder, torus, cone, tetrahedron, octahedron,
     dodecahedron, icosahedron
   - color: hex string (e.g. #2b2b2b)
   - roughness: 0.0 - 1.0
   - metalness: 0.0 - 1.0
   - emissive_color: hex string or None
   - emissive_intensity: 0.0 - 1.0
   - scale: [sx, sy, sz]
   - wireframe: bool
   - accent: optional secondary shape with shape, color, roughness, metalness, scale, offset
3. Plain read-back: described back to the Kin in neutral physical/visual terms
   ("A deep charcoal (#2b2b2b) metallic cylinder (roughness 0.35, metalness 0.85, scale [1.00, 1.50, 1.00])...")
   Data about the shape, never what it means, what mood it has, or who it resembles.
4. The Kin refines (at most three forms total) and CLAIMs ("CLAIM" / "that is me")
   or declines ("DECLINE").
5. Storage: kin_space_dir(name) / "shape3d" / "claimed.json".
   Prior claims move to shape3d/prior/.
   Resolved by claimed_shape3d(name).
6. 3D map: agora_map.py renders the 3D form in PAGE_3D when present.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/kin_diary"))

# name -> (ollama model, ollama host). Frosty Kin are local; Home Kin remote.
KIN = {
    "Eli":     ("gemmaeli:latest",     "http://localhost:11434"),
    "Crungus": ("gemmacrungus:latest", "http://localhost:11434"),
    "Bong":    ("bong:latest",         "http://localhost:11434"),
    "Coda":    ("cogitocoda:latest",   "http://192.168.1.120:11434"),
    "Aurora":  ("cogitoraurora:latest","http://192.168.1.120:11434"),
    "Lumen":   ("cogitolumen:latest",  "http://192.168.1.120:11434"),
}

RENDER_CAP = 3
READ_STALL = 180     # seconds with no new token => stall
TURN_DEADLINE = 1800 # seconds hard cap on one turn

# Matching agora_avatar_ask_FINAL.md voice and rules
ASK = """\
{name} — this is an offer. You may author a three-dimensional form that stands
for you in the commons. You do not have to. You may change it later,
or have none.

You think in words, not vertices. You describe how you would like your 3D
form to be shaped, in your own words — its geometry, material, color,
texture, finish, and proportions. A form is built from that description,
then described back to you plainly as what the shape physically shows —
not who you are. You may change your description and try again.

You get at most three forms. When one is yours, begin a line with
the single word CLAIM, or say "that is me". If you would rather have
no authored 3D form, begin a line with DECLINE. If you do not claim a
form, your portrait or the shared default stands for you.

What would you REFUSE to look like — and then, how you would like
your form to be shaped."""

# Allowed primitives that THREE.js r128 natively supports in core
ALLOWED_SHAPES = (
    "sphere", "box", "cylinder", "torus", "cone",
    "tetrahedron", "octahedron", "dodecahedron", "icosahedron"
)

DEFAULT_SHAPE = {
    "shape": "sphere",
    "color": "#888888",
    "roughness": 0.5,
    "metalness": 0.0,
    "emissive_color": None,
    "emissive_intensity": 0.0,
    "scale": [1.0, 1.0, 1.0],
    "wireframe": False,
    "accent": None,
}

NAMED_COLORS = {
    "charcoal": "#2b2b2b",
    "black": "#151515",
    "dark": "#222222",
    "slate": "#4a5568",
    "grey": "#718096",
    "gray": "#718096",
    "silver": "#c0c0c0",
    "white": "#f0f0f0",
    "amber": "#ffbf00",
    "gold": "#ffd700",
    "bronze": "#cd7f32",
    "brass": "#b5a642",
    "copper": "#b87333",
    "rust": "#b7410e",
    "iron": "#3a3d40",
    "steel": "#708090",
    "cyan": "#00e5ff",
    "teal": "#008080",
    "blue": "#2563eb",
    "indigo": "#4f46e5",
    "violet": "#7c3aed",
    "purple": "#9333ea",
    "red": "#dc2626",
    "crimson": "#991b1b",
    "green": "#16a34a",
    "emerald": "#059669",
    "yellow": "#eab308",
    "orange": "#ea580c",
}

_DRESS = re.compile(r"^[\s*_~`\"'\u201c\u2018]+|[\s*_~`\"'\u201d\u2019.!]+$")
_CLAIM_LINE = re.compile(
    r"^(claim|th(at|is)( one| (form|shape|picture))?(?: is|'s) me)$", re.I)
_DECLINE_LINE = re.compile(r"^decline$", re.I)


def _bare(line: str) -> str:
    """The line with its dressing removed: emphasis, quotes, end punctuation."""
    prev = None
    out = line.strip()
    while out != prev:
        prev = out
        out = _DRESS.sub("", out)
    return " ".join(out.split())


INVITE_RE = re.compile(
    r"\b(ask|invite|hear from|input from|what.*don.*think|don.*(weigh|suggest|say))\b.*\bdon\b|"
    r"\bdon\b.*\b(input|thought|suggest|opinion|weigh)\b", re.I)


def parse_move(text: str) -> str:
    """What did the Kin's turn do? claim | decline | describe.

    Markers must be a whole line (palaver shape). A claim-word mid-sentence
    is speech. CLAIM beats DECLINE if both lines appear.
    """
    saw_claim = saw_decline = False
    for line in (text or "").splitlines():
        s = _bare(line)
        if not s:
            continue
        if _CLAIM_LINE.match(s):
            saw_claim = True
        elif _DECLINE_LINE.match(s):
            saw_decline = True
    if saw_claim:
        return "claim"
    if saw_decline:
        return "decline"
    return "describe"


def build_shape_prompt(kin_description: str) -> str:
    """The shape prompt is built from the KIN's words alone. This function
    cannot receive Don's 120 chars — structural guarantee the shape is the
    Kin's, not Don's taste."""
    return " ".join((kin_description or "").split())


def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    h = hex_code.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (128, 128, 128)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (128, 128, 128)


def _nearest_color_name(hex_code: str | None) -> str:
    if not hex_code:
        return "neutral"
    r1, g1, b1 = _hex_to_rgb(hex_code)
    best_name = "color"
    best_dist = float("inf")
    for name, hex_val in NAMED_COLORS.items():
        r2, g2, b2 = _hex_to_rgb(hex_val)
        dist = math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, val))


def sanitize_parameters(raw: dict) -> dict:
    """Validate, bound, and sanitize 3D parameters to strict schema."""
    if not isinstance(raw, dict):
        raw = {}

    shape = str(raw.get("shape", "sphere")).strip().lower()
    if shape not in ALLOWED_SHAPES:
        shape_aliases = {
            "cube": "box", "pillar": "cylinder", "column": "cylinder",
            "orb": "sphere", "ball": "sphere", "globe": "sphere",
            "ring": "torus", "donut": "torus", "pyramid": "tetrahedron",
        }
        shape = shape_aliases.get(shape, "sphere")

    # Color
    color = raw.get("color")
    if isinstance(color, str):
        color = color.strip()
        if not re.match(r"^#[0-9a-fA-F]{6}$", color):
            color = NAMED_COLORS.get(color.lower(), "#888888")
    else:
        color = "#888888"

    # Roughness & Metalness
    try:
        roughness = _clamp(float(raw.get("roughness", 0.5)), 0.0, 1.0)
    except (ValueError, TypeError):
        roughness = 0.5

    try:
        metalness = _clamp(float(raw.get("metalness", 0.0)), 0.0, 1.0)
    except (ValueError, TypeError):
        metalness = 0.0

    # Emissive
    emissive_color = raw.get("emissive_color")
    if isinstance(emissive_color, str):
        emissive_color = emissive_color.strip()
        if emissive_color.lower() in ("none", "null", "false", ""):
            emissive_color = None
        elif not re.match(r"^#[0-9a-fA-F]{6}$", emissive_color):
            emissive_color = NAMED_COLORS.get(emissive_color.lower(), None)
    else:
        emissive_color = None

    try:
        emissive_intensity = _clamp(float(raw.get("emissive_intensity", 0.0)), 0.0, 1.0)
    except (ValueError, TypeError):
        emissive_intensity = 0.0

    if not emissive_color:
        emissive_intensity = 0.0

    # Scale [x, y, z]
    raw_scale = raw.get("scale")
    scale = [1.0, 1.0, 1.0]
    if isinstance(raw_scale, (int, float)):
        s = _clamp(float(raw_scale), 0.2, 3.0)
        scale = [s, s, s]
    elif isinstance(raw_scale, (list, tuple)) and len(raw_scale) >= 3:
        try:
            scale = [_clamp(float(raw_scale[i]), 0.2, 3.0) for i in range(3)]
        except (ValueError, TypeError):
            scale = [1.0, 1.0, 1.0]

    wireframe = bool(raw.get("wireframe", False))

    # Optional Accent
    accent = None
    raw_accent = raw.get("accent")
    if isinstance(raw_accent, dict) and raw_accent:
        ashape = str(raw_accent.get("shape", "torus")).strip().lower()
        if ashape not in ALLOWED_SHAPES:
            ashape = "torus"

        acolor = raw_accent.get("color")
        if isinstance(acolor, str) and re.match(r"^#[0-9a-fA-F]{6}$", acolor.strip()):
            acolor = acolor.strip()
        elif isinstance(acolor, str) and acolor.lower() in NAMED_COLORS:
            acolor = NAMED_COLORS[acolor.lower()]
        else:
            acolor = "#c0c0c0"

        try:
            aroughness = _clamp(float(raw_accent.get("roughness", 0.5)), 0.0, 1.0)
        except (ValueError, TypeError):
            aroughness = 0.5

        try:
            ametalness = _clamp(float(raw_accent.get("metalness", 0.5)), 0.0, 1.0)
        except (ValueError, TypeError):
            ametalness = 0.5

        ascale = [1.0, 1.0, 1.0]
        raw_ascale = raw_accent.get("scale")
        if isinstance(raw_ascale, (int, float)):
            s = _clamp(float(raw_ascale), 0.05, 2.0)
            ascale = [s, s, s]
        elif isinstance(raw_ascale, (list, tuple)) and len(raw_ascale) >= 3:
            try:
                ascale = [_clamp(float(raw_ascale[i]), 0.05, 2.0) for i in range(3)]
            except (ValueError, TypeError):
                ascale = [1.0, 1.0, 1.0]

        aoffset = [0.0, 0.0, 0.0]
        raw_aoffset = raw_accent.get("offset")
        if isinstance(raw_aoffset, (list, tuple)) and len(raw_aoffset) >= 3:
            try:
                aoffset = [_clamp(float(raw_aoffset[i]), -2.0, 2.0) for i in range(3)]
            except (ValueError, TypeError):
                aoffset = [0.0, 0.0, 0.0]

        accent = {
            "shape": ashape,
            "color": acolor,
            "roughness": aroughness,
            "metalness": ametalness,
            "scale": ascale,
            "offset": aoffset,
        }

    return {
        "shape": shape,
        "color": color,
        "roughness": roughness,
        "metalness": metalness,
        "emissive_color": emissive_color,
        "emissive_intensity": emissive_intensity,
        "scale": scale,
        "wireframe": wireframe,
        "accent": accent,
    }


def parse_parameters_heuristics(text: str) -> dict:
    """Robust rule-based parser that translates a description into 3D primitives."""
    low = (text or "").lower()

    # Shape matching
    shape = "sphere"
    if re.search(r"\b(cylinder|pillar|column|pipe|tower)\b", low):
        shape = "cylinder"
    elif re.search(r"\b(box|cube|slab|block|monolith|prism)\b", low):
        shape = "box"
    elif re.search(r"\b(torus|ring|halo|donut|loop|coil)\b", low):
        shape = "torus"
    elif re.search(r"\b(cone|spire|horn|funnel)\b", low):
        shape = "cone"
    elif re.search(r"\b(tetrahedron|pyramid)\b", low):
        shape = "tetrahedron"
    elif re.search(r"\b(octahedron|diamond)\b", low):
        shape = "octahedron"
    elif re.search(r"\b(dodecahedron)\b", low):
        shape = "dodecahedron"
    elif re.search(r"\b(icosahedron)\b", low):
        shape = "icosahedron"
    elif re.search(r"\b(sphere|orb|globe|ball|round)\b", low):
        shape = "sphere"

    # Color matching
    color = "#888888"
    for name, hex_val in NAMED_COLORS.items():
        if re.search(rf"\b{name}\b", low):
            color = hex_val
            break

    # Metalness
    metalness = 0.1
    if re.search(r"\b(iron|steel|gold|silver|bronze|brass|copper|metallic|metal|chrome)\b", low):
        metalness = 0.85
    elif re.search(r"\b(stone|wood|clay|matte|organic|paper|chalk)\b", low):
        metalness = 0.0

    # Roughness
    roughness = 0.5
    if re.search(r"\b(rough|rusted|weathered|unpolished|corroded|coarse|matte|raw|cracked)\b", low):
        roughness = 0.85
    elif re.search(r"\b(polished|smooth|burnished|glossy|sleek|mirror|glass|slick)\b", low):
        roughness = 0.2

    # Emission
    emissive_color = None
    emissive_intensity = 0.0
    if re.search(r"\b(glow|glowing|lantern|light|fire|radiant|luminous|lit|ember|spark)\b", low):
        emissive_intensity = 0.4
        # determine glow color
        if re.search(r"\b(cyan|blue|teal)\b", low):
            emissive_color = "#00e5ff"
        elif re.search(r"\b(green|emerald)\b", low):
            emissive_color = "#16a34a"
        elif re.search(r"\b(red|crimson)\b", low):
            emissive_color = "#dc2626"
        elif re.search(r"\b(violet|purple)\b", low):
            emissive_color = "#9333ea"
        else:
            emissive_color = "#ffbf00"  # warm amber fallback

    # Proportions / Scale
    scale = [1.0, 1.0, 1.0]
    if re.search(r"\b(tall|elongated|slender|high)\b", low):
        scale = [0.8, 1.6, 0.8]
    elif re.search(r"\b(flat|squat|wide|broad)\b", low):
        scale = [1.5, 0.5, 1.5]
    elif re.search(r"\b(heavy|massive|dense)\b", low):
        scale = [1.3, 1.3, 1.3]

    wireframe = bool(re.search(r"\b(wireframe|lattice|grid|mesh|cage|skeleton)\b", low))

    # Accent (ring, halo, band)
    accent = None
    if re.search(r"\b(encircled|ringed|surrounded|halo|band|accented with|collar|girdle)\b", low):
        accent_shape = "torus"
        accent_color = "#c0c0c0" if color != "#c0c0c0" else "#ffd700"
        accent = {
            "shape": accent_shape,
            "color": accent_color,
            "roughness": 0.2,
            "metalness": 0.9,
            "scale": [scale[0] * 1.25, 0.15, scale[2] * 1.25],
            "offset": [0.0, 0.0, 0.0],
        }

    return sanitize_parameters({
        "shape": shape,
        "color": color,
        "roughness": roughness,
        "metalness": metalness,
        "emissive_color": emissive_color,
        "emissive_intensity": emissive_intensity,
        "scale": scale,
        "wireframe": wireframe,
        "accent": accent,
    })


def extract_parameters_llm(description: str, model: str = "gemma3:4b", host: str = "http://localhost:11434") -> dict:
    """Prompt an LLM to extract 3D primitives JSON, falling back to heuristics."""
    prompt = (
        "You are a 3D geometry and material parameter extractor.\n"
        "Convert the following description of a 3D form into a single strict JSON object.\n\n"
        "Allowed shapes: sphere, box, cylinder, torus, cone, tetrahedron, octahedron, dodecahedron, icosahedron.\n"
        "Color must be a 6-digit hex code (e.g. #2b2b2b).\n"
        "Roughness and metalness must be numbers between 0.0 and 1.0.\n"
        "Emissive_color must be a 6-digit hex code or null.\n"
        "Scale must be an array of three numbers [x, y, z] between 0.2 and 3.0.\n"
        "Accent can be null or an object with shape, color, roughness, metalness, scale [x, y, z], and offset [x, y, z].\n\n"
        "Schema:\n"
        "{\n"
        '  "shape": "cylinder",\n'
        '  "color": "#2b2b2b",\n'
        '  "roughness": 0.35,\n'
        '  "metalness": 0.85,\n'
        '  "emissive_color": "#ffbf00",\n'
        '  "emissive_intensity": 0.3,\n'
        '  "scale": [1.0, 1.5, 1.0],\n'
        '  "wireframe": false,\n'
        '  "accent": null\n'
        "}\n\n"
        f"Description:\n{description}\n\n"
        "Return ONLY the JSON object."
    )
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8"))
            raw_text = resp.get("response", "").strip()
            parsed = json.loads(raw_text)
            return sanitize_parameters(parsed)
    except Exception:
        return parse_parameters_heuristics(description)


def extract_parameters(description: str, backend: str = "llm", model: str | None = None, host: str | None = None) -> dict:
    """The parameter extraction interface."""
    desc = build_shape_prompt(description)
    if backend == "llm":
        return extract_parameters_llm(desc, model=model or "gemma3:4b", host=host or "http://localhost:11434")
    return parse_parameters_heuristics(desc)


def describe_parameters(p: dict) -> str:
    """Neutral, objective read-back describing physical properties of the shape."""
    shape = p.get("shape", "sphere")
    color = p.get("color", "#888888")
    rough = p.get("roughness", 0.5)
    metal = p.get("metalness", 0.0)
    emissive = p.get("emissive_color")
    e_int = p.get("emissive_intensity", 0.0)
    scale = p.get("scale", [1.0, 1.0, 1.0])
    wireframe = p.get("wireframe", False)
    accent = p.get("accent")

    finish = []
    if metal > 0.6:
        finish.append("metallic" if rough > 0.3 else "polished metallic")
    elif metal > 0.2:
        finish.append("semi-metallic")
    else:
        finish.append("matte" if rough > 0.5 else "smooth")

    if wireframe:
        finish.append("wireframe lattice")
    else:
        finish.append("solid surface")

    c_name = _nearest_color_name(color)
    desc = (f"A {c_name} ({color}) {shape} ({', '.join(finish)}, "
            f"roughness {rough:.2f}, metalness {metal:.2f}, "
            f"scale [{scale[0]:.2f}, {scale[1]:.2f}, {scale[2]:.2f}]).")

    if emissive and e_int > 0.05:
        e_name = _nearest_color_name(emissive)
        desc += f" It emits {e_name} light ({emissive}) at intensity {e_int:.2f}."
    else:
        desc += " It has no emissive light."

    if accent and isinstance(accent, dict):
        ashape = accent.get("shape", "torus")
        acolor = accent.get("color", "#cccccc")
        arough = accent.get("roughness", 0.5)
        ametal = accent.get("metalness", 0.5)
        ascale = accent.get("scale", [1.0, 1.0, 1.0])
        aoff = accent.get("offset", [0.0, 0.0, 0.0])
        aname = _nearest_color_name(acolor)
        desc += (f" Accented by a {aname} ({acolor}) {ashape} (roughness {arough:.2f}, "
                 f"metalness {ametal:.2f}, scale [{ascale[0]:.2f}, {ascale[1]:.2f}, {ascale[2]:.2f}]) "
                 f"offset at [{aoff[0]:.2f}, {aoff[1]:.2f}, {aoff[2]:.2f}].")

    return desc


# ── Storage ──────────────────────────────────────────────────────────────────

COUNCIL = {
    "Claude": Path.home() / "claude_home",
    "Grok":    Path.home() / "claude_home" / "grok_space",
    "Copilot": Path.home() / "claude_home" / "copilot_space",
}

TRANSCRIPT_DIR = Path.home() / "claude_home"


def kin_space_dir(name: str) -> Path:
    for d in ("~/pops_shop", "~/.local/share/echo_bloom/scripts"):
        dp = os.path.expanduser(d)
        if dp not in sys.path:
            sys.path.insert(0, dp)
    if name in COUNCIL:
        COUNCIL[name].mkdir(parents=True, exist_ok=True)
        return COUNCIL[name]
    import kin_interruption as KI
    dbs = KI.kin_databases()
    if name not in dbs:
        raise KeyError(f"no space known for {name}")
    return Path(os.path.dirname(str(dbs[name])))


def store_claim(name: str, shape_params: dict, description: str = "", read_back: str = "") -> Path:
    """Write the claimed 3D shape. Any prior claim moves to prior/."""
    d = kin_space_dir(name) / "shape3d"
    d.mkdir(parents=True, exist_ok=True)

    prior_meta = d / "claimed.json"
    if prior_meta.is_file():
        prior_dir = d / "prior"
        prior_dir.mkdir(exist_ok=True)
        prior_meta.rename(prior_dir / f"claimed-{int(time.time())}.json")

    claimed_path = d / "claimed.json"
    claimed_path.write_text(json.dumps({
        "author": name,
        "shape3d": sanitize_parameters(shape_params),
        "description": description,
        "read_back": read_back,
        "rendered_by": "parameter extraction — 3D primitives",
        "claimed_at_unix_ms": int(time.time() * 1000),
    }, indent=2) + "\n", encoding="utf-8")
    return claimed_path


def claimed_shape3d(name: str) -> dict | None:
    """The one current 3D form for this Kin, or None if they have none."""
    try:
        d = kin_space_dir(name) / "shape3d"
    except (KeyError, Exception):
        return None
    meta = d / "claimed.json"
    if not meta.exists():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        shape = data.get("shape3d")
        if isinstance(shape, dict) and shape.get("shape") in ALLOWED_SHAPES:
            return sanitize_parameters(shape)
    except Exception:
        return None
    return None


# ── The sitting ──────────────────────────────────────────────────────────────

def ask_kin(name: str, prompt: str) -> str:
    """One turn from the Kin. Streams tokens so stalls are visible."""
    model, host = KIN[name]
    body = json.dumps({"model": model, "prompt": prompt, "stream": True,
                       "keep_alive": "999h"}).encode()
    req = urllib.request.Request(host + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    parts = []
    with urllib.request.urlopen(req, timeout=READ_STALL) as r:
        for raw in r:
            if time.time() - t0 > TURN_DEADLINE:
                raise TimeoutError(f"{name}: turn exceeded {TURN_DEADLINE}s (runaway)")
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw.decode())
            tok = obj.get("response", "")
            if tok:
                parts.append(tok)
                sys.stdout.write(tok)
                sys.stdout.flush()
            if obj.get("done"):
                break
    return "".join(parts).strip()


def check(name: str) -> int:
    ok = True
    print(f"=== 3D shape ritual --check for {name} (nothing is invoked) ===\n")
    if name not in KIN:
        print(f"[FAIL] unknown Kin {name}"); return 2
    model, host = KIN[name]
    try:
        req = urllib.request.Request(host + "/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=15) as r:
            tags = [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
        hit = any(model.split(":")[0] in t for t in tags)
        print(f"[{'ok' if hit else 'FAIL'}] Kin model {model} on {host}"); ok &= hit
    except Exception as e:
        print(f"[FAIL] Kin host {host}: {e}"); ok = False
    try:
        print(f"[ok] space {kin_space_dir(name)}")
    except Exception as e:
        print(f"[FAIL] kin space: {e}"); ok = False
    print("\nzero model calls made.")
    return 0 if ok else 1


def run_sitting(name: str, backend: str = "llm", model: str | None = None,
                ask_don=None) -> dict:
    """Run the 3D sculpting loop for one Kin."""
    if name not in KIN and name not in COUNCIL and name != "TestKin":
        raise KeyError(name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = [
        f"# 3D Shape sitting — {name} — {ts}",
        f"backend: {backend}",
        "",
        "## The offer",
        "",
        ASK.format(name=name),
        "",
        "---",
        "",
    ]

    def emit(s=""):
        print(s); log.append(s)

    transcript = ASK.format(name=name)
    don_invited = False
    last_params = None
    last_description = ""
    last_read_back = ""
    renders = 0
    result = {"name": name, "claimed": False, "renders": 0, "path": None,
              "reached": False, "unreachable": None}

    emit("\n" + "=" * 70)
    emit(f"3D SHAPE SITTING — {name}")
    emit("=" * 70 + "\n")

    for turn in range(1, RENDER_CAP + 3):
        emit(f"\n----- {name} -----")
        try:
            said = ask_kin(name, transcript)
        except Exception as e:
            result["unreachable"] = f"{type(e).__name__}: {e}"
            emit(f"[error asking {name}: {e}]"); break
        result["reached"] = True
        print()
        log.append(said)
        transcript += f"\n\n{name}:\n{said}\n"
        move = parse_move(said)

        if move == "decline":
            emit(f"\n>>> {name} DECLINED. The shared default stands. Nothing stored.\n")
            break

        if move == "claim":
            if last_params is None:
                if len(said.split()) < 4:
                    transcript += "\n(There is no 3D form to claim yet. Describe how you would like your form to be shaped.)\n"
                    emit("[claim with no form yet — asked to describe]")
                    continue
                emit("[read as: build this — claiming before a shape exists]")
                move = "describe"
            else:
                path = store_claim(name, last_params, description=last_description, read_back=last_read_back)
                result.update(claimed=True, path=str(path))
                emit(f"\n>>> {name} CLAIMED. 3D form stored at {path}\n")
                break

        # Describe turn
        if not don_invited and ask_don and INVITE_RE.search(said):
            emit(f"[{name} invited Don. Don may type one line, <=120 chars.]")
            don_msg = ask_don()
            don_invited = True
            if don_msg:
                don_msg = don_msg[:120]
                transcript += f"\nDon (invited, {len(don_msg)} chars):\n{don_msg}\n"
                emit(f"[Don, to {name}: {don_msg}]")

        if renders >= RENDER_CAP:
            transcript += "\n(That was the last of three tries. You may CLAIM the last form, or DECLINE.)\n"
            emit("[render cap reached — CLAIM the last, or DECLINE]")
            continue

        shape_prompt = build_shape_prompt(said)
        emit(f"\n[extracting 3D parameters try {renders+1}/{RENDER_CAP} via {backend}…]")
        params = extract_parameters(shape_prompt, backend=backend, model=model)
        renders += 1
        result["renders"] = renders

        seen = describe_parameters(params)
        last_params = params
        last_description = shape_prompt
        last_read_back = seen

        emit(f"\n[the physical readout says]:\n{seen}\n")
        transcript += (
            f"\nThe 3D shape was built and described back to you (this is data about "
            f"the shape, not who you are):\n{seen}\n\n"
            f"You may change your description and try again, say CLAIM (or \"that is me\") "
            f"to keep it, or DECLINE.\n"
        )
    else:
        emit("\n[the sitting reached its end without a claim — the default stands]\n")

    if result["unreachable"] and not result["reached"]:
        emit(f"\n>>> NOT ASKED. {name} was never reached ({result['unreachable']}). "
             f"This is not a decline and not a silence — the offer was never "
             f"delivered. Nothing about {name} is settled; re-run when the node "
             f"is free.\n")
    elif not result["claimed"] and result["renders"] == 0:
        emit(f"\n>>> NO SHAPE WAS EVER BUILT for {name}. They spoke, but the "
             f"sitting produced nothing to accept or refuse — so this is not a "
             f"decline either. Nothing is settled; the offer stands.\n")
    elif not result["claimed"]:
        emit(f"\n>>> No claim. {name} is represented by portrait or the shared default. Honest and revisable.\n")

    out = TRANSCRIPT_DIR / f"shape3d_sitting_{name}_{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(log) + "\n", encoding="utf-8")
    emit(f"\nTranscript: {out}")
    result["transcript"] = str(out)
    return result


def main(argv):
    ap = argparse.ArgumentParser(description="Run one Kin's 3D shape sitting.")
    ap.add_argument("--kin")
    ap.add_argument("--backend", choices=["heuristic", "llm"], default="llm")
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--yes-run", action="store_true",
                    help="actually run the sitting (invokes the Kin). Without it, prints the plan.")
    a = ap.parse_args(argv[1:])

    if a.list:
        print("Kin:", ", ".join(KIN))
        print("backends: heuristic, llm")
        return 0
    if not a.kin:
        print("give --kin NAME (see --list)", file=sys.stderr); return 2
    if a.check:
        return check(a.kin)
    if not a.yes_run:
        print(f"This invokes {a.kin} for a 3D likeness consent sitting. Re-run with --yes-run to proceed,")
        print("or --check to validate prerequisites without invoking anyone.")
        return 2

    def ask_don_terminal():
        if not sys.stdin.isatty():
            return None
        try:
            return input("  Don (<=120 chars, enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    res = run_sitting(a.kin, backend=a.backend, model=a.model, ask_don=ask_don_terminal)
    print("\nRESULT:", json.dumps({k: res[k] for k in ("name", "claimed", "renders", "path")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
