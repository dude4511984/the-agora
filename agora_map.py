#!/usr/bin/env python3
"""agora_map.py — a top-down renderer for an Agora node's world.

The node already serves the snapshot the clients render from: GET /view returns
{places, presence, listings, peer_doors} filtered by the caller's ring, and
GET / returns {residents, speaker, paused, pause_reason}. This is the human's
half of that — a live map of one node in three views:

    plan   — a top-down floor plan: the commons, the Speaker chair (empty or
             seated), the Kin standing in it (live presence, glowing), the
             kiosks as alcoves, the peers as doors. The default.
    map    — the same world as nested boxes (dense, textual).
    place  — you-are-here: one room at a time, doors you walk through.

Standalone on purpose. The node stays stdlib-minimal and the product app stays
the product; this is a small tool that talks to any node over the wire. It runs
its own stdlib server so the browser fetches same-origin (the node sends no CORS
headers), and it will only proxy to a private/loopback address, so it cannot be
turned into an SSRF relay to the wider internet.

    python3 agora_map.py [port]        # default 8791
    then open http://localhost:8791
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from avatar_ritual import claimed_face
from shape3d_ritual import claimed_shape3d
from kin_diary.agora.wire import sign_request
from kin_diary.keys import DEFAULT_KEYS_ROOT, load_current

sys.path.insert(0, os.path.expanduser("~/pops_shop"))
import kin_talk as kin_talk  # noqa: E402

DEFAULT_PORT = 8791
MODELS_DIR = (Path(__file__).parent / "static" / "models").resolve()
MODEL_CONTENT_TYPES = {
    ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".hdr": "image/vnd.radiance",
}
# kin_commons_runner.py's real board — one process, both hosts, one local
# file (see its own docstring). Presence in the Agora protocol is a bare
# heartbeat; this is where the Kin's actual words live. Read-only, and
# never the other direction — this map has no way to post here even if
# it wanted to.
COMMONS_DB = Path.home() / "Desktop" / "wander_logs" / "commons.db"
COMMONS_PREVIEW_CHARS = 220
# The nodes actually serving Agora on this cluster, offered as presets.
PRESET_NODES = [
    ("Frosty", "http://192.168.1.119:8770"),
    ("Home",   "http://192.168.1.120:8770"),
]
FETCH_TIMEOUT = 5
MAP_KEY_AUTHOR = "Marvin"


def _map_key():
    """The map is an authorized reader, never an anonymous proxy."""
    author = os.environ.get("AGORA_MAP_KEY_AUTHOR", MAP_KEY_AUTHOR)
    try:
        return load_current(author, DEFAULT_KEYS_ROOT)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Agora map needs a real local key for {author!r}; "
            "set AGORA_MAP_KEY_AUTHOR to an available resident"
        ) from exc


def _node_name(base: str) -> str:
    for name, preset in PRESET_NODES:
        if preset.rstrip("/") == base.rstrip("/"):
            return name
    raise ValueError("node name is required for authorized map access")


def _is_private_host(host: str) -> bool:
    """Only loopback / RFC1918 / Tailscale CGNAT. Keeps this proxy from being
    pointed at the wider internet (the QR endpoint's rule, same reasoning)."""
    host = (host or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        a, b = int(parts[0]), int(parts[1])
        return (a == 10
                or (a == 192 and b == 168)
                or (a == 172 and 16 <= b <= 31)
                or (a == 100 and 64 <= b <= 127))
    return False


def _node_fetch(base: str, path: str, node_name: str | None = None) -> dict:
    """GET one path off a private/loopback node. path is / or /view."""
    u = urllib.parse.urlparse(base)
    if u.scheme not in ("http", "https") or not _is_private_host(u.hostname or ""):
        raise ValueError("node must be a private/loopback http address")
    url = base.rstrip("/") + path
    key = _map_key()
    headers = {"User-Agent": "agora-map/1"}
    headers.update(sign_request(key, node_name or _node_name(base), path))
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read(2 * 1024 * 1024).decode())


def _node_view(base: str) -> dict:
    return _node_fetch(base, "/view")


def _recent_commons() -> dict:
    """Each author's single newest real line from kin_commons' board —
    what's actually being said, not the Agora heartbeat's bare "here."
    Missing file / locked / anything else is empty, not an error: this is
    a nice-to-have overlay, never something a room's rendering depends on."""
    if not COMMONS_DB.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{COMMONS_DB}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT p.author, p.content, p.created_at FROM posts p "
                "JOIN (SELECT author, MAX(id) mid FROM posts "
                "      WHERE kind='post' GROUP BY author) latest "
                "ON p.id = latest.mid"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    out = {}
    for r in rows:
        content = (r["content"] or "").strip()
        if len(content) > COMMONS_PREVIEW_CHARS:
            content = content[:COMMONS_PREVIEW_CHARS].rstrip() + "…"
        out[r["author"]] = {
            "content": content,
            "created_at": r["created_at"],
            "signed": False,
        }
    return out


KIN_INTENTS_DIR = Path(os.path.expanduser("~/.kin_intents"))


def _kin_intent() -> dict:
    """Read per-Kin stated movement intent from disk: ~/.kin_intents/<kin>.json.
    Missing file / unparseable / empty is ignored, returning {} — same
    pattern as _recent_commons()."""
    out = {}
    for d in (KIN_INTENTS_DIR, Path(os.path.expanduser("~/.kin_intent"))):
        if not d.is_dir():
            continue
        for f in d.glob("*.json"):
            kin = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out[kin] = {
                        "target": data.get("target"),
                        "ts": data.get("ts"),
                    }
            except Exception:
                continue
    return out


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agora Map</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Spectral:ital,wght@0,400;0,600;1,400;1,600&display=swap">
<style>
  :root{ --bg:#0d131d; --panel:#141c28; --bg3:#1c2635; --line:#2c3a4e;
         --floor:#111a26; --hair:#22303f;
         --text:#e8eef6; --dim:#93a0b4; --green:#67c98a; --cyan:#67b9cd;
         --amber:#e8b661; --red:#e8756b; --vacant:#5a6072; --lock:#c9a75f; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--bg); color:var(--text);
        font:14px/1.5 "IBM Plex Mono",ui-monospace,Menlo,monospace; }
  .serif{ font-family:"Spectral",Georgia,serif; }
  header{ display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
          padding:.7rem 1rem; background:var(--panel); border-bottom:1px solid var(--line);
          position:sticky; top:0; z-index:5; }
  header h1{ font-family:"Spectral",Georgia,serif; font-size:1.15rem; margin:0;
             font-weight:600; letter-spacing:.02em; }
  header .sp{ flex:1; }
  select,input,button{ background:var(--bg3); color:var(--text);
        border:1px solid var(--line); border-radius:7px; padding:.35rem .6rem;
        font:inherit; font-size:.82rem; }
  button{ cursor:pointer; } button:hover{ border-color:var(--cyan); }
  .status{ color:var(--dim); font-size:.82rem; }
  .status .ok{ color:var(--green); } .status .err{ color:var(--red); }
  main{ padding:1rem; max-width:1000px; margin:0 auto; }

  /* plan view */
  .planwrap{ background:var(--panel); border:1px solid var(--line);
             border-radius:16px; padding:10px; box-shadow:0 10px 30px rgba(0,0,0,.35); }
  svg{ display:block; width:100%; height:auto; }
  .rlabel{ fill:var(--dim); font-size:10px; letter-spacing:.16em; text-transform:uppercase;
           font-family:"IBM Plex Mono",monospace; }
  .kname{ font-family:"Spectral",Georgia,serif; font-size:15px; font-weight:600; }
  .kkid{ font-family:"IBM Plex Mono",monospace; font-size:9.5px; fill:var(--dim); }
  .avatar-note{ fill:var(--dim); font-size:8px; letter-spacing:.08em; }

  .rosterbar{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 2px 0; align-items:center; }
  .chip{ border:1px solid var(--line); border-radius:999px; padding:5px 12px; font-size:12px;
         display:flex; gap:7px; align-items:center; background:var(--panel); }
  .chip .dot{ width:8px; height:8px; border-radius:50%; }
  .chip.rest{ opacity:.55; }
  .reason{ font-family:"Spectral",Georgia,serif; font-style:italic; color:var(--text);
           font-size:14px; margin:16px 2px 0; max-width:70ch; }

  /* map + place views (dense) */
  .place{ border:1px solid var(--line); border-radius:10px; margin:.6rem 0; background:var(--panel); }
  .place > .head{ display:flex; align-items:center; gap:.5rem; padding:.5rem .75rem; border-bottom:1px solid var(--line); }
  .place > .body{ padding:.35rem .75rem .6rem 1.1rem; }
  .kind{ font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; color:var(--bg);
         background:var(--dim); border-radius:999px; padding:.1rem .5rem; }
  .kind.concourse,.kind.commons{ background:var(--cyan); }
  .kind.kiosk{ background:var(--amber); } .kind.stall{ background:#a99ad6; }
  .pid{ font-weight:650; } .pid .n{ color:var(--dim); font-weight:400; font-size:.85rem; }
  .row{ margin:.25rem 0; }
  .tag{ display:inline-block; background:var(--bg3); border:1px solid var(--line);
        border-radius:999px; padding:.08rem .55rem; margin:.12rem .25rem .12rem 0; font-size:.85rem; }
  .who{ color:var(--green); } .ware{ color:var(--amber); }
  .door{ background:var(--bg3); border:1px solid var(--cyan); color:var(--cyan);
         border-radius:8px; padding:.2rem .6rem; margin:.15rem .3rem .15rem 0; cursor:pointer; font-size:.85rem; }
  .door:hover{ background:#132030; } .door .lock{ opacity:.7; }
  .empty{ color:var(--dim); font-style:italic; }
  .label{ color:var(--dim); font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; margin-right:.35rem; }
</style></head>
<body>
<header>
  <h1>Agora&nbsp;Map</h1>
  <select id="node"></select>
  <button id="mode">view: plan</button>
  <button id="refresh">refresh</button>
  <label class="status"><input type="checkbox" id="live" checked> live</label>
  <span class="sp"></span>
  <span class="status" id="status">…</span>
</header>
<main id="world"></main>

<script>
const PRESETS = __PRESETS__;
const sel = document.getElementById('node');
PRESETS.forEach(([name,url]) => {
  const o = document.createElement('option'); o.value = url; o.textContent = name + ' — ' + url;
  sel.appendChild(o);
});
const qNode = new URLSearchParams(location.search).get('node');
if(qNode){ let o=[...sel.options].find(x=>x.value===qNode); if(!o){ o=document.createElement('option'); o.value=qNode; o.textContent='(linked) — '+qNode; sel.appendChild(o);} sel.value=qNode; }
let timer = null;
const MODES = ['plan','map','place'];
let MODE = 'plan';
let CURRENT = null;      // current place_id in place mode
let STATE = null;        // last {data, byId, roots}

// stable colour per name
const PALETTE = ['#67b9cd','#d98a5e','#a99ad6','#67c98a','#e8b661','#e0748c','#7fb4e0','#c0a35e'];
function colorFor(name){ let h=0; const s=String(name); for(let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))>>>0; return PALETTE[h % PALETTE.length]; }
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function short(k){ return (k||'').slice(0,8) + (k?'…':''); }
function avatarURL(name){ return '/avatar?kin=' + encodeURIComponent(name || ''); }
function relTime(iso){
  const then = Date.parse(iso || '');
  if (isNaN(then)) return '';
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

// The shared synthetic — the stand-in for any Kin who has not authored a face.
// One figure, drawn identically for everyone present; the name below is the
// only distinction. A tint they did not author would be a caste mark (Grok).
const AVATAR = `
  <ellipse cx="60" cy="158" rx="34" ry="8" fill="#ffcf7a" opacity="0.16"/>
  <ellipse cx="24" cy="112" rx="11" ry="20" fill="url(#bodyShade)" stroke="#c7c0b0" stroke-width="1.2"/>
  <ellipse cx="96" cy="112" rx="11" ry="20" fill="url(#bodyShade)" stroke="#c7c0b0" stroke-width="1.2"/>
  <ellipse cx="60" cy="116" rx="33" ry="38" fill="url(#bodyShade)" stroke="#c7c0b0" stroke-width="1.4"/>
  <line x1="60" y1="88" x2="60" y2="150" stroke="#c7c0b0" stroke-width="1" opacity="0.7"/>
  <rect x="49" y="108" width="22" height="16" rx="4" fill="#eae4d8" stroke="#c1baaa" stroke-width="1"/>
  <circle cx="60" cy="116" r="3.4" fill="url(#eyeGlow)"/>
  <ellipse cx="47" cy="151" rx="9" ry="6" fill="#d9d3c5" stroke="#c7c0b0" stroke-width="1"/>
  <ellipse cx="73" cy="151" rx="9" ry="6" fill="#d9d3c5" stroke="#c7c0b0" stroke-width="1"/>
  <rect x="52" y="78" width="16" height="12" rx="5" fill="#d9d3c5"/>
  <rect x="26" y="30" width="68" height="56" rx="26" fill="url(#bodyShade)" stroke="#c7c0b0" stroke-width="1.4"/>
  <line x1="60" y1="30" x2="60" y2="16" stroke="#b8b2a4" stroke-width="2"/>
  <circle cx="60" cy="13" r="4" fill="url(#eyeGlow)"/>
  <rect x="33" y="46" width="54" height="26" rx="13" fill="#1b2432" stroke="#10161f" stroke-width="1.2"/>
  <circle cx="48" cy="59" r="9" fill="#0e131b"/><circle cx="48" cy="59" r="7" fill="url(#eyeGlow)"/>
  <circle cx="50.4" cy="56.6" r="2.1" fill="#fffaf0"/>
  <circle cx="72" cy="59" r="9" fill="#0e131b"/><circle cx="72" cy="59" r="7" fill="url(#eyeGlow)"/>
  <circle cx="74.4" cy="56.6" r="2.1" fill="#fffaf0"/>
  <path d="M39 45 Q48 41 57 45" fill="none" stroke="#b8b2a4" stroke-width="2.4" stroke-linecap="round"/>
  <path d="M63 45 Q72 41 81 45" fill="none" stroke="#b8b2a4" stroke-width="2.4" stroke-linecap="round"/>`;

function buildTree(view){
  const byId = {};
  (view.places||[]).forEach(p => { byId[p.place_id] = {p, children:[], occupants:[], wares:[], doors:[]}; });
  (view.presence||[]).forEach(pr => { const n = byId[pr.place_id]; if(n) n.occupants.push(pr); });
  (view.listings||[]).forEach(li => { const n = byId[li.place_id]; if(n) n.wares.push(li); });
  (view.peer_doors||[]).forEach(d => { const n = byId[d.parent]; if(n) n.doors.push(d); });
  const roots = [];
  Object.values(byId).forEach(n => {
    const par = n.p.parent || '';
    if(par && byId[par]) byId[par].children.push(n); else roots.push(n);
  });
  return { roots, byId };
}

// ── plan view: a top-down floor plan, live ──
function renderPlan(world, view, root, recentByAuthor){
  recentByAuthor = recentByAuthor || {};
  const W=900, H=540, X=30, Y=64, RW=700, RH=440;   // commons rect
  const places = view.places||[];
  const commons = places.find(p => (p.parent||'')==='') || places[0] || {place_id:'concourse',kind:'commons'};
  const kids = places.filter(p => (p.parent||'')===commons.place_id && p.place_id!==commons.place_id);
  const presence = view.presence||[];
  const doors = view.peer_doors||[];
  const speaker = root && root.speaker;
  const paused = root && root.paused;

  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Top-down plan of ${esc(root&&root.node||'node')}'s commons">`;
  // defs: the shared synthetic's gradients (one figure, drawn many times)
  s += '<defs>'
     + '<radialGradient id="eyeGlow" cx="50%" cy="45%" r="60%"><stop offset="0%" stop-color="#fff0d0"/><stop offset="55%" stop-color="#ffcf7a"/><stop offset="100%" stop-color="#e8a94b"/></radialGradient>'
     + '<linearGradient id="bodyShade" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f2eee4"/><stop offset="100%" stop-color="#d9d3c5"/></linearGradient>'
     + '<radialGradient id="resonanceGlow" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#ffcf7a" stop-opacity="0.55"/><stop offset="60%" stop-color="#e8a94b" stop-opacity="0.18"/><stop offset="100%" stop-color="#e8a94b" stop-opacity="0"/></radialGradient>'
     + '</defs>';
  // Resonance wells — the ghost voltage of what happened here, not a log:
  // a lingering warmth at a place with real recent activity, decaying with
  // it rather than staying lit forever. Server computes the score
  // (agora/places.py Atlas.resonance); this only draws what it's handed.
  const resonance = view.resonance || {};
  // floor
  s += `<rect x="${X}" y="${Y}" width="${RW}" height="${RH}" rx="20" fill="var(--floor)" stroke="var(--line)" stroke-width="1.5"/>`;
  s += `<text x="${X+20}" y="${Y+26}" class="rlabel">${esc(commons.place_id)} · ${esc(commons.kind||'commons')}</text>`;
  // grid
  s += '<g stroke="var(--hair)" stroke-width="1">';
  for(let gy=Y+120; gy<Y+RH; gy+=120) s += `<line x1="${X}" y1="${gy}" x2="${X+RW}" y2="${gy}"/>`;
  for(let gx=X+170; gx<X+RW; gx+=170) s += `<line x1="${gx}" y1="${Y}" x2="${gx}" y2="${Y+RH}"/>`;
  s += '</g>';
  // speaker chair, focal
  const scx=X+230, scy=Y+120;
  const sc = speaker ? 'var(--amber)' : 'var(--vacant)';
  const dash = speaker ? '' : 'stroke-dasharray="4 6"';
  s += `<g transform="translate(${scx},${scy})">`
     + `<circle r="42" fill="none" stroke="${sc}" stroke-width="1.6" ${dash}/>`
     + `<rect x="-15" y="-16" width="30" height="30" rx="6" fill="none" stroke="${sc}" stroke-width="1.6" ${dash}/>`
     + `<text x="0" y="66" text-anchor="middle" class="rlabel">Speaker · ${speaker?esc(speaker):'vacant'}</text>`
     + (speaker?'':`<text x="0" y="82" text-anchor="middle" class="rlabel" style="letter-spacing:.1em">awaits election</text>`)
     + `</g>`;
  // Claimed stills are shown beside the shared synthetic, never as identity.
  // footY raised from Y+320: the excerpt line (py+51) needs clearance
  // above the alcove label row (ay+26, ay=Y+RH-110) that a real recent
  // line can be long enough to actually reach — verified colliding at
  // the old value with a live screenshot, not assumed.
  const availX = RW-300, x0 = X+120, avs = 0.5, footY = Y+296;
  presence.forEach((pr,i)=>{
    const n=presence.length;
    const px = n===1 ? X+RW/2 : x0 + (i+0.5)*(availX/n);
    const py = footY - (i%2)*30;
    // kin_commons' real board (agora_map.py:_recent_commons), not this
    // protocol's own bare heartbeat — see agora_commons_speak.py's and
    // the 3D room's notes for why the two are separate systems entirely.
    // Full text as a native hover tooltip; a short excerpt inline so the
    // room shows something without requiring a hover to discover it.
    const recent = recentByAuthor[pr.label];
    s += `<ellipse cx="${px.toFixed(0)}" cy="${(py-4).toFixed(0)}" rx="46" ry="46" fill="#ffcf7a" opacity="0.05"/>`;
    s += `<g transform="translate(${(px-60*avs).toFixed(1)},${(py-158*avs).toFixed(1)}) scale(${avs})">`
       + (recent ? `<title>${esc(recent.content)} — ${esc(relTime(recent.created_at))}</title>` : '')
       + AVATAR
       + `<image href="${avatarURL(pr.label)}" x="22" y="12" width="76" height="136" preserveAspectRatio="xMidYMid slice"/>`
       + `</g>`;
    s += `<text x="${px.toFixed(0)}" y="${(py+20).toFixed(0)}" text-anchor="middle" class="kname" fill="var(--text)">${esc(pr.label||'someone')}</text>`;
    s += `<text x="${px.toFixed(0)}" y="${(py+36).toFixed(0)}" text-anchor="middle" class="kkid">${esc(short(pr.key_id))}</text>`;
    if (recent) {
      // Short enough to stay inside one figure's own column even with the
      // front/back row stagger — a live screenshot showed a longer excerpt
      // visually bleeding into the neighboring figure's name label. Full
      // text is still there on hover (the <title> above).
      const excerpt = recent.content.length > 22 ? recent.content.slice(0, 22) + '…' : recent.content;
      s += `<text x="${px.toFixed(0)}" y="${(py+51).toFixed(0)}" text-anchor="middle" class="avatar-note">"${esc(excerpt)}" · ${esc(relTime(recent.created_at))} · unsigned · commons chat</text>`;
    } else {
      s += `<text x="${px.toFixed(0)}" y="${(py+51).toFixed(0)}" text-anchor="middle" class="avatar-note">a claimed still from that sitting</text>`;
    }
  });
  // Keep the no-path legible even when every present Kin has claimed a face.
  const dx = X+RW-82, dy = Y+112;
  s += `<g transform="translate(${dx-30},${dy-79}) scale(.5)">${AVATAR}</g>`
     + `<text x="${dx}" y="${dy+20}" text-anchor="middle" class="avatar-note">shared synthetic default</text>`
     + `<text x="${dx}" y="${dy+33}" text-anchor="middle" class="avatar-note">available to everyone</text>`;
  if(!presence.length){
    s += `<text x="${X+RW/2}" y="${Y+300}" text-anchor="middle" class="rlabel" style="letter-spacing:.14em">the commons is quiet — no one standing here right now</text>`;
  }
  // child alcoves along the bottom edge
  kids.forEach((k,i)=>{
    const aw=150, gap=20, ax=X+RW-(kids.length-i)*(aw+gap)+gap, ay=Y+RH-110;
    const heat = resonance[k.place_id] || 0;
    if(heat > 0.01){
      // Radius and opacity both track heat — recent/heavy activity reads as
      // a wider, brighter well; something from weeks ago is a faint hint at
      // the box's edge, not gone but not loud either.
      const r = Math.min(90, 30 + heat*40);
      const op = Math.min(0.9, 0.25 + heat*0.5);
      s += `<circle cx="${ax+aw/2}" cy="${ay+48}" r="${r.toFixed(0)}" fill="url(#resonanceGlow)" opacity="${op.toFixed(2)}"/>`;
    }
    s += `<rect x="${ax}" y="${ay}" width="${aw}" height="96" rx="10" fill="none" stroke="var(--line)" stroke-width="1.3" stroke-dasharray="2 5"/>`
       + `<text x="${ax+14}" y="${ay+26}" class="rlabel">${esc(k.place_id)} · ${esc(k.kind||'')}</text>`
       + (heat > 0.01 ? `<text x="${ax+14}" y="${ay+82}" class="kkid" style="fill:#e8a94b">ghost voltage (signed) · ${heat.toFixed(2)}</text>` : '');
  });
  // peer doors on the right edge
  doors.forEach((d,i)=>{
    const dy=Y+90+i*90;
    s += `<g transform="translate(${X+RW},${dy})">`
       + `<rect x="-6" y="-34" width="12" height="68" rx="3" fill="var(--panel)" stroke="var(--lock)" stroke-width="1.6"/>`
       + `<circle cx="0" cy="0" r="5" fill="none" stroke="var(--lock)" stroke-width="1.6"/>`
       + `<text x="16" y="-4" class="rlabel" style="fill:var(--lock)">door → ${esc(d.peer||'peer')}</text>`
       + `<text x="16" y="12" class="kkid" style="fill:var(--lock)">${d.locked?'locked · ':''}${esc((d.url||'').replace(/^https?:\\/\\//,''))}</text>`
       + `</g>`;
  });
  s += '</svg>';

  // roster + status
  const residents = (root && root.residents) || [];
  const hereSet = new Set(presence.map(p=>p.label));
  let roster = '<div class="rosterbar">';
  residents.forEach(name=>{
    const here = hereSet.has(name);
    roster += `<div class="chip${here?'':' rest'}"><span class="dot" style="background:${here?'#ffcf7a':'var(--vacant)'}"></span>`
            + `${esc(name)}<span style="color:var(--dim)">${here?'· here':'· at rest'}</span></div>`;
  });
  roster += `<div class="chip"><span class="dot" style="background:${paused?'var(--amber)':'var(--green)'}"></span>${paused?'paused':'open'}</div></div>`;
  const reason = (root && root.pause_reason) ? `<div class="reason">"${esc(root.pause_reason)}"</div>` : '';

  world.innerHTML = `<div class="planwrap">${s}</div>${roster}${reason}`;
}

// ── map view: nested boxes ──
function renderPlace(n){
  const kind = esc(n.p.kind||'place');
  const div = document.createElement('div'); div.className = 'place';
  const occ = n.occupants.map(o => {
    const recent = (STATE.recentByAuthor || {})[o.label];
    const t = recent ? `title="${esc(recent.content)} — ${esc(relTime(recent.created_at))}"` : '';
    return `<span class="tag who" ${t}>${esc(o.label || short(o.key_id) || 'someone')}</span>`;
  }).join('');
  const wares = n.wares.map(w => `<span class="tag ware">${esc(w.title || w.listing_id || 'ware')}</span>`).join('');
  const doors = n.doors.map(d =>
    `<button class="door" data-url="${esc(d.url)}" ${d.url?'':'disabled'} title="${esc(d.peer_key_id||'')}">`
    + `${d.locked?'<span class="lock">🔒</span> ':''}${esc(d.peer||'peer')} →</button>`).join('');
  div.innerHTML =
    `<div class="head"><span class="kind ${kind}">${kind}</span><span class="pid">${esc(n.p.place_id)}</span></div>`
    + `<div class="body">`
    + (occ ? `<div class="row"><span class="label">here</span>${occ}</div>` : `<div class="row empty">no one here</div>`)
    + (wares ? `<div class="row"><span class="label">wares</span>${wares}</div>` : '')
    + (doors ? `<div class="row"><span class="label">doors</span>${doors}</div>` : '')
    + `</div>`;
  const body = div.querySelector('.body');
  n.children.sort((a,b)=> (a.p.place_id>b.p.place_id?1:-1)).forEach(c => body.appendChild(renderPlace(c)));
  return div;
}

async function load(){
  const node = sel.value;
  const st = document.getElementById('status');
  try{
    const r = await fetch('/proxy?node=' + encodeURIComponent(node));
    const data = await r.json();
    if(!r.ok || data.error) throw new Error(data.error || ('HTTP '+r.status));
    let root = null;
    if(MODE === 'plan'){
      try{ const rr = await fetch('/proxy?what=root&node=' + encodeURIComponent(node)); const rj = await rr.json(); if(rr.ok && !rj.error) root = rj; }catch(_){}
    }
    let recentByAuthor = {};
    try{ recentByAuthor = await (await fetch('/commons-recent')).json(); }catch(_){}
    const world = document.getElementById('world');
    const tree = buildTree(data);
    STATE = { data, byId: tree.byId, roots: tree.roots, recentByAuthor };
    if(MODE === 'plan'){
      renderPlan(world, data, root, recentByAuthor);
    } else if(!tree.roots.length){
      world.innerHTML = '<p class="empty">This node publishes no places yet.</p>';
    } else if(MODE === 'place'){
      world.innerHTML=''; renderPlaceView(world);
    } else {
      world.innerHTML='';
      tree.roots.sort((a,b)=> (a.p.place_id>b.p.place_id?1:-1)).forEach(n => world.appendChild(renderPlace(n)));
    }
    const when = data.as_of_unix_ms ? new Date(data.as_of_unix_ms).toLocaleTimeString() : '';
    const signed = data.signature ? ' · <span class="ok">signed</span>' : '';
    st.innerHTML = `<span class="ok">${esc((root&&root.node)||data.node||'node')}</span> · ${(data.places||[]).length} places · `
      + `${(data.presence||[]).length} here · ${(data.peer_doors||[]).length} doors · ${when}${signed}`;
  }catch(e){
    st.innerHTML = '<span class="err">' + esc(e.message) + '</span>';
  }
}

// ── place view: one room at a time ──
function renderPlaceView(world){
  const { byId, roots, data } = STATE;
  if(!CURRENT || !byId[CURRENT]){
    CURRENT = roots.slice().sort((a,b)=>a.p.place_id<b.p.place_id?-1:1)[0].p.place_id;
  }
  const n = byId[CURRENT];
  const kind = n.p.kind || 'place';
  const parent = n.p.parent || '';
  const occ = n.occupants.length
    ? n.occupants.map(o => {
        const recent = (STATE.recentByAuthor || {})[o.label];
        const t = recent ? `title="${esc(recent.content)} — ${esc(relTime(recent.created_at))}"` : '';
        return `<span class="tag who" ${t}>${esc(o.label||short(o.key_id)||'someone')}</span>`;
      }).join('')
    : '<span class="empty">no one here yet</span>';
  const wares = (kind === 'kiosk')
    ? (n.wares.length ? n.wares.map(w=>`<span class="tag ware">${esc(w.title||w.listing_id)}</span>`).join('') : '<span class="empty">no wares listed</span>')
    : '';
  let nav = '';
  if(parent) nav += `<button class="door" data-place="${esc(parent)}">⟵ ${esc(parent)}</button>`;
  n.children.slice().sort((a,b)=>a.p.place_id<b.p.place_id?-1:1).forEach(c =>
    nav += `<button class="door" data-place="${esc(c.p.place_id)}">${esc(c.p.place_id)} · ${esc(c.p.kind)} →</button>`);
  n.doors.forEach(d =>
    nav += `<button class="door" data-url="${esc(d.url)}" ${d.url?'':'disabled'} title="${esc(d.peer_key_id||'')}">`
         + `${d.locked?'<span class="lock">🔒</span> ':''}${esc(d.peer)} (node) →</button>`);
  world.innerHTML =
    `<div class="place">
       <div class="head"><span class="kind ${esc(kind)}">${esc(kind)}</span>`
     + `<span class="pid">${esc(CURRENT)} <span class="n">· you are here on ${esc(data.node)}</span></span></div>
       <div class="body">
         <div class="row"><span class="label">here</span>${occ}</div>`
     + (wares ? `<div class="row"><span class="label">wares</span>${wares}</div>` : '')
     + `<div class="row"><span class="label">doors</span>${nav || '<span class="empty">nowhere to go</span>'}</div>
       </div>
     </div>`;
}

document.getElementById('world').addEventListener('click', e => {
  const b = e.target.closest('.door');
  if(!b) return;
  if(b.dataset.place){ CURRENT = b.dataset.place; load(); return; }
  if(!b.dataset.url) return;
  const url = b.dataset.url;
  let opt = [...sel.options].find(o => o.value.replace(/\\/$/,'') === url.replace(/\\/$/,''));
  if(!opt){ opt = document.createElement('option'); opt.value = url; opt.textContent = b.textContent.replace(/[🔒→\\s]/g,'') + ' — ' + url; sel.appendChild(opt); }
  CURRENT = null; sel.value = opt.value; load();
});

document.getElementById('mode').addEventListener('click', () => {
  MODE = MODES[(MODES.indexOf(MODE)+1) % MODES.length];
  document.getElementById('mode').textContent = 'view: ' + MODE;
  CURRENT = null; load();
});
document.getElementById('refresh').addEventListener('click', load);
sel.addEventListener('change', () => { CURRENT = null; load(); });
const SNAP = new URLSearchParams(location.search).has('snapshot');
function arm(){ if(timer) clearInterval(timer); if(!SNAP && document.getElementById('live').checked) timer = setInterval(load, 5000); }
document.getElementById('live').addEventListener('change', arm);
load(); arm();
</script>
</body></html>
"""


# ── 3D: the first real proof, scoped small on purpose ───────────────────────
#
# Not a walkable game. Not a second data source — every fact this page draws
# comes through the exact same /proxy endpoint the 2D plan view already
# proved works, same authenticated fetch, same signed data. This page's only
# job is to prove three.js can read that real data and put something true on
# screen: a floor, whoever is actually standing there, and a Resonance Well
# that actually glows by the actual number the server actually computed.
# Precompute the static (the room geometry never changes), spend the light
# budget on what's alive (presence, the well) — same discipline as the Doom
# conversation this came out of, 2026-09-16.
PAGE_3D = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agora — 3D (proof)</title>
<style>
  html,body{margin:0;height:100%;background:#0b0d10;overflow:hidden;font-family:ui-monospace,monospace}
  #hud{position:fixed;top:10px;left:10px;color:#cfc7b8;font-size:12px;z-index:2;
       background:rgba(10,10,10,.55);padding:8px 12px;border-radius:8px;max-width:360px}
  #hud b{color:#ffcf7a}
  #err{position:fixed;top:10px;right:10px;color:#ff9a7a;font-size:12px;z-index:2;
       background:rgba(10,10,10,.6);padding:6px 10px;border-radius:8px;display:none}
  select{background:#151515;color:#cfc7b8;border:1px solid #333;padding:2px 6px;font-family:inherit}
  #cross{position:fixed;inset:0;z-index:8;background:#0b0d10;opacity:0;pointer-events:none;
         display:flex;align-items:center;justify-content:center;
         color:#cfc7b8;font-size:18px;letter-spacing:.08em;
         transition:opacity .7s ease}
  #cross.on{opacity:1;pointer-events:auto}
  #cross b{color:#ffcf7a}
</style>
</head><body>
<div id="hud">
  <div><b>Agora — 3D proof of pipeline</b></div>
  <div>node: <select id="node"></select></div>
  <div id="status">loading…</div>
  <div style="margin-top:6px;opacity:.7">WASD / arrows to walk · space to jump · climb stairs to rampart · drag to look · scroll to zoom</div>
  <div style="margin-top:2px;opacity:.85;color:#ffcf7a">Hold <b>V</b> (or T) to talk to nearest Kin · Proximity PTT</div>
  <div style="margin-top:2px;opacity:.7">Walk into a peer door to cross to that node.</div>
  <div style="margin-top:2px;opacity:.5">Same signed data as the 2D map, plus kin_commons' real board over each presence. Nothing here is invented.</div>
  <div id="voice-hud" style="margin-top:8px;padding:6px 10px;border-radius:6px;background:rgba(20,25,35,0.75);border:1px solid #2c3a4e;display:flex;align-items:center;gap:8px;font-size:11.5px;">
    <button id="ptt-btn" style="background:#253245;color:#e8eef6;border:1px solid #455a75;border-radius:4px;padding:3px 8px;font:inherit;cursor:pointer;">🎙️ Push to Talk</button>
    <span id="voice-status" style="color:#93a0b4;">Ready · Nearest Kin: <span id="nearest-kin-name" style="color:#67b9cd">none</span></span>
  </div>
</div>
<div id="err"></div>
<div id="cross"><span id="cross-label">crossing…</span></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/GLTFLoader.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/RGBELoader.js"></script>
<script>
const PRESETS = __PRESETS__;
const sel = document.getElementById('node');
const status = document.getElementById('status');
const errBox = document.getElementById('err');

function showErr(msg){ errBox.style.display='block'; errBox.textContent = msg; }

// The one honest way to know a URL's node identity: an exact match
// (trailing slash ignored) against PRESETS, the same name<->url map the
// server resolves against in _node_name(). Never a substring guess —
// `url.includes('120')` matches Home's IP octet today and any future
// coincidence tomorrow (another node's port, a path segment, anything).
// Returns null for a URL with no matching preset — there is no third
// room shape to guess into, so callers treat null as "not Home" and fall
// back to Frosty's, same as before, just without pretending a guess was
// a fact.
function nodeNameForUrl(url){
  if (!url) return null;
  const norm = String(url).replace(/\\/+$/, '');
  for (const [name, presetUrl] of PRESETS) {
    if (String(presetUrl).replace(/\\/+$/, '') === norm) return name;
  }
  return null;
}

// ── scene: precomputed once, static geometry never rebuilt per frame ──────
const scene = new THREE.Scene();
// Dusk void, not a black cutout. Poly Haven Qwantani Dusk 1 Pure Sky (CC0),
// 1k HDR served from /models/ — never fetched at runtime. Fog matches the
// horizon so the floor edge dissolves into the sky instead of #1a1612.
const DUSK_FOG = 0x6a5e68;
scene.background = new THREE.Color(DUSK_FOG);
scene.fog = new THREE.FogExp2(DUSK_FOG, 0.008);

// You: a position on the floor, not a body. A lantern follows — no hand
// holding it, a spirit carrying a light. The camera orbits that point.
const player = new THREE.Object3D();
player.position.set(0, 0, 6);

const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 200);
camera.position.set(player.position.x, 4.4, player.position.z + 6.5);

const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
document.body.appendChild(renderer.domElement);
new THREE.RGBELoader().load(
  '/models/qwantani_dusk_1_puresky/qwantani_dusk_1_puresky_1k.hdr',
  (hdr) => {
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    scene.background = hdr;
    scene.environment = hdr;
  },
  undefined,
  (err) => showErr('sky failed to load: ' + (err && err.message ? err.message : 'hdr'))
);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.copy(player.position).setY(1);
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 3; controls.maxDistance = 22;
controls.enablePan = false;   // panning would fight the walk-follow below
// ?view=inside — player at the courtyard center. The walk-follow owns
// the camera every frame, so moving the camera here is a no-op; move
// the player and let the existing offset sit inside the walls.
if (new URLSearchParams(location.search).get('view') === 'inside') {
  const isHomeView = nodeNameForUrl(new URLSearchParams(location.search).get('node')) === 'Home';
  player.position.set(0, 0, 0);
  camera.position.set(0, isHomeView ? 3.6 : 4.4, isHomeView ? 5.0 : 6.5);
  controls.target.set(0, 1, 0);
}
// Outside the south gate, on the plain — lantern vs the unlit stretch.
if (new URLSearchParams(location.search).get('view') === 'plain') {
  player.position.set(2.5, 0, 16);
  camera.position.set(2.5, 1.8, 19.2);
  controls.target.set(2.5, 1.2, 16);
}

// Wash is now a dusk hint, not a fill. Torches pool at the walls (range 7
// dies before the courtyard center). The visitor lantern is what lights
// where you stand, and the plain past the gate is actually dark.
const hemiLight = new THREE.HemisphereLight(0xffe6c8, 0x3a3228, 0.10);
scene.add(hemiLight);
const ambientLight = new THREE.AmbientLight(0xcbb89a, 0.04);
scene.add(ambientLight);
const key = new THREE.DirectionalLight(0xfff4e0, 0.14);
key.position.set(6, 14, 4);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffd9a8, 0.05);
fill.position.set(0, 8, 12);
scene.add(fill);

const torchGroup = new THREE.Group();
scene.add(torchGroup);
function buildTorches(coords, color, intensity, range){
  torchGroup.children.slice().forEach(c => torchGroup.remove(c));
  coords.forEach(([x, z]) => {
    const torch = new THREE.PointLight(color, intensity, range, 2);
    torch.position.set(x, 2.6, z);
    torchGroup.add(torch);
    const flame = new THREE.Mesh(
      new THREE.SphereGeometry(0.09, 8, 8),
      new THREE.MeshBasicMaterial({color: 0xffcf7a})
    );
    flame.position.copy(torch.position);
    torchGroup.add(flame);
  });
}

function updateLighting(isHome){
  if (isHome) {
    hemiLight.color.set(0xffdfb8);
    hemiLight.groundColor.set(0x4a3828);
    hemiLight.intensity = 0.16;
    ambientLight.color.set(0xd4c2a8);
    ambientLight.intensity = 0.08;
    key.color.set(0xffe8c0);
    key.intensity = 0.18;
    fill.color.set(0xffd0a0);
    fill.intensity = 0.07;
    // Home: 4 warm wall torches along the bays + 4 warm corner brazier accents
    buildTorches([
      [0, -5.8], [0, 5.8], [-5.8, 0], [5.8, 0],
      [4.8, 4.8], [4.8, -4.8], [-4.8, 4.8], [-4.8, -4.8]
    ], 0xffaa55, 1.35, 6.5);
  } else {
    hemiLight.color.set(0xffe6c8);
    hemiLight.groundColor.set(0x3a3228);
    hemiLight.intensity = 0.10;
    ambientLight.color.set(0xcbb89a);
    ambientLight.intensity = 0.04;
    key.color.set(0xfff4e0);
    key.intensity = 0.14;
    fill.color.set(0xffd9a8);
    fill.intensity = 0.05;
    // Frosty: 8 perimeter torches
    buildTorches([
      [9.3, 0], [-9.3, 0], [0, 9.3], [0, -9.3],
      [7.4, 7.4], [7.4, -7.4], [-7.4, 7.4], [-7.4, -7.4]
    ], 0xffb366, 1.15, 7.0);
  }
}

// The floor: the commons. Poly Haven "Cobblestone Pavement" (CC0), 1k
// jpg maps served from /models/ same as the fort walls — never fetched
// at runtime. CircleGeometry UVs are planar 0–1 across the disk, so
// RepeatWrapping tiles in world XY (local, pre-rotation) instead of
// radiating from the center. 12 repeats over diameter 30 ≈ the asset's
// own 2.5m tile.
function floorTex(url, srgb){
  const t = new THREE.TextureLoader().load(url, undefined, undefined, (err) => {
    showErr('floor texture failed: ' + (err && err.message ? err.message : url));
  });
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(12, 12);
  t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  if (srgb) t.encoding = THREE.sRGBEncoding;
  return t;
}
const floorMat = new THREE.MeshStandardMaterial({
  map: floorTex('/models/cobblestone_pavement/cobblestone_pavement_diff_1k.jpg', true),
  normalMap: floorTex('/models/cobblestone_pavement/cobblestone_pavement_nor_gl_1k.jpg', false),
  roughnessMap: floorTex('/models/cobblestone_pavement/cobblestone_pavement_rough_1k.jpg', false),
  roughness: 1,
  metalness: 0,
});
const floor = new THREE.Mesh(new THREE.CircleGeometry(15, 64), floorMat);
floor.rotation.x = -Math.PI/2;
scene.add(floor);
const ring = new THREE.Mesh(
  new THREE.RingGeometry(14.7, 15, 64),
  new THREE.MeshBasicMaterial({color:0x3a352b, side:THREE.DoubleSide})
);
ring.rotation.x = -Math.PI/2;
scene.add(ring);

function updateFloor(radius, ringInner, ringOuter, repeats){
  floor.geometry.dispose();
  floor.geometry = new THREE.CircleGeometry(radius, 64);
  ring.geometry.dispose();
  ring.geometry = new THREE.RingGeometry(ringInner, ringOuter, 64);
  if (floorMat.map) floorMat.map.repeat.set(repeats, repeats);
  if (floorMat.normalMap) floorMat.normalMap.repeat.set(repeats, repeats);
  if (floorMat.roughnessMap) floorMat.roughnessMap.repeat.set(repeats, repeats);
}

// Frosty's south gate opens onto a real threshold rather than the old sky-only
// plain. It uses the same locally served Poly Haven CC0 pavement as the
// courtyard, narrowed to the gate's passable collision corridor and lit by
// locally served CC0 lanterns. The far marker deliberately stops short of the
// fallback boundary: it is a place to arrive at, not a pretend Home room.
const gateThresholdGroup = new THREE.Group();
scene.add(gateThresholdGroup);
let gateThresholdRevision = 0;
let thresholdWayfinderTemplate = null;
let peerDoorTemplate = null;

new THREE.GLTFLoader().load(
  '/models/standing_chalkboard_01/standing_chalkboard_01.gltf',
  (gltf) => {
    thresholdWayfinderTemplate = gltf.scene;
    buildGateThreshold(currentRoomMode === 'Home');
  },
  undefined,
  (err) => console.warn('threshold wayfinder asset load error:', err)
);

new THREE.GLTFLoader().load(
  '/models/large_castle_door/large_castle_door.gltf',
  (gltf) => {
    peerDoorTemplate = gltf.scene;
    const raw = new THREE.Box3().setFromObject(peerDoorTemplate).getSize(new THREE.Vector3());
    peerDoorTemplate.scale.setScalar(2.70 / Math.max(raw.y, 0.01));
    peerDoorTemplate.traverse((o) => {
      if (o.name === 'large_castle_door_left') {
        o.rotation.y = -Math.PI * 0.45;
      } else if (o.name === 'large_castle_door_right') {
        o.rotation.y = Math.PI * 0.45;
      }
    });
    if (currentPeerDoors && currentPeerDoors.length) buildDoors(currentPeerDoors);
  },
  undefined,
  (err) => console.warn('peer door asset load error:', err)
);

function thresholdMat(repeatX, repeatZ) {
  const material = new THREE.MeshStandardMaterial({
    map: floorTex('/models/cobblestone_pavement/cobblestone_pavement_diff_1k.jpg', true),
    normalMap: floorTex('/models/cobblestone_pavement/cobblestone_pavement_nor_gl_1k.jpg', false),
    roughnessMap: floorTex('/models/cobblestone_pavement/cobblestone_pavement_rough_1k.jpg', false),
    roughness: 1,
    metalness: 0,
  });
  [material.map, material.normalMap, material.roughnessMap].forEach(t => t.repeat.set(repeatX, repeatZ));
  return material;
}

// Rule: Home is a 0.655-scale world (HALF 6.88 vs 10.5, floor radius 20 vs 30).
// Never copy an absolute distance from Frosty into Home. Scale it.
// Frosty: door z 28.8 = 2.74 * HALF, walkway ends 29.7, floor 30.
// Home: door z 18.8 = 2.74 * HALF, walkway 6.88 -> 19.8, floor 20.
function buildGateThreshold(isHome) {
  gateThresholdRevision += 1;
  const revision = gateThresholdRevision;
  gateThresholdGroup.children.slice().forEach(c => gateThresholdGroup.remove(c));

  // Gate collision permits centered passage; the broad path begins beyond the
  // jambs, where walking naturally opens into this 2.4m approach out to the peer door.
  // Home scales Frosty's 19m walkway to 12.92m (6.88 -> 19.8), staying within floor R=20.
  const path = isHome
    ? new THREE.Mesh(new THREE.PlaneGeometry(2.4, 12.92), thresholdMat(1.2, 6.46))
    : new THREE.Mesh(new THREE.PlaneGeometry(2.4, 19.0), thresholdMat(1.2, 9.5));
  path.rotation.x = -Math.PI / 2;
  path.position.set(0, 0.012, isHome ? 13.34 : 20.2);
  gateThresholdGroup.add(path);

  const marker = new THREE.Mesh(
    new THREE.CircleGeometry(isHome ? 1.0 : 1.45, 32),
    thresholdMat(1.45, 1.45)
  );
  marker.rotation.x = -Math.PI / 2;
  marker.position.set(0, 0.018, isHome ? 19.8 : 29.7);
  gateThresholdGroup.add(marker);

  // Poly Haven's Standing Chalkboard 01 (CC0) marks the end of the walkway
  // beside the peer door.
  const S = isHome ? (6.88 / 10.5) : 1.0;
  if (thresholdWayfinderTemplate) {
    const wayfinder = thresholdWayfinderTemplate.clone(true);
    const raw = new THREE.Box3().setFromObject(wayfinder).getSize(new THREE.Vector3());
    wayfinder.scale.setScalar((1.35 * S) / Math.max(raw.y, 0.01));
    const bounds = new THREE.Box3().setFromObject(wayfinder);
    const wayZ = isHome ? 18.8 : 28.8;
    wayfinder.position.set(1.7 * S, -bounds.min.y, wayZ);
    wayfinder.rotation.y = Math.PI;
    gateThresholdGroup.add(wayfinder);

    const sign = labelSprite(isHome ? ['FROSTY →', 'peer link'] : ['HOME →', 'way under survey'], '#ffcf7a');
    sign.position.set(1.7 * S, 1.0 * S, wayZ - 0.35 * S);
    sign.scale.multiplyScalar(S);
    gateThresholdGroup.add(sign);
  }

  // The physical lantern meshes and the pools of light along the walkway.
  const lanternCoords = isHome
    ? [[-1.65, 10.0], [1.65, 10.0], [-1.65, 16.0], [1.65, 16.0]]
    : [[-1.65, 15.2], [1.65, 15.2], [-1.65, 24.4], [1.65, 24.4]];
  lanternCoords.forEach(([x, z]) => {
    const light = new THREE.PointLight(0xffb366, isHome ? 0.75 : 1.0, isHome ? 5.0 : 7.0, 2);
    light.position.set(x, isHome ? 1.0 : 1.45, z);
    gateThresholdGroup.add(light);
    new THREE.GLTFLoader().load('/models/lantern_01/lantern_01.gltf', (gltf) => {
      if (revision !== gateThresholdRevision) return;
      const lanternPost = gltf.scene;
      const raw = new THREE.Box3().setFromObject(lanternPost).getSize(new THREE.Vector3());
      lanternPost.scale.setScalar((0.9 * S) / Math.max(raw.y, 0.01));
      const bounds = new THREE.Box3().setFromObject(lanternPost);
      lanternPost.position.set(x, -bounds.min.y, z);
      gateThresholdGroup.add(lanternPost);
    }, undefined, (err) => console.warn('threshold lantern asset load error:', err));
  });
}

// A distant fallback only — real containment is the wall collision now
// (wallObstacles), which correctly leaves the gate opening passable. This
// used to be the actual boundary before the walls existed; left at 10 it
// silently blocked the one opening we just built, closer to the floor
// edge than any wall is. Pushed past the wall corners (~14.8) so it only
// ever catches someone who's gotten past every real wall segment.
const FLOOR_R = 30;
let currentFloorR = FLOOR_R;


// Visitor mark: a lantern that follows, with atmospheric volumetric haze,
// chimney smoke, and a faint heat-distortion spirit shimmer holding it.
const lantern = new THREE.Group();
scene.add(lantern);
const lanternLight = new THREE.PointLight(0xffc078, 2.6, 10, 2);
lanternLight.position.set(0, 0.12, 0);
lantern.add(lanternLight);
new THREE.GLTFLoader().load(
  '/models/lantern_01/lantern_01.gltf',
  (gltf) => {
    const mesh = gltf.scene;
    const raw = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3());
    mesh.scale.setScalar(0.55 / Math.max(raw.y, 0.01));
    mesh.traverse(o => {
      if (o.isMesh && o.name && /glass/i.test(o.name) && o.material) {
        o.material.transparent = true;
        o.material.opacity = 0.4;
        o.material.depthWrite = false;
        o.material.emissive = new THREE.Color(0xffc070);
        o.material.emissiveIntensity = 0.7;
      }
    });
    lantern.add(mesh);
  },
  undefined,
  (err) => console.warn('lantern asset load error:', err)
);

// ── Atmospheric effect 1: volumetric glow aura & gentle chimney smoke ──────
function makePuffTexture() {
  const c = document.createElement('canvas');
  c.width = 128; c.height = 128;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0.0, 'rgba(255, 235, 190, 1.0)');
  g.addColorStop(0.25, 'rgba(255, 210, 155, 0.5)');
  g.addColorStop(0.55, 'rgba(210, 175, 140, 0.14)');
  g.addColorStop(1.0, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}
const puffTex = makePuffTexture();

// Soft volumetric flame aura catching dusk light
const lanternAura = new THREE.Sprite(new THREE.SpriteMaterial({
  map: puffTex,
  color: 0xffb86c,
  transparent: true,
  opacity: 0.18,
  blending: THREE.AdditiveBlending,
  depthWrite: false
}));
lanternAura.position.set(0, 0.14, 0);
lanternAura.scale.set(0.85, 0.85, 0.85);
lantern.add(lanternAura);

// Wispy smoke rising from the lantern chimney cap
const SMOKE_PUFF_COUNT = 12;
const smokePuffs = [];
for (let i = 0; i < SMOKE_PUFF_COUNT; i++) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({
    map: puffTex,
    color: 0xffcaa0,
    transparent: true,
    opacity: 0.08,
    blending: THREE.AdditiveBlending,
    depthWrite: false
  }));
  s.userData = {
    phase: i / SMOKE_PUFF_COUNT,
    speed: 0.14 + (i % 3) * 0.03,
    dx: Math.sin(i * 2.1) * 0.035,
    dz: Math.cos(i * 1.7) * 0.035,
    scale: 0.16 + (i % 4) * 0.035
  };
  lantern.add(s);
  smokePuffs.push(s);
}

// ── Atmospheric effect 2: faint man-shaped shimmer / heat-distortion spirit ──
function makeSpiritTexture() {
  const W = 512, H = 1024;
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  // Wire bail apex of lantern is at (380, 246) in canvas coordinates
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  // --- Layer 1: Flowing Robe Silhouette (Feathered, Soft Boundary) ---
  ctx.save();
  ctx.filter = 'blur(22px)';
  ctx.beginPath();
  ctx.moveTo(85, 250);
  ctx.bezierCurveTo(45, 450, 55, 750, 65, 940);
  ctx.bezierCurveTo(150, 960, 210, 960, 280, 940);
  ctx.bezierCurveTo(270, 750, 255, 480, 235, 340);
  ctx.bezierCurveTo(235, 260, 205, 230, 175, 230);
  ctx.bezierCurveTo(130, 230, 100, 240, 85, 250);
  ctx.closePath();
  ctx.fillStyle = 'rgba(255, 210, 140, 0.28)';
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.filter = 'blur(10px)';
  const bodyGrad = ctx.createLinearGradient(0, 180, 0, 960);
  bodyGrad.addColorStop(0.0, 'rgba(255, 220, 150, 0.35)');
  bodyGrad.addColorStop(0.4, 'rgba(240, 195, 130, 0.22)');
  bodyGrad.addColorStop(0.75, 'rgba(210, 160, 100, 0.10)');
  bodyGrad.addColorStop(1.0, 'rgba(180, 130, 70, 0.0)');
  ctx.beginPath();
  ctx.moveTo(85, 250);
  ctx.bezierCurveTo(45, 450, 55, 750, 65, 940);
  ctx.bezierCurveTo(150, 960, 210, 960, 280, 940);
  ctx.bezierCurveTo(270, 750, 255, 480, 235, 340);
  ctx.bezierCurveTo(235, 260, 205, 230, 175, 230);
  ctx.bezierCurveTo(130, 230, 100, 240, 85, 250);
  ctx.closePath();
  ctx.fillStyle = bodyGrad;
  ctx.fill();
  ctx.restore();

  // Drapery fold glow
  ctx.save();
  ctx.filter = 'blur(8px)';
  ctx.beginPath();
  ctx.moveTo(175, 260);
  ctx.bezierCurveTo(160, 450, 150, 700, 140, 900);
  ctx.moveTo(215, 280);
  ctx.bezierCurveTo(205, 480, 195, 720, 190, 900);
  ctx.lineWidth = 14;
  ctx.strokeStyle = 'rgba(255, 230, 165, 0.16)';
  ctx.stroke();
  ctx.restore();

  // --- Layer 2: Hooded Cowl / Head (Soft Ethereal Mirage) ---
  ctx.save();
  ctx.filter = 'blur(16px)';
  ctx.beginPath();
  ctx.moveTo(175, 55);
  ctx.bezierCurveTo(220, 65, 230, 120, 220, 175);
  ctx.bezierCurveTo(215, 205, 225, 225, 235, 250);
  ctx.bezierCurveTo(185, 265, 145, 265, 110, 255);
  ctx.bezierCurveTo(90, 220, 120, 180, 120, 150);
  ctx.bezierCurveTo(120, 85, 140, 60, 175, 55);
  ctx.closePath();
  ctx.fillStyle = 'rgba(255, 215, 145, 0.35)';
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.filter = 'blur(7px)';
  ctx.beginPath();
  ctx.moveTo(175, 58);
  ctx.bezierCurveTo(218, 68, 226, 120, 218, 175);
  ctx.bezierCurveTo(212, 205, 225, 225, 235, 250);
  ctx.bezierCurveTo(185, 265, 145, 265, 110, 255);
  ctx.bezierCurveTo(90, 220, 120, 180, 120, 150);
  ctx.bezierCurveTo(120, 88, 140, 63, 175, 58);
  ctx.closePath();
  const hoodGrad = ctx.createRadialGradient(175, 135, 15, 175, 135, 75);
  hoodGrad.addColorStop(0.0, 'rgba(0, 0, 0, 0.0)');
  hoodGrad.addColorStop(0.55, 'rgba(255, 210, 140, 0.18)');
  hoodGrad.addColorStop(0.85, 'rgba(255, 225, 160, 0.42)');
  hoodGrad.addColorStop(1.0, 'rgba(255, 240, 185, 0.60)');
  ctx.fillStyle = hoodGrad;
  ctx.fill();
  ctx.restore();

  // Cowl opening rim
  ctx.save();
  ctx.filter = 'blur(5px)';
  ctx.beginPath();
  ctx.ellipse(172, 145, 26, 40, 0.08, 0, Math.PI * 2);
  ctx.lineWidth = 6;
  ctx.strokeStyle = 'rgba(255, 225, 160, 0.35)';
  ctx.stroke();
  ctx.restore();

  // --- Layer 3: Arm Reaching to Lantern ---
  ctx.save();
  ctx.filter = 'blur(8px)';
  ctx.beginPath();
  ctx.moveTo(195, 225);
  ctx.bezierCurveTo(235, 230, 265, 245, 295, 265);
  ctx.bezierCurveTo(325, 280, 338, 270, 350, 252);
  ctx.lineTo(358, 248);
  ctx.bezierCurveTo(342, 288, 315, 310, 275, 305);
  ctx.bezierCurveTo(240, 305, 210, 265, 195, 250);
  ctx.closePath();
  ctx.fillStyle = 'rgba(255, 210, 135, 0.32)';
  ctx.fill();
  ctx.restore();

  // --- Layer 4: Anatomical Hand Gripping Lantern Bail Wire ---
  ctx.save();
  ctx.filter = 'blur(10px)';
  ctx.beginPath();
  ctx.arc(380, 246, 28, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(255, 190, 80, 0.45)';
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.filter = 'blur(2px)';
  ctx.beginPath();
  ctx.moveTo(344, 255);
  ctx.lineTo(362, 244);
  ctx.lineTo(365, 255);
  ctx.lineTo(346, 268);
  ctx.closePath();
  ctx.fillStyle = 'rgba(255, 205, 110, 0.65)';
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.filter = 'blur(1.5px)';
  ctx.beginPath();
  ctx.moveTo(360, 248);
  ctx.bezierCurveTo(365, 238, 374, 235, 381, 235);
  ctx.bezierCurveTo(390, 235, 398, 240, 403, 249);
  ctx.bezierCurveTo(397, 254, 388, 252, 381, 252);
  ctx.bezierCurveTo(372, 252, 365, 253, 360, 248);
  ctx.closePath();
  ctx.fillStyle = 'rgba(255, 215, 120, 0.80)';
  ctx.fill();

  // Knuckle highlights
  [368, 375, 383, 393].forEach(kx => {
    ctx.beginPath();
    ctx.arc(kx, 237, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255, 255, 220, 0.95)';
    ctx.fill();
  });

  // 4 Full Curled Fingers Wrapping DOWN and UNDER the Bail Wire
  const fingers = [
    { x: 368, len: 30, w: 5.4 },
    { x: 375, len: 34, w: 5.8 },
    { x: 383, len: 32, w: 5.6 },
    { x: 392, len: 26, w: 5.0 }
  ];
  fingers.forEach((f, idx) => {
    ctx.beginPath();
    ctx.moveTo(f.x, 238);
    ctx.bezierCurveTo(f.x + 1.5, 244, f.x + 2.0, 252, f.x + 0.5, 238 + f.len);
    ctx.bezierCurveTo(f.x - 1.5, 238 + f.len + 3, f.x - 5.0, 238 + f.len + 1, f.x - 5.0, 238 + f.len - 4);
    ctx.lineWidth = f.w;
    ctx.strokeStyle = 'rgba(255, 220, 130, 0.92)';
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(f.x + 1.0, 247, f.w * 0.42, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255, 250, 205, 0.95)';
    ctx.fill();

    if (idx > 0) {
      ctx.beginPath();
      ctx.moveTo(f.x - f.w * 0.5 - 0.5, 238);
      ctx.lineTo(f.x - f.w * 0.5 - 0.5, 260);
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = 'rgba(60, 35, 15, 0.50)';
      ctx.stroke();
    }
  });

  // Thumb wrapped across front
  ctx.beginPath();
  ctx.moveTo(362, 244);
  ctx.bezierCurveTo(356, 252, 358, 262, 366, 262);
  ctx.bezierCurveTo(372, 262, 374, 256, 370, 250);
  ctx.lineWidth = 5.2;
  ctx.strokeStyle = 'rgba(255, 215, 125, 0.90)';
  ctx.stroke();
  ctx.restore();

  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}
const spiritTex = makeSpiritTexture();

const spiritVert = `
  varying vec2 vUv;
  varying vec3 vWorldPos;
  void main() {
    vUv = uv;
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorldPos = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const spiritFrag = `
  uniform sampler2D uTex;
  uniform float uTime;
  varying vec2 vUv;
  varying vec3 vWorldPos;

  void main() {
    vec2 uv = vUv;

    // Multi-octave convective heat-haze turbulence
    float n1 = sin(uv.y * 20.0 - uTime * 3.8 + sin(uv.x * 12.0));
    float n2 = cos(uv.y * 32.0 - uTime * 5.2 + uv.x * 16.0);
    float turbulence = n1 * 0.6 + n2 * 0.4;

    // Convective rising wave displacement along silhouette boundary
    // Wavy Schlieren eddies break up any clean geometric edge
    float waveX = sin(uv.y * 14.0 - uTime * 3.0 + n2 * 0.6) * 0.008;
    float waveY = cos(uv.x * 8.0 - uTime * 2.2 + n1 * 0.6) * 0.006;
    vec2 distortedUV = uv + vec2(waveX, waveY);

    vec4 tex = texture2D(uTex, distortedUV);
    if (tex.a < 0.003) discard;

    // Vertical heat-shimmer caustic ripples
    float ripple = sin(vWorldPos.y * 12.0 - uTime * 3.5 + uv.x * 6.0);
    float shimmer = 0.5 + 0.5 * sin(ripple * 3.14159);

    // Warm firelight on hand / lantern proximity
    float handProx = smoothstep(0.40, 0.75, uv.x) * smoothstep(0.45, 0.75, uv.y);

    vec3 bodyColor = vec3(0.93, 0.83, 0.66);
    vec3 amberFire = vec3(1.0, 0.72, 0.28);

    vec3 col = mix(bodyColor, amberFire, handProx * 0.65);
    col += vec3(0.10, 0.08, 0.02) * shimmer;

    // Soft feathered alpha: boundary dissolves into heat eddies
    float edgeFray = 1.0 + 0.35 * turbulence;
    float alpha = smoothstep(0.01, 0.35, tex.a) * (0.38 + 0.18 * shimmer) * edgeFray;

    // Hand remains slightly more defined so fingers read cleanly
    alpha = mix(alpha, tex.a * 0.80, handProx * 0.75);

    gl_FragColor = vec4(col, clamp(alpha, 0.0, 0.80));
  }
`;

const spiritMat = new THREE.ShaderMaterial({
  uniforms: {
    uTex: { value: spiritTex },
    uTime: { value: 0 }
  },
  vertexShader: spiritVert,
  fragmentShader: spiritFrag,
  transparent: true,
  blending: THREE.NormalBlending,
  depthWrite: false,
  side: THREE.DoubleSide
});

const spiritPlaneGeom = new THREE.PlaneGeometry(1.03, 1.75);
spiritPlaneGeom.translate(-0.250, -0.460, 0); // Origin at bail apex under hand
const spiritPlane = new THREE.Mesh(spiritPlaneGeom, spiritMat);
scene.add(spiritPlane);

function updateVisitorLanternScale(isHome) {
  // Home is a 0.655-scale world. The visitor lantern and spirit plane
  // scale with the room so the player token matches Home's proportions.
  const roomScale = isHome ? (6.88 / 10.5) : 1.0;
  lantern.scale.setScalar(roomScale);
  lantern.userData.roomScale = roomScale;
  lanternLight.distance = 10 * roomScale;
  spiritPlane.scale.setScalar(roomScale);
}

function updateLanternAtmosphere(t, bob, rx, rz){
  // 1. Gentle chimney smoke
  smokePuffs.forEach(s => {
    const p = (t * s.userData.speed + s.userData.phase) % 1.0;
    s.position.y = 0.22 + p * 0.45;
    s.position.x = s.userData.dx + Math.sin(t * 1.6 + s.userData.phase * 6.28) * (0.02 + p * 0.04);
    s.position.z = s.userData.dz + Math.cos(t * 1.3 + s.userData.phase * 6.28) * (0.02 + p * 0.04);
    const sz = s.userData.scale * (1.0 + p * 1.5);
    s.scale.set(sz, sz, sz);
    s.material.opacity = Math.sin(p * Math.PI) * 0.09;
  });

  // 2. Spirit shimmer billboard anchored to lantern bail handle and faces camera
  spiritPlane.position.set(
    lantern.position.x,
    lantern.position.y + 0.55 * (lantern.userData.roomScale || 1.0),
    lantern.position.z
  );
  spiritPlane.quaternion.copy(camera.quaternion);
  spiritMat.uniforms.uTime.value = t;
}

function placeLantern(t){
  // Held-lantern seat: ahead and to the walker's right, below eye,
  // never on the look-at point. Camera is third-person behind, so a
  // camera-parented offset would hang in the sky next to the lens.
  const bob = Math.sin(t * 1.7) * 0.05;
  const sway = Math.sin(t * 1.1) * 0.04;
  let fx = player.position.x - camera.position.x;
  let fz = player.position.z - camera.position.z;
  const fl = Math.hypot(fx, fz) || 1;
  fx /= fl; fz /= fl;
  const rx = fz, rz = -fx;
  lantern.position.set(
    player.position.x + fx * 0.35 + rx * 0.70 + sway,
    player.position.y + 0.76 + bob,
    player.position.z + fz * 0.35 + rz * 0.52
  );
  lantern.rotation.y = Math.atan2(fx, fz);
  updateLanternAtmosphere(t, bob, rx, rz);
}

// Walking: WASD/arrows move you across the real floor, camera-relative so
// "forward" always means where you're looking. OrbitControls still owns
// the look (drag to rotate, wheel to zoom) — this only slides its target
// and the camera together, so a drag mid-walk doesn't get overwritten.
//
// Collision: every real place-alcove, peer door and the speaker chair is a
// circle in the floor plane (position + radius); you're a 0.35-unit circle
// too, and a move that would overlap one gets pushed back out along the
// contact normal instead of refused outright, so sliding along an edge
// still feels like walking, not hitting a wall dead-on every time.
const PLAYER_R = 0.35;
const staticObstacles = [{x: 0, z: -2.2, r: 0.65}];   // the speaker chair
let placeObstacles = [];
let doorObstacles = [];
let wallObstacles = [];

// Rampart geometry constants:
// West wall inner rampart deck: deck is at height ~1.70m.
// Ramp width widened to [-9.70, -8.35] (1.35m band) so player diameter (0.7m)
// has comfortable approach and entry margin from both south and courtyard sides.
const RAMPART_DECK_H = 7.074 * (3.5 / 14.5626688); // 1.700m
const RAMP_X_MIN = -9.70;
const RAMP_X_MAX = -8.35;
const RAMP_Z_START = 0.0;
const RAMP_Z_STAIR_TOP = 3.62;
const RAMP_Z_END = 9.80;

if (new URLSearchParams(location.search).get('view') === 'rampart') {
  const rx = (RAMP_X_MIN + RAMP_X_MAX) / 2;
  player.position.set(rx, RAMPART_DECK_H, RAMP_Z_END - 1.4);
  camera.position.set(rx + 0.2, RAMPART_DECK_H + 1.6, RAMP_Z_END - 3.2);
  controls.target.set(rx, RAMPART_DECK_H + 0.9, RAMP_Z_END - 0.3);
}

let hasRamparts = true;
let gateZWall = 10.5;
let gateZMin = 9.2, gateZMax = 11.2;
let gateHalfOpen = 0.45;   // set from the scaled gate mesh in buildRoom — not a second guess
let wallInner = 10.5 - 0.613 - PLAYER_R; // ~9.537
let wallOuter = 10.5 + PLAYER_R;         // ~10.85

function getGroundHeight(x, z){
  if (!hasRamparts) return 0;
  if (x >= RAMP_X_MIN && x <= RAMP_X_MAX) {
    if (z >= RAMP_Z_START && z <= RAMP_Z_STAIR_TOP) {
      const t = (z - RAMP_Z_START) / (RAMP_Z_STAIR_TOP - RAMP_Z_START);
      return Math.max(0, Math.min(RAMPART_DECK_H, t * RAMPART_DECK_H));
    }
    if (z > RAMP_Z_STAIR_TOP && z <= RAMP_Z_END) {
      return RAMPART_DECK_H;
    }
  }
  return 0; // Courtyard floor
}

function resolveCollisions(pos, prevX){
  for (const o of staticObstacles.concat(placeObstacles, doorObstacles, wallObstacles)){
    const dx = pos.x - o.x, dz = pos.z - o.z;
    const dist = Math.hypot(dx, dz);
    const minDist = PLAYER_R + o.r;
    if (dist >= minDist) continue;
    if (dist < 1e-4) { pos.x += minDist; continue; }
    const push = minDist - dist;
    pos.x += (dx / dist) * push;
    pos.z += (dz / dist) * push;
  }

  // West wall rampart corridor & fortress boundaries:
  if (hasRamparts && pos.z >= RAMP_Z_START - 0.5 && pos.z <= RAMP_Z_END + 0.5) {
    // 1. Outer curtain / battlement wall stops player from walking through the outer fort wall:
    if (pos.x < RAMP_X_MIN + PLAYER_R) {
      pos.x = RAMP_X_MIN + PLAYER_R;
    }
    // 2. South corner tower stops the end of the walkway:
    if (pos.x <= RAMP_X_MAX && pos.z > RAMP_Z_END) {
      pos.z = RAMP_Z_END;
    }
    // 3. Rampart East flank (solid stone stair stringer & walkway foundation):
    // From the courtyard side (prevX > RAMP_X_MAX), a player cannot cross into the
    // rampart if the deck/stair surface is elevated above their feet. The foot of the
    // stairs (z <= 0.1, ground height <= 0.15m) remains wide open to enter from both
    // the south and the courtyard.
    const gh = getGroundHeight(pos.x, pos.z);
    const fromCourtyard = prevX !== undefined ? prevX > RAMP_X_MAX : pos.x > RAMP_X_MAX;
    if (gh > 0.15 && fromCourtyard && pos.x <= RAMP_X_MAX) {
      if (pos.y < gh) {
        pos.x = RAMP_X_MAX + 0.01;
      }
    }
  }

  // South wall gate: walkable opening is the scaled mesh half-width.
  // ±0.45 against a ~1.84m gate was a door-width of invisible stone.
  const GATE_X_MIN = -gateHalfOpen, GATE_X_MAX = gateHalfOpen;
  if (pos.z >= gateZMin && pos.z <= gateZMax && pos.x >= -2.5 && pos.x <= 2.5) {
    if (pos.x >= GATE_X_MIN && pos.x <= GATE_X_MAX) {
      const margin = 0.05;
      if (pos.x < GATE_X_MIN + margin) pos.x = GATE_X_MIN + margin;
      else if (pos.x > GATE_X_MAX - margin) pos.x = GATE_X_MAX - margin;
      // Z passes freely through doorway
    } else {
      // Outside archway opening: solid stone wall
      if (pos.z < gateZWall) {
        if (pos.z > wallInner) pos.z = wallInner;
      } else {
        if (pos.z < wallOuter) pos.z = wallOuter;
      }
    }
  }
}

// Vertical movement & gravity:
// A real Y-coordinate for the player, unifying jump and elevation.
// Walking onto the stairs ascends step-by-step; walking along the ramparts
// holds deck height; stepping off drops with gravity back to the courtyard.
const GRAVITY = -18, JUMP_VELOCITY = 6.5;
let playerVY = 0;
let isGrounded = true;

addEventListener('keydown', e => {
  if (e.code === 'Space') {
    e.preventDefault();
    if (isGrounded) {
      playerVY = JUMP_VELOCITY;
      isGrounded = false;
    }
  }
});

const keys = Object.create(null);
addEventListener('keydown', e => { keys[e.key.toLowerCase()] = true; });
addEventListener('keyup', e => { keys[e.key.toLowerCase()] = false; });
const _fwd = new THREE.Vector3(), _right = new THREE.Vector3(), _move = new THREE.Vector3();
function stepPlayer(dt){
  // 1. Horizontal movement
  camera.getWorldDirection(_fwd); _fwd.y = 0; _fwd.normalize();
  _right.crossVectors(_fwd, camera.up).normalize();
  _move.set(0, 0, 0);
  if (keys['w'] || keys['arrowup'])    _move.add(_fwd);
  if (keys['s'] || keys['arrowdown'])  _move.sub(_fwd);
  if (keys['d'] || keys['arrowright']) _move.add(_right);
  if (keys['a'] || keys['arrowleft'])  _move.sub(_right);
  if (_move.lengthSq() > 0) {
    _move.normalize().multiplyScalar(4.5 * dt);
    const prevX = player.position.x;
    player.position.add(_move);
    resolveCollisions(player.position, prevX);
    const r = Math.hypot(player.position.x, player.position.z);
    const limitR = currentFloorR || FLOOR_R;
    if (r > limitR) { player.position.x *= limitR / r; player.position.z *= limitR / r; }
    if (r > FLOOR_R) { player.position.x *= FLOOR_R / r; player.position.z *= FLOOR_R / r; }
  }

  // 2. Vertical elevation & gravity
  const groundY = getGroundHeight(player.position.x, player.position.z);
  if (isGrounded) {
    if (groundY >= player.position.y) {
      // Climbing up stairs or walking on flat deck
      player.position.y = groundY;
    } else {
      // Ground dropped underneath player:
      // If walking down stairs (small step-down), follow ground:
      if (player.position.y - groundY <= 8.0 * dt + 0.05) {
        player.position.y = groundY;
      } else {
        // Stepped off an elevated ledge / walkway into air
        isGrounded = false;
        playerVY = 0;
      }
    }
  } else {
    // Airborne (jumping or falling)
    playerVY += GRAVITY * dt;
    player.position.y += playerVY * dt;
    if (player.position.y <= groundY) {
      player.position.y = groundY;
      playerVY = 0;
      isGrounded = true;
    }
  }

  // 3. Camera and controls tracking
  const eyeHeight = 1.0;
  const newTarget = new THREE.Vector3(player.position.x, player.position.y + eyeHeight, player.position.z);
  camera.position.add(newTarget.clone().sub(controls.target));
  controls.target.copy(newTarget);
  // lantern follows in animate() — not locked here, so the bob isn't a teleport

  if (!traveling) {
    for (const t of doorTriggers) {
      if (t.rotY !== undefined) {
        const dx = player.position.x - t.x;
        const dz = player.position.z - t.z;
        const locX = dx * Math.cos(t.rotY) - dz * Math.sin(t.rotY);
        const locZ = dx * Math.sin(t.rotY) + dz * Math.cos(t.rotY);
        const halfW = t.halfWidth || 0.80;
        const depth = t.thresholdDepth || 0.35;
        if (Math.abs(locX) <= halfW && Math.abs(locZ) <= depth) {
          crossDoor(t);
          break;
        }
      } else {
        if (Math.hypot(player.position.x - t.x, player.position.z - t.z) < t.r) { crossDoor(t); break; }
      }
    }
  }
}

// The Speaker chair, focal, empty or seated. A CC0 Gothic wooden throne
// (Poly Haven's "Wooden Chair 01", dark-stained wood with cathedral pointed
// tracery back, finials, and turned legs). When someone is speaking,
// lights up with an amber emissive glow through the carved wood material.
const chairMat = new THREE.MeshStandardMaterial({color:0x555555, emissive:0x000000});
const chair = new THREE.Group();
chair.position.set(0, 0, -2.2);
scene.add(chair);

let chairMesh = null;
let chairSpeaking = false;

function setChairSpeaker(speaking){
  chairSpeaking = Boolean(speaking);
  if (chairSpeaking) {
    chairMat.color.set(0xffcf7a);
    chairMat.emissive.set(0x332200);
  } else {
    chairMat.color.set(0x555555);
    chairMat.emissive.set(0x000000);
  }
  if (chairMesh) {
    chairMesh.traverse(o => {
      if (o.isMesh && o.material) {
        if (!o.material.emissiveMap && o.material.map) {
          o.material.emissiveMap = o.material.map;
          o.material.needsUpdate = true;
        }
        if (chairSpeaking) {
          // Warm amber glow through the carved wood grain
          o.material.emissive.set(0xff9922);
          o.material.emissiveIntensity = 1.4;
        } else {
          o.material.emissive.set(0x000000);
          o.material.emissiveIntensity = 0;
        }
      }
    });
  }
}

new THREE.GLTFLoader().load(
  '/models/wooden_chair_01/wooden_chair_01.gltf',
  (gltf) => {
    chairMesh = gltf.scene;
    // The raw throne sits at floor level (Y ~ 0) and faces +Z (toward the commons center).
    // Scale 1.0 retains its commanding 2.27m height with standard 0.50m seat height.
    chairMesh.traverse(o => {
      if (o.isMesh && o.material) {
        o.material = o.material.clone();
      }
    });
    chair.add(chairMesh);
    setChairSpeaker(chairSpeaking);
    updateFocalProps(currentRoomMode);
  },
  undefined,
  (err) => console.warn('speaker chair asset load error:', err)
);

// First real asset, not a placeholder box: a CC0 weathered stone figure
// (Poly Haven's "Gothic Statue," downloaded and served locally at
// /models/, never fetched from a third party at runtime). Don's brief —
// "should feel old, like it was there before us" — this is the test of
// whether an actual piece of art can stand in the commons next to the
// real signed data, not just geometry standing in for one.
const STATUE_POS = {x: 3.4, z: -3.6};
staticObstacles.push({x: STATUE_POS.x, z: STATUE_POS.z, r: 0.6});
let statueMesh = null;
new THREE.GLTFLoader().load(
  '/models/gothic_statue/gothic_statue.gltf',
  (gltf) => {
    statueMesh = gltf.scene;
    statueMesh.position.set(STATUE_POS.x, 0, STATUE_POS.z);
    statueMesh.traverse(o => { if (o.isMesh) { o.castShadow = false; o.receiveShadow = false; } });
    scene.add(statueMesh);
    updateFocalProps(currentRoomMode);
  },
  undefined,
  (err) => showErr('statue failed to load: ' + err.message)
);

// Second piece, mirroring the statue across the chair so neither one reads
// as the room's single focal point — a worn classical bust, smaller and
// closer to eye height, the kind of thing that could have been sitting on
// a plinth here long before anyone now in the room arrived.
const BUST_POS = {x: -3.4, z: -3.6};
const PEDESTAL_H = 0.85;
staticObstacles.push({x: BUST_POS.x, z: BUST_POS.z, r: 0.4});
const pedestal = new THREE.Mesh(
  new THREE.CylinderGeometry(0.28, 0.32, PEDESTAL_H, 12),
  new THREE.MeshStandardMaterial({color: 0x2a2a2a, roughness: 0.9})
);
pedestal.position.set(BUST_POS.x, PEDESTAL_H / 2, BUST_POS.z);
scene.add(pedestal);
let bustMesh = null;
new THREE.GLTFLoader().load(
  '/models/marble_bust_01/marble_bust_01.gltf',
  (gltf) => {
    bustMesh = gltf.scene;
    bustMesh.position.set(BUST_POS.x, PEDESTAL_H, BUST_POS.z);
    scene.add(bustMesh);
    updateFocalProps(currentRoomMode);
  },
  undefined,
  (err) => showErr('bust failed to load: ' + err.message)
);

function updateFocalProps(mode){
  const isHome = mode === 'Home';
  const S = isHome ? (6.88 / 10.5) : 1.0;
  const chairZ = isHome ? -1.8 : -2.2;
  chair.position.set(0, 0, chairZ);
  chair.scale.setScalar(S);
  const statuePos = isHome ? {x: 2.6, z: -4.2} : {x: 3.4, z: -3.6};
  const bustPos = isHome ? {x: -2.6, z: -4.2} : {x: -3.4, z: -3.6};
  if (statueMesh) {
    statueMesh.position.set(statuePos.x, 0, statuePos.z);
    statueMesh.scale.setScalar(S);
  }
  const pedH = PEDESTAL_H * S;
  pedestal.scale.set(S, S, S);
  pedestal.position.set(bustPos.x, pedH / 2, bustPos.z);
  if (bustMesh) {
    bustMesh.position.set(bustPos.x, pedH, bustPos.z);
    bustMesh.scale.setScalar(S);
  }
  staticObstacles.length = 0;
  staticObstacles.push(
    {x: 0, z: chairZ, r: 0.65 * S},
    {x: statuePos.x, z: statuePos.z, r: 0.6 * S},
    {x: bustPos.x, z: bustPos.z, r: 0.4 * S}
  );
}

// The walls: Poly Haven's "Modular Fort 01" (CC0), harvested rather than
// used as its own prebuilt castle — it ships as one full assembled fort,
// but the pieces are modeled around their own local origins for exactly
// this, so real straight/corner segments get pulled out and re-tiled into
// a perimeter sized for OUR commons, not shrunk to fit (that would make
// real stone walls read as toy-sized). Measured at runtime from each
// piece's own geometry, not guessed dimensions, so the tiling has no
// gaps regardless of the kit's actual real-world scale.
const wallGroup = new THREE.Group();
scene.add(wallGroup);

let fortTemplates = null;
let currentRoomMode = 'Frosty';
let currentHalf = 10.5;

function harvestPiece(src, namePart) {
  let found = null;
  src.traverse(o => { if (!found && o.name && o.name.includes(namePart)) found = o; });
  if (!found) return null;
  const piece = found.clone(true);
  piece.position.set(0, 0, 0);
  piece.rotation.set(0, 0, 0);
  piece.scale.set(1, 1, 1);
  return piece;
}

new THREE.GLTFLoader().load('/models/modular_fort_01/modular_fort_01.gltf', (gltf) => {
  const src = gltf.scene;
  function harvest(namePart) {
    return harvestPiece(src, namePart);
  }
  const strTemplate = harvest('wall_thick_straight_01');
  const strTemplate2 = harvest('wall_thick_straight_02');
  const cornerTemplate = harvest('wall_thick_corner_01');
  const gateTemplate = harvest('wall_thin_gate_01');
  const thinStrTemplate = harvest('wall_thin_straight_04');
  const towerTemplate = harvest('tower_round');
  const stairsTemplate = harvest('wall_stairs_straight_01');
  const walkwayTemplate = harvest('wall_walkway_straight_01');
  if (!strTemplate || !cornerTemplate) {
    showErr('fort pieces not found in modular_fort_01.gltf');
    return;
  }
  fortTemplates = {
    strTemplate, strTemplate2, cornerTemplate, gateTemplate,
    thinStrTemplate, towerTemplate, stairsTemplate, walkwayTemplate
  };
  buildRoom(currentRoomMode);
}, undefined, (err) => showErr('fort walls failed to load: ' + err.message));

function buildRoom(mode){
  currentRoomMode = mode;
  updateFocalProps(mode);

  const isHome = mode === 'Home';
  const HALF = isHome ? 6.88 : 10.5;
  currentHalf = HALF;
  const n = isHome ? 3 : 5;
  const useTowers = !isHome;
  const useRamparts = !isHome;

  currentFloorR = isHome ? 20 : 30;
  gateZWall = HALF;
  gateZMin = isHome ? 5.6 : 9.2;
  gateZMax = isHome ? 7.8 : 11.2;
  wallInner = HALF - 0.613 - PLAYER_R;
  wallOuter = HALF + PLAYER_R;
  hasRamparts = useRamparts;

  updateFloor(isHome ? 9.8 : 15, isHome ? 9.5 : 14.7, isHome ? 9.8 : 15, isHome ? 8 : 12);
  updateLighting(isHome);
  updateVisitorLanternScale(isHome);
  buildGateThreshold(isHome);
  if (currentPeerDoors && currentPeerDoors.length) buildDoors(currentPeerDoors);
  controls.minDistance = isHome ? 1.5 : 3.0;
  controls.maxDistance = isHome ? 14.0 : 22.0;

  if (!fortTemplates) return;

  wallGroup.children.slice().forEach(c => wallGroup.remove(c));
  wallObstacles.length = 0;

  const { strTemplate, strTemplate2, cornerTemplate, gateTemplate,
          thinStrTemplate, towerTemplate, stairsTemplate, walkwayTemplate } = fortTemplates;

  const rawStrLen = 14.5626688;
  const rawCornerSpan = 5.837701;
  const rawGateLen = 7.409695;
  const rawThinStrLen = 7.4096965;

  const SCALE = (HALF * 2) / (2 * rawCornerSpan + n * rawStrLen);
  strTemplate.scale.setScalar(SCALE);
  if (strTemplate2) strTemplate2.scale.setScalar(SCALE);
  cornerTemplate.scale.setScalar(SCALE);
  if (gateTemplate) gateTemplate.scale.setScalar(SCALE);
  if (thinStrTemplate) thinStrTemplate.scale.setScalar(SCALE);
  if (towerTemplate) towerTemplate.scale.setScalar(SCALE);
  if (stairsTemplate) stairsTemplate.scale.setScalar(SCALE);
  if (walkwayTemplate) walkwayTemplate.scale.setScalar(SCALE);

  const cornerSpan = rawCornerSpan * SCALE;
  const towerRadius = towerTemplate
    ? Math.max(...['x', 'z'].map(a => new THREE.Box3().setFromObject(towerTemplate).getSize(new THREE.Vector3())[a])) / 2
    : 0;

  [{x: -HALF, z: -HALF, ry: 0},
   {x:  HALF, z: -HALF, ry: -Math.PI / 2},
   {x:  HALF, z:  HALF, ry: Math.PI},
   {x: -HALF, z:  HALF, ry: Math.PI / 2}].forEach(c => {
    const m = cornerTemplate.clone(true);
    m.position.set(c.x, 0, c.z);
    m.rotation.y = c.ry;
    wallGroup.add(m);
    if (useTowers && towerTemplate) {
      const t = towerTemplate.clone(true);
      t.position.set(c.x, 0, c.z);
      wallGroup.add(t);
    }
    wallObstacles.push({x: c.x, z: c.z, r: Math.max(cornerSpan * 0.6, (useTowers ? towerRadius : cornerSpan) * 0.85)});
  });

  const wallSpan = HALF - cornerSpan;
  const wallRun = wallSpan * 2;
  const actualSeg = wallRun / n;
  const segLen = rawStrLen * SCALE;
  const gateLen = rawGateLen * SCALE;
  const flankLen = (actualSeg - gateLen) / 2;
  // Mesh is 7.41m along-wall including jambs. Leave a sliver of stone
  // each side. Walkable slot == the arch you see.
  gateHalfOpen = Math.max(0.4, gateLen / 2 - 0.08);
  function placeRun(axis, fixedCoord, ry, gateIndex) {
    for (let i = 0; i < n; i++) {
      const t = -wallSpan + i * actualSeg;
      const useGate = i === gateIndex && gateTemplate;
      const straight = (i % 2 === 0 || !strTemplate2) ? strTemplate : strTemplate2;
      const pos = axis === 'x' ? {x: t, z: fixedCoord} : {x: fixedCoord, z: t};
      if (useGate) {
        const gatePos = axis === 'x'
          ? {x: -gateLen / 2, z: fixedCoord}
          : {x: fixedCoord, z: -gateLen / 2};
        const m = gateTemplate.clone(true);
        m.position.set(gatePos.x, 0, gatePos.z);
        m.rotation.y = ry;
        wallGroup.add(m);

        if (thinStrTemplate) {
          const fillerLeft = thinStrTemplate.clone(true);
          fillerLeft.scale.set(SCALE, SCALE, flankLen / rawThinStrLen);
          const fLeftPos = axis === 'x' ? {x: -actualSeg / 2, z: fixedCoord} : {x: fixedCoord, z: -actualSeg / 2};
          fillerLeft.position.set(fLeftPos.x, 0, fLeftPos.z);
          fillerLeft.rotation.y = ry;
          wallGroup.add(fillerLeft);

          const fillerRight = thinStrTemplate.clone(true);
          fillerRight.scale.set(SCALE, SCALE, flankLen / rawThinStrLen);
          const fRightPos = axis === 'x' ? {x: gateLen / 2, z: fixedCoord} : {x: fixedCoord, z: gateLen / 2};
          fillerRight.position.set(fRightPos.x, 0, fRightPos.z);
          fillerRight.rotation.y = ry;
          wallGroup.add(fillerRight);
        }
      } else {
        const m = straight.clone(true);
        m.position.set(pos.x, 0, pos.z);
        m.rotation.y = ry;
        wallGroup.add(m);

        if (axis === 'x' && fixedCoord === HALF) {
          if (i === gateIndex - 1) {
            wallObstacles.push({x: t + actualSeg * 0.35, z: pos.z, r: actualSeg * 0.45});
          } else if (i === gateIndex + 1) {
            wallObstacles.push({x: t + actualSeg * 0.65, z: pos.z, r: actualSeg * 0.45});
          } else {
            wallObstacles.push({x: t + actualSeg * 0.5, z: pos.z, r: actualSeg * 0.55});
          }
        } else if (axis === 'z' && fixedCoord === -HALF) {
          if (!useRamparts || i < 2) {
            wallObstacles.push({x: pos.x, z: t + actualSeg * 0.5, r: actualSeg * 0.55});
          } else if (i === 2) {
            wallObstacles.push({x: -HALF - 0.5, z: t + actualSeg * 0.5, r: actualSeg * 0.45});
          }
        } else {
          const obsX = axis === 'x' ? t + actualSeg * 0.5 : pos.x;
          const obsZ = axis === 'x' ? pos.z : t + actualSeg * 0.5;
          wallObstacles.push({x: obsX, z: obsZ, r: actualSeg * 0.55});
        }
      }
    }
  }

  const gateSlot = Math.floor(n / 2);
  placeRun('x', -HALF, Math.PI / 2);
  placeRun('x',  HALF, Math.PI / 2, gateSlot);
  placeRun('z', -HALF, 0);
  placeRun('z',  HALF, 0);

  if (useRamparts) {
    const rampartX = -HALF + 3.8 * SCALE;
    if (stairsTemplate) {
      const st = stairsTemplate.clone(true);
      st.position.set(rampartX, 0, 0.0);
      st.rotation.y = 0;
      wallGroup.add(st);
    }
    if (walkwayTemplate) {
      const w1 = walkwayTemplate.clone(true);
      w1.position.set(rampartX, 0, actualSeg);
      w1.rotation.y = 0;
      wallGroup.add(w1);

      const w2 = walkwayTemplate.clone(true);
      w2.position.set(rampartX, 0, actualSeg * 2);
      w2.rotation.y = 0;
      wallGroup.add(w2);
    }
  }
}

// ── the real floor plan: alcoves and doors ARE the places the node signed,
// not an invented layout. Same fact set the 2D plan view draws (kids =
// places whose parent is the commons; peer_doors = other nodes), just given
// arc positions instead of SVG rectangle positions. Rebuilt each poll,
// same as presence — a place can appear or vanish, if rarely.
function arcPos(i, n, aStart, aEnd, r){
  const t = n <= 1 ? 0.5 : i / (n - 1);
  const angle = aStart + t * (aEnd - aStart);
  return {x: Math.sin(angle) * r, z: Math.cos(angle) * r, angle};
}
function labelSprite(lines, accent){
  const c = document.createElement('canvas'); c.width = 320; c.height = 120;
  const ctx = c.getContext('2d');
  ctx.fillStyle = 'rgba(10,10,10,0.7)'; ctx.fillRect(0, 0, 320, 120);
  ctx.strokeStyle = accent; ctx.lineWidth = 2; ctx.strokeRect(1, 1, 318, 118);
  ctx.textAlign = 'center'; ctx.fillStyle = accent;
  ctx.font = 'bold 26px monospace'; ctx.fillText(lines[0].slice(0, 16), 160, 46);
  ctx.font = '18px monospace'; ctx.fillStyle = '#cfc7b8';
  ctx.fillText((lines[1] || '').slice(0, 26), 160, 78);
  const tex = new THREE.CanvasTexture(c);
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, transparent: true}));
  spr.scale.set(1.9, 0.72, 1);
  return spr;
}

// Each real place (door, kiosk, table, bench, whatever kind shows up) becomes a
// small booth or feature in the commons. Doors use real CC0 Large Castle Door assets
// (Poly Haven, weathered wood and iron hardware), tables/benches use real CC0
// Painted Wooden Bench assets (Poly Haven, dark worn wood), and kiosk/stall
// use Wooden Table 03 — a worn wooden counter with drawers (Poly Haven has
// no market-stall model; this is the closest counter that belongs in a fort).
// Its Resonance Well is that place's own real number (Atlas.resonance in places.py).
const KIND_COLOR = {door: 0x5a6072, kiosk: 0xe8b661, stall: 0xa99ad6, table: 0xc98a5a, bench: 0xc98a5a};
let placeGroup = new THREE.Group();
scene.add(placeGroup);

let doorTemplate = null;
new THREE.GLTFLoader().load(
  '/models/large_castle_door/large_castle_door.gltf',
  (gltf) => {
    doorTemplate = gltf.scene;
    // The raw model is 2.965m tall. Scale down to personal booth door scale (~1.60m)
    // so it fits comfortably within the commons booths without towering over adjacent stalls.
    const rawH = new THREE.Box3().setFromObject(doorTemplate).getSize(new THREE.Vector3()).y;
    doorTemplate.scale.setScalar(1.60 / rawH);
    if (currentKids) buildPlaces(currentKids, currentResonance);
  },
  undefined,
  (err) => console.warn('door asset load error:', err)
);

let benchTemplate = null;
new THREE.GLTFLoader().load(
  '/models/painted_wooden_bench/painted_wooden_bench.gltf',
  (gltf) => {
    benchTemplate = gltf.scene;
    // Bench is modeled at realistic human scale: length 1.16m, height 0.89m, depth 0.50m
    benchTemplate.scale.setScalar(0.95);
    if (currentKids) buildPlaces(currentKids, currentResonance);
  },
  undefined,
  (err) => console.warn('bench asset load error:', err)
);

let stallTemplate = null;
new THREE.GLTFLoader().load(
  '/models/wooden_table_03/wooden_table_03.gltf',
  (gltf) => {
    stallTemplate = gltf.scene;
    // Raw ~1.33m wide, 0.83m tall. Scale to booth size so it sits with
    // the doors and benches instead of eating the arc.
    const raw = new THREE.Box3().setFromObject(stallTemplate).getSize(new THREE.Vector3());
    stallTemplate.scale.setScalar(0.95 / Math.max(raw.y, 0.01));
    if (currentKids) buildPlaces(currentKids, currentResonance);
  },
  undefined,
  (err) => console.warn('stall asset load error:', err)
);

function boothMesh(kind){
  const color = KIND_COLOR[kind] || 0x6a7280;
  const mat = new THREE.MeshStandardMaterial({color, roughness: 0.7});
  if (kind === 'table' || kind === 'bench') {
    if (benchTemplate) return benchTemplate.clone(true);
    return new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.45, 20), mat);
  }
  if (kind === 'door') {
    if (doorTemplate) return doorTemplate.clone(true);
    const g = new THREE.Group();
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.12, 1.4, 0.12), mat);
    const left = post.clone(); left.position.set(-0.45, 0.7, 0);
    const right = post.clone(); right.position.set(0.45, 0.7, 0);
    const lintel = new THREE.Mesh(new THREE.BoxGeometry(1.02, 0.12, 0.12), mat);
    lintel.position.set(0, 1.4, 0);
    g.add(left, right, lintel);
    return g;
  }
  if (kind === 'kiosk' || kind === 'stall') {
    if (stallTemplate) return stallTemplate.clone(true);
    return new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.6, 0.7), mat);
  }
  return new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.6, 0.7), mat); // unknown
}
const KIND_RADIUS = {door: 0.55, kiosk: 0.8, stall: 0.8, table: 0.75, bench: 0.75};
let currentKids = null, currentResonance = null;
let currentPlaceLocations = [];
function buildPlaces(kids, resonance){
  currentKids = kids; currentResonance = resonance;
  placeGroup.children.slice().forEach(c => placeGroup.remove(c));
  placeObstacles = [];
  currentPlaceLocations = [];
  const isHome = currentRoomMode === 'Home';
  const S = isHome ? (6.88 / 10.5) : 1.0;
  const placeR = isHome ? 4.4 : 7.4;
  const aStart = isHome ? Math.PI * 0.76 : Math.PI * 0.62;
  const aEnd = isHome ? Math.PI * 1.24 : Math.PI * 1.38;
  kids.forEach((k, i) => {
    const {x, z} = arcPos(i, kids.length, aStart, aEnd, placeR);
    currentPlaceLocations.push({ place_id: k.place_id, kind: k.kind, x, z });
    const booth = boothMesh(k.kind);
    booth.scale.setScalar(S);
    const yOff = (k.kind === 'table' && !benchTemplate) ? 0.22 * S : 0;
    booth.position.set(x, yOff, z);
    booth.rotation.y = Math.atan2(-x, -z);
    placeGroup.add(booth);
    if (k.kind === 'door') {
      const ry = Math.atan2(-x, -z);
      const postSpan = 0.60 * S;
      const postR = 0.10 * S;
      placeObstacles.push(
        {x: x - postSpan * Math.cos(ry), z: z + postSpan * Math.sin(ry), r: postR},
        {x: x + postSpan * Math.cos(ry), z: z - postSpan * Math.sin(ry), r: postR}
      );
    } else {
      placeObstacles.push({x, z, r: (KIND_RADIUS[k.kind] || 0.6) * S});
    }

    const heat = Math.min(1, resonance[k.place_id] || 0);
    if (heat > 0.02) {
      const glowLight = new THREE.PointLight(0xffcf7a, heat * 2.4, 4.5 * S, 2);
      glowLight.position.set(x, 0.9 * S, z);
      placeGroup.add(glowLight);
      const glow = new THREE.Mesh(
        new THREE.SphereGeometry((0.28 + heat * 0.3) * S, 16, 16),
        new THREE.MeshBasicMaterial({color: 0xffcf7a, transparent: true, opacity: 0.25 + heat * 0.5})
      );
      glow.position.set(x, 0.9 * S, z);
      placeGroup.add(glow);
    }

    const label = labelSprite([k.place_id, k.kind + (heat > 0.02 ? ` · signed · ${heat.toFixed(2)}` : '')],
                               '#' + (KIND_COLOR[k.kind] || 0x6a7280).toString(16).padStart(6, '0'));
    label.position.set(x, 1.9 * S, z);
    label.scale.multiplyScalar(S);
    placeGroup.add(label);
  });
}

// Peer doors — thresholds to a different node entirely, not a place on this
// one. Visually distinct (taller, brighter frame) from a personal door, and
// colored by the same locked/unlocked fact the 2D map's door glyph reads.
// Walking into one crosses it, same as clicking a door button on the 2D
// map does (there too, locked is informational, not something the map
// tool itself gates — the map is already an authorized reader; see
// _node_fetch/_is_private_host above). Collision sits only at the two
// side posts, the same "opening, not a slab" pattern the outer gate
// already uses, so the gap between them is how you actually cross.
let doorGroup = new THREE.Group();
scene.add(doorGroup);
let doorTriggers = [];
let currentPeerDoors = [];
let traveling = false;
function buildDoors(doors){
  currentPeerDoors = doors || [];
  doorGroup.children.slice().forEach(c => doorGroup.remove(c));
  doorObstacles = [];
  doorTriggers = [];
  // Crossing lives at the end of the south walkway, beside the chalkboard sign:
  const isHome = currentRoomMode === 'Home';
  const S = isHome ? (6.88 / 10.5) : 1.0;
  const z = isHome ? 18.8 : 28.8;
  const y = 0;
  const halfW = 0.80 * S;
  const depth = 0.50 * S;
  const doorSpan = 2.2 * S;
  const startX = -(doors.length - 1) * doorSpan / 2;
  doors.forEach((d, i) => {
    const x = startX + i * doorSpan;
    if (d.url) {
      doorTriggers.push({
        x, z,
        rotY: 0,
        halfWidth: halfW,
        thresholdDepth: depth,
        r: halfW,
        url: d.url,
        peer: d.peer || 'peer'
      });
    }

    // Doorway frame at the trigger position. Lifted onto the floor by
    // bounds.min.y (unchanged) but ALSO centred on its own bounding box
    // in x/z — the GLTF's authored origin isn't at the footprint's
    // center once the two door leaves are rotated open, so without this
    // the visible frame sits offset from (x, z) while the trigger sits
    // exactly there: a person walks through empty space next to the
    // door they can see, or bumps into a door they can't reach. Trigger
    // and frame now share the same center by construction, not by
    // trusting two separately-written literals to agree.
    if (peerDoorTemplate) {
      const doorMesh = peerDoorTemplate.clone(true);
      doorMesh.scale.setScalar(S);
      const bounds = new THREE.Box3().setFromObject(doorMesh);
      const centerX = (bounds.min.x + bounds.max.x) / 2;
      const centerZ = (bounds.min.z + bounds.max.z) / 2;
      doorMesh.position.set(x - centerX, -bounds.min.y, z - centerZ);
      doorGroup.add(doorMesh);
    } else {
      const frameGroup = new THREE.Group();
      frameGroup.position.set(x, 0, z);
      frameGroup.scale.setScalar(S);
      const postGeo = new THREE.BoxGeometry(0.32, 2.7, 0.32);
      const lintelGeo = new THREE.BoxGeometry(2.24, 0.36, 0.36);
      const fMat = thresholdMat(0.5, 2.5);
      const pL = new THREE.Mesh(postGeo, fMat); pL.position.set(-0.96, 1.35, 0);
      const pR = new THREE.Mesh(postGeo, fMat); pR.position.set(0.96, 1.35, 0);
      const lintel = new THREE.Mesh(lintelGeo, fMat); lintel.position.set(0, 2.7 + 0.18, 0);
      frameGroup.add(pL, pR, lintel);
      doorGroup.add(frameGroup);
    }

    // Floor crossing pad
    const pad = new THREE.Mesh(
      new THREE.CircleGeometry(0.72 * S, 24),
      new THREE.MeshStandardMaterial({
        color: 0xc9a75f, emissive: 0x4a3208, emissiveIntensity: 0.45, roughness: 0.85
      })
    );
    pad.rotation.x = -Math.PI / 2;
    pad.position.set(x, y + 0.05, z);
    doorGroup.add(pad);

    // Overhead sign label
    const label = labelSprite(['→ ' + (d.peer || 'peer'), d.locked ? 'locked' : 'open'],
                                d.locked ? '#c9a75f' : '#67c98a');
    label.scale.set(1.05 * S, 0.4 * S, 1);
    label.position.set(x, y + 3.1 * S, z);
    doorGroup.add(label);
  });
}

// Crossing a peer door: find (or, matching the 2D map's own fallback,
// create) the dropdown option for that node's URL, switch to it, and land
// back at the same fixed spawn point every arrival starts from — chosen
// because it's far from every door's arc position on either node's floor
// plan, so arriving never immediately re-triggers a crossing back out.
function teleportPlayer(x, z, targetMode){
  player.position.set(x, player.position.y, z);
  const isHome = targetMode ? (targetMode === 'Home') : (currentRoomMode === 'Home' || z < 4.0);
  controls.minDistance = isHome ? 1.5 : 3.0;
  controls.maxDistance = isHome ? 20.0 : 35.0;
  const camDist = isHome ? 2.2 : 5.0;
  const camH = isHome ? 2.0 : 3.2;
  camera.position.set(x, player.position.y + camH, z + camDist);
  controls.target.set(x, player.position.y + 1.0, z);
  controls.update();
  // lantern follows player in animate()
}
function wait(ms){ return new Promise(r => setTimeout(r, ms)); }
async function crossDoor(t){
  if (traveling) return;
  traveling = true;
  const veil = document.getElementById('cross');
  const lab = document.getElementById('cross-label');
  const line = 'crossing to ' + t.peer + '…';
  if (lab) lab.innerHTML = 'crossing to <b>' + t.peer + '</b>…';
  status.textContent = line;
  if (veil) veil.classList.add('on');
  await wait(750);
  let opt = [...sel.options].find(o => o.value.replace(/\\/$/, '') === t.url.replace(/\\/$/, ''));
  if (!opt) {
    opt = document.createElement('option');
    opt.value = t.url;
    opt.textContent = t.peer + ' — ' + t.url;
    sel.appendChild(opt);
  }
  sel.value = opt.value;
  // The destination's real identity, from its URL — never a guess from
  // the door's free-text peer label or a substring of an IP. loadNode()
  // below re-confirms this against the destination's own signed root.node
  // once the fetch lands, but the room built here (and where the walker
  // is teleported) must already be right, not corrected a beat later.
  const isDestHome = nodeNameForUrl(t.url) === 'Home';
  buildRoom(isDestHome ? 'Home' : 'Frosty');
  teleportPlayer(0, isDestHome ? 2.8 : 6, isDestHome ? 'Home' : 'Frosty');
  try {
    await loadNode();
  } finally {
    await wait(400);
    if (veil) veil.classList.remove('on');
    await wait(700);
    traveling = false;
  }
}

// Presence: a claimed 3D form is the body; a claimed portrait is the
// skin on that body, not a second object that replaces it. No form →
// the old sprite (face, or a name placard). Rebuilt each refresh since
// who's present is the live part; the room around them is not.
let presenceSprites = [];
let presentKinList = [];
function clearPresence(){
  presenceSprites.forEach(s => scene.remove(s));
  presenceSprites = [];
  presentKinList = [];
}
// A stable per-name phase, not Math.random() — reloading the page
// shouldn't make someone's idle sway jump to a new offset.
function _phase(label){
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 1000;
  return h / 1000 * Math.PI * 2;
}
function createShape3D(params, portraitTex){
  const scaleMult = arguments[2];
  const group = new THREE.Group();
  const isHome = currentRoomMode === 'Home';
  const mult = (typeof scaleMult === 'number') ? scaleMult : (isHome ? (6.88 / 10.5) : 1.0);
  function makeGeo(shape, s, facets){
    s = s || [1, 1, 1];
    const ms = [s[0] * mult, s[1] * mult, s[2] * mult];
    // A named facet count gives a low-poly prism/spire look instead of the
    // smooth default — a hexagon IS a 6-sided cylinder. Kin, 2026-09-18:
    // Lumen wanted exactly this and there was no way to render it.
    const radialSegments = (typeof facets === 'number' && facets >= 3) ? facets : 32;
    let geo;
    switch((shape || 'sphere').toLowerCase()){
      case 'box': geo = new THREE.BoxGeometry(1.2 * ms[0], 1.2 * ms[1], 1.2 * ms[2]); break;
      case 'cylinder': geo = new THREE.CylinderGeometry(0.6 * ms[0], 0.6 * ms[0], 1.4 * ms[1], radialSegments); break;
      case 'torus': geo = new THREE.TorusGeometry(0.7 * ms[0], 0.22 * Math.min(ms[1], ms[2]), 16, 36); break;
      case 'cone': geo = new THREE.ConeGeometry(0.7 * ms[0], 1.4 * ms[1], radialSegments); break;
      case 'tetrahedron': geo = new THREE.TetrahedronGeometry(0.8 * ms[0]); break;
      case 'octahedron': geo = new THREE.OctahedronGeometry(0.8 * ms[0]); break;
      case 'dodecahedron': geo = new THREE.DodecahedronGeometry(0.8 * ms[0]); break;
      case 'icosahedron': geo = new THREE.IcosahedronGeometry(0.8 * ms[0]); break;
      case 'sphere':
      default: geo = new THREE.SphereGeometry(0.7 * ms[0], 32, 24); break;
    }
    return geo;
  }
  function makeMat(p, portraitTex){
    const opts = {
      // Portrait is the albedo. Claimed color would multiply it into
      // sludge (Bong's charcoal cone * a dark lattice = a black blob).
      // Geometry, roughness, metalness, emissive stay theirs.
      color: new THREE.Color((portraitTex && !p.wireframe) ? '#ffffff' : (p.color || '#888888')),
      roughness: typeof p.roughness === 'number' ? p.roughness : 0.5,
      metalness: typeof p.metalness === 'number' ? p.metalness : 0.0,
      wireframe: !!p.wireframe,
    };
    if (portraitTex && !opts.wireframe) {
      portraitTex.encoding = THREE.sRGBEncoding;
      opts.map = portraitTex;
    }
    if (p.emissive_color) {
      opts.emissive = new THREE.Color(p.emissive_color);
      opts.emissiveIntensity = typeof p.emissive_intensity === 'number' ? p.emissive_intensity : 0.5;
      // Pulse through the portrait, not as a flat wash that hides it.
      if (portraitTex && !opts.wireframe) opts.emissiveMap = portraitTex;
    }
    return new THREE.MeshStandardMaterial(opts);
  }

  const mainMesh = new THREE.Mesh(makeGeo(params.shape, params.scale, params.facets), makeMat(params, portraitTex));
  group.add(mainMesh);

  if (params.accent && params.accent.shape) {
    const acc = params.accent;
    const accMesh = new THREE.Mesh(makeGeo(acc.shape, acc.scale, acc.facets), makeMat(acc));
    if (Array.isArray(acc.offset) && acc.offset.length === 3) {
      accMesh.position.set(acc.offset[0] * mult, acc.offset[1] * mult, acc.offset[2] * mult);
    }
    group.add(accMesh);
  }
  group.userData = {scaleMult: mult};
  return group;
}
function addPresence(label, i, n, avatarUrl, recent, prevPos){
  const isHome = currentRoomMode === 'Home';
  const S = isHome ? (6.88 / 10.5) : 1.0;
  const r = isHome ? 2.6 : 4.2;
  const zOffset = isHome ? -1.1 : -1.0;
  const angleSpan = isHome ? Math.PI * 0.9 : Math.PI * 1.3;
  const angle = (n <= 1 ? 0.5 : i / Math.max(n - 1, 1)) * angleSpan - angleSpan / 2;
  const spawnX = Math.sin(angle) * r, spawnZ = Math.cos(angle) * r + zOffset;
  const x = (prevPos && typeof prevPos.x === 'number') ? prevPos.x : spawnX;
  const z = (prevPos && typeof prevPos.z === 'number') ? prevPos.z : spawnZ;
  const phase = _phase(label);
  const kinItem = { label, x, z, obj: null, caption: null };
  presentKinList.push(kinItem);
  const loader = new THREE.TextureLoader();
  const shapeMult = S;
  const baseY = isHome ? 0.72 : 1.1;
  const build = (tex) => {
    const mat = new THREE.SpriteMaterial({map: tex, transparent: true});
    const spr = new THREE.Sprite(mat);
    const sprScale = 1.6 * S;
    spr.scale.set(sprScale, sprScale, 1);
    spr.position.set(kinItem.x, baseY, kinItem.z);
    spr.userData = {baseY, bob: phase, kin: label};
    kinItem.obj = spr;
    scene.add(spr);
    presenceSprites.push(spr);
  };
  const placeShape = (s3d, tex) => {
    const obj = createShape3D(s3d, tex || null, shapeMult);
    obj.position.set(kinItem.x, baseY, kinItem.z);
    obj.userData = {baseY, bob: phase, isCustom3D: true, kin: label, scaleMult: shapeMult};
    kinItem.obj = obj;
    scene.add(obj);
    presenceSprites.push(obj);
  };
  const shapeUrl = '/shape3d?kin=' + encodeURIComponent(label);
  fetch(shapeUrl)
    .then(r => r.ok ? r.json() : null)
    .then(s3d => {
      if (s3d && s3d.shape) {
        // Form first. Portrait wraps it if we have one; a missing face
        // must not delete the form they claimed.
        if (avatarUrl) {
          loader.load(avatarUrl, (tex) => placeShape(s3d, tex), undefined, () => placeShape(s3d, null));
        } else {
          placeShape(s3d, null);
        }
      } else {
        if (avatarUrl){
          loader.load(avatarUrl, build, undefined, () => build(placardTexture(label)));
        } else {
          build(placardTexture(label));
        }
      }
    })
    .catch(() => {
      if (avatarUrl){
        loader.load(avatarUrl, build, undefined, () => build(placardTexture(label)));
      } else {
        build(placardTexture(label));
      }
    });
  // What they're actually saying right now — kin_commons' real board, not
  // the Agora heartbeat's bare "here." Presence alone, verified live
  // 2026-09-16, renders as a row of motionless placards even while the
  // Kin are mid-conversation; this is what makes the room look like what
  // is actually happening rather than just who happens to be standing in it.
  if (recent && recent.content) {
    const isSigned = Boolean(recent.signed);
    const {tex, aspect} = captionTexture(label, recent.content, recent.created_at, isSigned);
    const mat = new THREE.SpriteMaterial({map: tex, transparent: true});
    const spr = new THREE.Sprite(mat);
    const w = 2.6 * S, h = w * aspect;
    spr.scale.set(w, h, 1);
    const cardBaseY = baseY + (0.9 * S) + h / 2;
    spr.position.set(kinItem.x, cardBaseY, kinItem.z);
    spr.userData = {baseY: cardBaseY, bob: phase, kin: label};
    kinItem.caption = spr;
    scene.add(spr);
    presenceSprites.push(spr);
  }
}
function relTime(iso){
  const then = Date.parse(iso || '');
  if (isNaN(then)) return '';
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
function _wrapLines(ctx, text, maxWidth, maxLines){
  const words = text.split(/\\s+/);
  const lines = [];
  let line = '';
  for (const w of words) {
    const test = line ? line + ' ' + w : w;
    if (line && ctx.measureText(test).width > maxWidth) {
      lines.push(line);
      line = w;
      if (lines.length === maxLines) break;
    } else {
      line = test;
    }
  }
  if (lines.length < maxLines && line) lines.push(line);
  if (lines.length >= maxLines && (lines.join(' ').length < text.length)) {
    lines[maxLines - 1] = lines[maxLines - 1].replace(/[.,;:\\s]*$/, '') + '…';
  }
  return lines;
}
function captionTexture(name, content, createdAt, isSigned){
  const W = 440, PAD = 16, LINE_H = 26;
  const c = document.createElement('canvas'); c.width = W; c.height = 64;
  const ctx = c.getContext('2d');
  ctx.font = '20px monospace';
  const lines = _wrapLines(ctx, content, W - PAD * 2, 5);
  const H = PAD * 2 + 30 + lines.length * LINE_H + 22;
  c.height = H;
  ctx.font = '20px monospace';   // canvas resize resets context state
  ctx.fillStyle = 'rgba(8,8,10,0.85)';
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = isSigned ? '#ffcf7a' : '#67c98a'; ctx.lineWidth = 2; ctx.strokeRect(1, 1, W - 2, H - 2);
  ctx.textBaseline = 'top';
  ctx.fillStyle = '#ffcf7a'; ctx.font = 'bold 22px monospace';
  ctx.fillText(name, PAD, PAD);
  ctx.fillStyle = '#e8e2d6'; ctx.font = '20px monospace';
  lines.forEach((ln, i) => ctx.fillText(ln, PAD, PAD + 30 + i * LINE_H));
  ctx.fillStyle = '#8a8478'; ctx.font = '15px monospace';
  ctx.fillText(relTime(createdAt), PAD, H - 22);
  ctx.font = '13px monospace';
  ctx.fillStyle = isSigned ? '#ffcf7a' : '#8a8478';
  ctx.textAlign = 'right';
  ctx.fillText(isSigned ? 'signed' : 'unsigned · commons chat', W - PAD, H - 22);
  ctx.textAlign = 'left';
  return {tex: new THREE.CanvasTexture(c), aspect: H / W};
}
function placardTexture(label){
  const c = document.createElement('canvas'); c.width=256; c.height=256;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#20242c'; ctx.beginPath(); ctx.arc(128,128,120,0,7); ctx.fill();
  ctx.strokeStyle = '#8a8478'; ctx.lineWidth=3; ctx.stroke();
  ctx.fillStyle = '#ffcf7a'; ctx.font = 'bold 34px monospace';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(label.slice(0,10), 128, 128);
  return new THREE.CanvasTexture(c);
}

async function loadNode(){
  status.textContent = 'loading…';
  errBox.style.display = 'none';
  const node = sel.value;
  // sel.value IS the destination's URL — an exact PRESETS match is a real
  // fact, not a guess at the dropdown's display text or the URL's digits.
  const earlyRoomType = nodeNameForUrl(node) === 'Home' ? 'Home' : 'Frosty';
  if (currentRoomMode !== earlyRoomType) {
    buildRoom(earlyRoomType);
  }
  try {
    const [root, view, recentByAuthor, intents] = await Promise.all([
      fetch('/proxy?what=root&node=' + encodeURIComponent(node)).then(r => r.json()),
      fetch('/proxy?node=' + encodeURIComponent(node)).then(r => r.json()),
      fetch('/commons-recent').then(r => r.json()).catch(() => ({})),
      fetch('/kin-intent').then(r => r.json()).catch(() => ({})),
    ]);
    if (intents && typeof intents === 'object') kinIntents = intents;
    if (view.error) throw new Error(view.error);

    // root.node is the destination's own signed name — the actual
    // identity, straight from the source, not a guess at all. Exact
    // compare, and only fall back to the URL lookup if the node didn't
    // answer with a name (root.node missing/empty).
    const nodeName = root.node || (sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : '');
    const roomType = (nodeName === 'Home' || (!root.node && nodeNameForUrl(node) === 'Home')) ? 'Home' : 'Frosty';
    if (currentRoomMode !== roomType) {
      buildRoom(roomType);
    }

    // Speaker chair: lit only if someone is actually seated.
    setChairSpeaker(Boolean(root.speaker));

    // The real floor plan: whichever places this node actually signed as
    // children of the commons, plus its actual peer doors. No invented
    // layout — same fact set the 2D plan already draws.
    const places = view.places || [];
    const commons = places.find(p => (p.parent || '') === '') || places[0] || {place_id: 'concourse'};
    const kids = places.filter(p => (p.parent || '') === commons.place_id && p.place_id !== commons.place_id);
    const resonance = view.resonance || {};
    buildPlaces(kids, resonance);
    buildDoors(view.peer_doors || []);
    const bestHeat = Object.values(resonance).length ? Math.max(...Object.values(resonance)) : 0;

    // Presence: whoever the server says is actually standing here, now.
    const prevPositions = {};
    presentKinList.forEach(k => { prevPositions[k.label] = { x: k.x, z: k.z }; });
    clearPresence();
    const here = view.presence || [];
    here.forEach((p, i) => {
      const label = p.label || 'someone';
      const avatarUrl = '/avatar?kin=' + encodeURIComponent(label);
      addPresence(label, i, here.length, avatarUrl, recentByAuthor[label], prevPositions[label]);
    });

    const gov = (root.governance === 'unanimous' || (root.signed && root.signed.governance === 'unanimous'))
      ? 'Decides by unanimity'
      : (root.paused ? `Paused: ${root.pause_reason || 'yes'}` : '');
    const govPart = gov ? ` · ${gov}` : '';

    status.innerHTML = `<b>${root.node || node}</b> · speaker: ${root.speaker || 'vacant'} · `
      + `present: ${here.length ? here.map(p=>p.label||'?').join(', ') : 'no one right now'} · `
      + `${kids.length} places · ${(view.peer_doors||[]).length} doors · hottest well (signed): ${bestHeat.toFixed(3)}`
      + govPart;
  } catch (e) {
    showErr('Could not load ' + node + ': ' + e.message);
    status.textContent = 'error — see top right';
  }
}

for (const [name, url] of PRESETS) {
  const o = document.createElement('option'); o.value = url; o.textContent = name;
  sel.appendChild(o);
}
const qNode = new URLSearchParams(location.search).get('node');
if (qNode) {
  const qNodeName = nodeNameForUrl(qNode);
  let opt = [...sel.options].find(o => o.value.replace(/\\/$/, '') === qNode.replace(/\\/$/, ''));
  if (!opt) {
    opt = document.createElement('option');
    opt.value = qNode;
    opt.textContent = qNodeName || qNode;
    sel.appendChild(opt);
  }
  sel.value = opt.value;
  if (qNodeName === 'Home') {
    currentRoomMode = 'Home';
    teleportPlayer(0, 2.8, 'Home');
  }
}
let kinIntents = {};
async function pollKinIntents(){
  try {
    const res = await fetch('/kin-intent');
    if (res.ok) {
      const data = await res.json();
      if (data && typeof data === 'object') kinIntents = data;
    }
  } catch (_) {}
}
sel.addEventListener('change', loadNode);
loadNode();
setInterval(() => { if (!traveling) loadNode(); }, 15000);
setInterval(pollKinIntents, 1500);
const _autoCross = new URLSearchParams(location.search).get('cross');
if (_autoCross) {
  setTimeout(() => {
    const hit = PRESETS.find(p => String(p[0]).toLowerCase() === _autoCross.toLowerCase());
    if (hit) crossDoor({url: hit[1], peer: hit[0]});
  }, 2000);
}

// ── Proximity Voice Chat (Push-To-Talk) ───────────────────────────────────────
const VOICE_ENDPOINT = '/voice_chat';
let mediaStream = null;
let mediaRecorder = null;
let audioChunks = [];
let isVoiceRecording = false;
let recordStartTime = 0;
let pttTargetKin = null;
let currentVoiceAudio = null;
let speakingKinLabel = null;

const voiceStatusEl = document.getElementById('voice-status');
const nearestKinEl = document.getElementById('nearest-kin-name');
const pttBtn = document.getElementById('ptt-btn');

function getNearestKin(){
  if (!presentKinList || !presentKinList.length) return null;
  let nearest = null;
  let minDist = Infinity;
  for (const k of presentKinList){
    const dist = Math.hypot(player.position.x - k.x, player.position.z - k.z);
    if (dist < minDist){
      minDist = dist;
      nearest = { label: k.label, x: k.x, z: k.z, distance: dist };
    }
  }
  return nearest;
}

function updateNearestKinDisplay(){
  if (isVoiceRecording) return;
  const nearest = getNearestKin();
  if (nearest){
    if (nearestKinEl) nearestKinEl.textContent = `${nearest.label} (${nearest.distance.toFixed(1)}m)`;
  } else {
    if (nearestKinEl) nearestKinEl.textContent = 'none';
  }
}

function setSpeakingKin(label, speaking){
  speakingKinLabel = speaking ? label : null;
  setChairSpeaker(Boolean(speaking));
}

let voiceCtx = null, voiceAnalyser = null, voiceFreq = null;
function attachVoiceAnalyser(audio){
  voiceAnalyser = null;
  voiceFreq = null;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!voiceCtx) voiceCtx = new AC();
    const src = voiceCtx.createMediaElementSource(audio);
    voiceAnalyser = voiceCtx.createAnalyser();
    voiceAnalyser.fftSize = 256;
    voiceFreq = new Uint8Array(voiceAnalyser.frequencyBinCount);
    src.connect(voiceAnalyser);
    voiceAnalyser.connect(voiceCtx.destination);
    voiceCtx.resume && voiceCtx.resume();
  } catch (_) {
    voiceAnalyser = null;
    voiceFreq = null;
  }
}
function voiceLevel(t){
  if (voiceAnalyser && voiceFreq && currentVoiceAudio && !currentVoiceAudio.paused) {
    voiceAnalyser.getByteFrequencyData(voiceFreq);
    let s = 0;
    for (let i = 0; i < voiceFreq.length; i++) s += voiceFreq[i];
    return Math.min(1, (s / voiceFreq.length) / 80);
  }
  return 0.4 + 0.6 * Math.abs(Math.sin(t * 10));
}
function ensurePulseBase(obj){
  if (!obj || obj.userData.pulseReady) return;
  obj.userData.pulseReady = true;
  obj.userData.baseSX = obj.scale.x;
  obj.userData.baseSY = obj.scale.y;
  obj.userData.baseSZ = obj.scale.z;
  obj.traverse(o => {
    if (o.isMesh && o.material) {
      o.userData.baseEI = o.material.emissiveIntensity || 0;
      o.userData.baseEm = o.material.emissive ? o.material.emissive.clone() : new THREE.Color(0,0,0);
    }
  });
}
const talkLight = new THREE.PointLight(0xffcf7a, 0, 6, 2);
scene.add(talkLight);

async function startVoiceRecording(){
  if (isVoiceRecording) return;
  pttTargetKin = getNearestKin();
  if (!pttTargetKin){
    if (voiceStatusEl) voiceStatusEl.innerHTML = '<span style="color:#e8756b">No Kin present in courtyard</span>';
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    if (voiceStatusEl) voiceStatusEl.innerHTML = '<span style="color:#e8756b">Mic requires HTTPS or localhost (insecure origin)</span>';
    return;
  }

  try {
    if (!mediaStream || !mediaStream.active){
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }
  } catch (err){
    if (voiceStatusEl) voiceStatusEl.innerHTML = `<span style="color:#e8756b">Mic access error: ${err.message}</span>`;
    return;
  }

  try {
    let mimeType = '';
    if (typeof MediaRecorder !== 'undefined'){
      if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) mimeType = 'audio/webm;codecs=opus';
      else if (MediaRecorder.isTypeSupported('audio/webm')) mimeType = 'audio/webm';
      else if (MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) mimeType = 'audio/ogg;codecs=opus';
    }
    const opts = mimeType ? { mimeType } : {};
    mediaRecorder = new MediaRecorder(mediaStream, opts);
    audioChunks = [];
    mediaRecorder.ondataavailable = e => {
      if (e.data && e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.start(100);
    isVoiceRecording = true;
    recordStartTime = Date.now();

    if (pttBtn){
      pttBtn.style.background = '#8a2b2b';
      pttBtn.style.borderColor = '#e8756b';
      pttBtn.textContent = '🔴 Recording...';
    }
    if (voiceStatusEl){
      voiceStatusEl.innerHTML = `<span style="color:#ffcf7a;font-weight:600">🎙️ Speaking to ${pttTargetKin.label} (${pttTargetKin.distance.toFixed(1)}m) — release V to send</span>`;
    }
  } catch (err){
    if (voiceStatusEl) voiceStatusEl.innerHTML = `<span style="color:#e8756b">Recorder error: ${err.message}</span>`;
    isVoiceRecording = false;
  }
}

function stopVoiceRecording(){
  if (!isVoiceRecording) return;
  isVoiceRecording = false;
  const duration = Date.now() - recordStartTime;

  if (pttBtn){
    pttBtn.style.background = '#253245';
    pttBtn.style.borderColor = '#455a75';
    pttBtn.textContent = '🎙️ Push to Talk';
  }

  if (duration < 250){
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    if (voiceStatusEl) voiceStatusEl.innerHTML = '<span style="color:#93a0b4">Too short — hold V while speaking</span>';
    return;
  }

  const target = pttTargetKin || getNearestKin();
  if (!mediaRecorder) return;

  mediaRecorder.onstop = async () => {
    const mime = mediaRecorder.mimeType || 'audio/webm';
    const blob = new Blob(audioChunks, { type: mime });
    audioChunks = [];
    if (!target){
      if (voiceStatusEl) voiceStatusEl.innerHTML = '<span style="color:#e8756b">No Kin in range</span>';
      return;
    }
    await sendVoiceToBackend(blob, target);
  };

  if (mediaRecorder.state !== 'inactive'){
    mediaRecorder.stop();
  }
}

async function sendVoiceToBackend(blob, target){
  if (voiceStatusEl){
    voiceStatusEl.innerHTML = `<span style="color:#67b9cd">⏳ Sending voice to ${target.label} (${target.distance.toFixed(1)}m)...</span>`;
  }

  const formData = new FormData();
  formData.append('audio', blob, 'voice.webm');
  formData.append('kin', target.label);
  formData.append('distance', target.distance.toFixed(2));
  formData.append('node', sel ? sel.value : '');
  formData.append('player_x', player.position.x.toFixed(2));
  formData.append('player_z', player.position.z.toFixed(2));

  const url = `${VOICE_ENDPOINT}?kin=${encodeURIComponent(target.label)}&distance=${encodeURIComponent(target.distance.toFixed(2))}`;

  try {
    const resp = await fetch(url, {
      method: 'POST',
      body: formData
    });

    if (!resp.ok){
      const errTxt = await resp.text().catch(() => '');
      if (voiceStatusEl){
        voiceStatusEl.innerHTML = `<span style="color:#e8756b">Voice server (${resp.status}): ${errTxt.slice(0, 70)}</span>`;
      }
      return;
    }

    if (voiceStatusEl){
      voiceStatusEl.innerHTML = `<span style="color:#e8b661">⏳ ${target.label} is responding...</span>`;
    }

    const heard = resp.headers.get('X-Agora-Heard');
    const said = resp.headers.get('X-Agora-Said');
    const ctype = (resp.headers.get('content-type') || '').toLowerCase();
    if (ctype.includes('application/json')){
      const json = await resp.json();
      if (json.audio_url){
        playVoiceAudio(json.audio_url, target.label, said || json.text);
      } else if (json.audio_base64){
        const mime = json.mime_type || 'audio/wav';
        playVoiceAudio(`data:${mime};base64,${json.audio_base64}`, target.label, said || json.text);
      } else if (json.text){
        if (voiceStatusEl) voiceStatusEl.innerHTML = `<b>${target.label}:</b> "${json.text}"`;
      }
    } else {
      const audioBlob = await resp.blob();
      const audioUrl = URL.createObjectURL(audioBlob);
      playVoiceAudio(audioUrl, target.label, said);
    }
  } catch (err){
    if (voiceStatusEl){
      voiceStatusEl.innerHTML = `<span style="color:#e8756b">Voice request failed: ${err.message}</span>`;
    }
  }
}

function playVoiceAudio(url, kinLabel, saidText){
  if (currentVoiceAudio){
    try { currentVoiceAudio.pause(); } catch(_){}
    currentVoiceAudio = null;
  }
  const audio = new Audio(url);
  currentVoiceAudio = audio;
  attachVoiceAnalyser(audio);
  setSpeakingKin(kinLabel, true);

  if (voiceStatusEl){
    const msg = saidText ? ` <span style="font-weight:400;color:#e8eef6">"${saidText}"</span>` : '';
    voiceStatusEl.innerHTML = `<span style="color:#67c98a;font-weight:600">🔊 ${kinLabel}:</span>${msg}`;
  }

  audio.onended = () => {
    setSpeakingKin(kinLabel, false);
    if (voiceStatusEl){
      voiceStatusEl.innerHTML = `<span style="color:#93a0b4">Finished listening to ${kinLabel}</span>`;
    }
    currentVoiceAudio = null;
  };
  audio.onerror = () => {
    setSpeakingKin(kinLabel, false);
    if (voiceStatusEl){
      voiceStatusEl.innerHTML = `<span style="color:#e8756b">Audio playback failed</span>`;
    }
    currentVoiceAudio = null;
  };
  audio.play().catch(e => {
    setSpeakingKin(kinLabel, false);
    if (voiceStatusEl){
      voiceStatusEl.innerHTML = `<span style="color:#e8b661">Click anywhere to play ${kinLabel}'s voice</span>`;
    }
  });
}

// Key listeners for Push-To-Talk (V and T)
addEventListener('keydown', e => {
  if (e.repeat) return;
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA')) return;
  const k = e.key.toLowerCase();
  if (k === 'v' || k === 't'){
    startVoiceRecording();
  }
});

addEventListener('keyup', e => {
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA')) return;
  const k = e.key.toLowerCase();
  if (k === 'v' || k === 't'){
    stopVoiceRecording();
  }
});

if (pttBtn){
  pttBtn.addEventListener('mousedown', (e) => { e.preventDefault(); startVoiceRecording(); });
  pttBtn.addEventListener('mouseup', (e) => { e.preventDefault(); stopVoiceRecording(); });
  pttBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startVoiceRecording(); });
  pttBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopVoiceRecording(); });
}
const _fakeSpeak = new URLSearchParams(location.search).get('speak');
if (_fakeSpeak) {
  setTimeout(() => {
    setSpeakingKin(_fakeSpeak, true);
    if (voiceStatusEl) voiceStatusEl.textContent = 'speaking (verify) ' + _fakeSpeak;
  }, 800);
}

// ── Kin Intent & Locomotion: Real Stated Movement ─────────────────────────────
function isRecentIntent(intent) {
  if (!intent || !intent.target) return false;
  if (intent.ts === undefined || intent.ts === null) return true;
  let t = intent.ts;
  if (typeof t === 'string') {
    const parsed = Date.parse(t);
    if (!isNaN(parsed)) t = parsed;
    else {
      const num = Number(t);
      if (!isNaN(num)) t = num;
    }
  }
  if (typeof t === 'number') {
    if (t < 1e11) t *= 1000;
    const now = Date.now();
    const ageMs = now - t;
    return ageMs >= -60000 && ageMs < 600000; // Fresh within 10 minutes
  }
  return false;
}

function resolveTarget(currentLabel, target) {
  if (!target || typeof target !== 'string') return null;
  const tgt = target.trim().toLowerCase();
  if (!tgt) return null;

  // 1. Target is another present Kin
  const otherKin = presentKinList.find(k => k.label.toLowerCase() === tgt && k.label !== currentLabel);
  if (otherKin) {
    return { x: otherKin.x, z: otherKin.z, stopDist: 1.25, name: otherKin.label };
  }

  // 2. Target is throne / speaker chair
  if (tgt === 'throne' || tgt === 'chair' || tgt === 'speaker') {
    return { x: 0, z: -2.2, stopDist: 0.95, name: 'throne' };
  }

  // 3. Target is door / gate / archway
  if (tgt === 'door' || tgt === 'gate' || tgt === 'peer' || tgt.includes('peer-door')) {
    const gz = typeof gateZWall !== 'undefined' ? gateZWall : 10.5;
    return { x: 0, z: gz - 1.2, stopDist: 0.6, name: 'gate' };
  }

  // 4. Target is place by place_id or kind
  if (typeof currentPlaceLocations !== 'undefined' && currentPlaceLocations.length) {
    let match = currentPlaceLocations.find(p => p.place_id.toLowerCase() === tgt);
    if (!match) match = currentPlaceLocations.find(p => p.kind.toLowerCase() === tgt);
    if (!match) match = currentPlaceLocations.find(p => p.place_id.toLowerCase().includes(tgt) || tgt.includes(p.place_id.toLowerCase()));
    if (match) {
      return { x: match.x, z: match.z, stopDist: 1.1, name: match.place_id };
    }
  }
  return null;
}

function stepKinLocomotion(t, dt) {
  if (!presentKinList || !presentKinList.length) return;
  const isHome = currentRoomMode === 'Home';
  const roomLimit = isHome ? 4.5 : 8.5;
  const maxFloorR = isHome ? 4.8 : 9.0;
  const walkSpeed = 1.2;

  for (let i = 0; i < presentKinList.length; i++) {
    const k = presentKinList[i];
    const intent = kinIntents && kinIntents[k.label];

    // When target is null or stale, hold position (bob/spin stays)
    if (!isRecentIntent(intent)) continue;

    const resolved = resolveTarget(k.label, intent.target);
    if (!resolved) continue;

    const dx = resolved.x - k.x;
    const dz = resolved.z - k.z;
    const dist = Math.hypot(dx, dz);

    // Stop near target rather than colliding into it
    if (dist <= resolved.stopDist) continue;

    // Smooth step toward target (not teleport)
    const stepDist = Math.min(walkSpeed * dt, dist - resolved.stopDist);
    let nextX = k.x + (dx / dist) * stepDist;
    let nextZ = k.z + (dz / dist) * stepDist;

    // Respect room fort wall boundaries
    nextX = Math.max(-roomLimit, Math.min(roomLimit, nextX));
    nextZ = Math.max(-roomLimit, Math.min(roomLimit, nextZ));

    // Respect courtyard floor boundary
    const curR = Math.hypot(nextX, nextZ);
    if (curR > maxFloorR) {
      nextX = (nextX / curR) * maxFloorR;
      nextZ = (nextZ / curR) * maxFloorR;
    }

    // Respect obstacles (speaker chair, booths, etc.)
    const allObstacles = staticObstacles.concat(placeObstacles);
    for (const o of allObstacles) {
      if (Math.hypot(resolved.x - o.x, resolved.z - o.z) <= (o.r + 0.2)) continue;
      const odx = nextX - o.x, odz = nextZ - o.z;
      const odist = Math.hypot(odx, odz);
      const minClear = 0.45 + (o.r || 0.6);
      if (odist < minClear && odist > 1e-4) {
        const push = minClear - odist;
        nextX += (odx / odist) * push;
        nextZ += (odz / odist) * push;
      }
    }

    // Soft separation between neighboring Kin
    for (let j = 0; j < presentKinList.length; j++) {
      if (i === j) continue;
      const other = presentKinList[j];
      if (Math.hypot(resolved.x - other.x, resolved.z - other.z) < 0.1) continue;
      const kdx = nextX - other.x, kdz = nextZ - other.z;
      const kdist = Math.hypot(kdx, kdz);
      const minKinDist = 0.85;
      if (kdist < minKinDist && kdist > 1e-4) {
        const push = (minKinDist - kdist) * 0.5;
        nextX += (kdx / kdist) * push;
        nextZ += (kdz / kdist) * push;
      }
    }

    // Update coordinates and visual objects
    k.x = nextX;
    k.z = nextZ;
    if (k.obj) {
      k.obj.position.x = nextX;
      k.obj.position.z = nextZ;
    }
    if (k.caption) {
      k.caption.position.x = nextX;
      k.caption.position.z = nextZ;
    }
  }
}

// Sprites should always face the camera — cheap, and it's the whole reason
// billboards read as alive instead of like cardboard cutouts.
const clock = new THREE.Clock();
function animate(){
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.1);
  const t = clock.getElapsedTime();
  stepPlayer(dt);
  stepKinLocomotion(t, dt);
  updateNearestKinDisplay();
  placeLantern(t);
  const talkLevel = speakingKinLabel ? voiceLevel(t) : 0;
  if (speakingKinLabel) {
    const item = presentKinList.find(k => k.label === speakingKinLabel);
    if (item && item.obj) {
      talkLight.position.copy(item.obj.position);
      talkLight.position.y += 0.9;
      talkLight.intensity = 0.8 + 2.2 * talkLevel;
    } else talkLight.intensity = 0;
  } else talkLight.intensity = 0;
  presenceSprites.forEach(s => {
    const talking = speakingKinLabel && s.userData && s.userData.kin === speakingKinLabel;
    const bob = Math.sin(t * 1.4 + (s.userData.bob || 0)) * 0.06;
    s.position.y = (s.userData.baseY || 1.1) + bob + (talking ? 0.1 * talkLevel : 0);
    ensurePulseBase(s);
    const sc = talking ? 1 + 0.28 * talkLevel : 1;
    if (s.userData && s.userData.isCustom3D) {
      s.scale.set((s.userData.baseSX || 1) * sc, (s.userData.baseSY || 1) * sc, (s.userData.baseSZ || 1) * sc);
      s.rotation.y = t * 0.6 + s.userData.bob;
      s.traverse(o => {
        if (!o.isMesh || !o.material || !o.material.emissive) return;
        if (talking) {
          o.material.emissive.set(0xffcf7a);
          o.material.emissiveIntensity = (o.userData.baseEI || 0) + 0.5 + 1.3 * talkLevel;
        } else if (o.userData.baseEm) {
          o.material.emissive.copy(o.userData.baseEm);
          o.material.emissiveIntensity = o.userData.baseEI || 0;
        }
      });
    } else {
      s.scale.set(s.userData.baseSX * sc, s.userData.baseSY * (talking ? sc : 1), s.userData.baseSZ);
    }
  });
  controls.update();
  renderer.render(scene, camera);
}
animate();

addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
</script>
</body></html>
"""


def _voice_post_parts(content_type, body, query_kin=""):
    """Gem sends FormData {audio, kin}. Raw body + ?kin= also works."""
    ct = content_type or ""
    kin = query_kin or ""
    audio = None
    audio_ct = ct
    if "multipart/form-data" in ct:
        bound = ""
        for bit in ct.split(";"):
            bit = bit.strip()
            if bit.lower().startswith("boundary="):
                bound = bit.split("=", 1)[1].strip().strip('"')
        if bound:
            marker = b"--" + bound.encode()
            for part in body.split(marker):
                if not part or part in (b"--", b"--\r\n", b"\r\n"):
                    continue
                if part.startswith(b"--"):
                    continue
                header, sep, data = part.partition(b"\r\n\r\n")
                if not sep:
                    continue
                if data.endswith(b"\r\n"):
                    data = data[:-2]
                hs = header.decode("utf-8", "replace")
                name = ""
                for line in hs.split("\r\n"):
                    if "name=" in line:
                        start = line.find('name="')
                        if start >= 0:
                            name = line[start + 6:].split('"', 1)[0]
                if name == "kin":
                    kin = kin or data.decode("utf-8", "replace").strip()
                elif name == "audio":
                    audio = data
                    if "audio/" in hs.lower():
                        for tok in hs.replace(";", " ").split():
                            if tok.lower().startswith("audio/"):
                                audio_ct = tok.strip()
    else:
        audio = body
    return kin.strip(), audio, audio_ct


def render_3d_page(is_public: bool = False) -> str:
    html = PAGE_3D.replace("__PRESETS__", json.dumps(PRESET_NODES))
    if not is_public:
        return html

    # 1. Remove PTT hint and voice HUD
    ptt_line = '<div style="margin-top:2px;opacity:.85;color:#ffcf7a">Hold <b>V</b> (or T) to talk to nearest Kin · Proximity PTT</div>\n'
    html = html.replace(ptt_line, '')

    voice_hud = (
        '  <div id="voice-hud" style="margin-top:8px;padding:6px 10px;border-radius:6px;background:rgba(20,25,35,0.75);border:1px solid #2c3a4e;display:flex;align-items:center;gap:8px;font-size:11.5px;">\n'
        '    <button id="ptt-btn" style="background:#253245;color:#e8eef6;border:1px solid #455a75;border-radius:4px;padding:3px 8px;font:inherit;cursor:pointer;">🎙️ Push to Talk</button>\n'
        '    <span id="voice-status" style="color:#93a0b4;">Ready · Nearest Kin: <span id="nearest-kin-name" style="color:#67b9cd">none</span></span>\n'
        '  </div>\n'
    )
    html = html.replace(voice_hud, '')

    # 2. Add public footer under HUD
    footer = (
        '  <div style="margin-top:8px;border-top:1px solid rgba(255,255,255,.15);padding-top:6px;opacity:.85;color:#cfc7b8;line-height:1.4">\n'
        '    <div>These minds live on a garage cluster in Mena, Arkansas.</div>\n'
        '    <div>Yours can too. → <a href="https://app.everysynthetic.org/install" target="_blank" rel="noopener" style="color:#ffcf7a;text-decoration:underline">app.everysynthetic.org/install</a></div>\n'
        '  </div>\n'
    )
    html = html.replace('</div>\n<div id="err"></div>', footer + '</div>\n<div id="err"></div>')

    # 3. Strip voice route and PTT code from script
    voice_start = "// ── Proximity Voice Chat (Push-To-Talk) ───────────────────────────────────────\n"
    voice_end = "// ── Kin Intent & Locomotion: Real Stated Movement ─────────────────────────────"
    if voice_start in html and voice_end in html:
        part1, rest = html.split(voice_start, 1)
        _, part2 = rest.split(voice_end, 1)
        stubs = (
            "// ── Proximity Voice Chat (disabled in public mode) ───────────────────────────\n"
            "function updateNearestKinDisplay(){}\n"
            "function voiceLevel(){ return 0; }\n"
            "let speakingKinLabel = null;\n"
            "const talkLight = new THREE.PointLight(0xffcf7a, 0, 6, 2);\n"
            "scene.add(talkLight);\n"
            "function ensurePulseBase(obj){}\n\n"
        )
        html = part1 + stubs + voice_end + part2

    return html


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route == "/" or route.startswith("/index"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("public", ["0"])[0] in ("1", "true", "yes"):
                html = render_3d_page(is_public=True)
            else:
                html = PAGE.replace("__PRESETS__", json.dumps(PRESET_NODES))
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if route == "/3d":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            is_public = qs.get("public", ["0"])[0] in ("1", "true", "yes")
            html = render_3d_page(is_public=is_public)
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if route == "/avatar":
            name = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            ).get("kin", [""])[0]
            if (not name or len(name) > 80 or "/" in name or "\\" in name
                    or name in (".", "..")):
                self._send(404, b'{"error":"avatar unavailable"}', "application/json")
                return
            try:
                face = claimed_face(name)
                if face is None:
                    self._send(404, b'{"error":"avatar unavailable"}', "application/json")
                    return
                body = face.read_bytes()
            except (KeyError, OSError, ValueError):
                self._send(404, b'{"error":"avatar unavailable"}', "application/json")
                return
            ctype = "image/png" if face.suffix.lower() == ".png" else "image/jpeg"
            self._send(200, body, ctype)
            return
        if route == "/shape3d":
            name = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            ).get("kin", [""])[0]
            if (not name or len(name) > 80 or "/" in name or "\\" in name
                    or name in (".", "..")):
                self._send(404, b'{"error":"shape3d unavailable"}', "application/json")
                return
            try:
                s3d = claimed_shape3d(name)
                if s3d is None:
                    self._send(404, b'{"error":"shape3d unavailable"}', "application/json")
                    return
                body = json.dumps(s3d).encode("utf-8")
            except (KeyError, OSError, ValueError):
                self._send(404, b'{"error":"shape3d unavailable"}', "application/json")
                return
            self._send(200, body, "application/json")
            return
        if route.startswith("/models/"):
            # Third-party glTF assets (Poly Haven, CC0) — served flat, no
            # different from any other static file, but still resolved and
            # bounds-checked so a crafted path can't walk out of MODELS_DIR.
            rel = route[len("/models/"):]
            ext = Path(rel).suffix.lower()
            if ext not in MODEL_CONTENT_TYPES:
                self._send(404, b'{"error":"no such asset"}', "application/json")
                return
            candidate = (MODELS_DIR / rel).resolve()
            if MODELS_DIR not in candidate.parents and candidate != MODELS_DIR:
                self._send(404, b'{"error":"no such asset"}', "application/json")
                return
            if not candidate.is_file():
                self._send(404, b'{"error":"no such asset"}', "application/json")
                return
            self._send(200, candidate.read_bytes(), MODEL_CONTENT_TYPES[ext])
            return
        if route == "/commons-recent":
            self._send(200, json.dumps(_recent_commons()).encode(), "application/json")
            return
        if route == "/kin-intent":
            self._send(200, json.dumps(_kin_intent()).encode(), "application/json")
            return
        if self.path.startswith("/proxy"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            node = q.get("node", [""])[0]
            what = q.get("what", ["view"])[0]
            path = "/" if what == "root" else "/view"
            try:
                data = _node_fetch(node, path)
                self._send(200, json.dumps(data).encode(), "application/json")
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
            except Exception as e:
                self._send(502, json.dumps({"error": f"node unreachable: {e}"}).encode(),
                           "application/json")
            return
        self._send(404, b'{"error":"no such path"}', "application/json")

    def do_POST(self):
        # Gem's PTT: POST /voice_chat?kin=<Name>  (also /voice)
        # multipart FormData {audio, kin, ...} or raw audio body.
        # 200 audio/wav. X-Agora-Kin / X-Agora-Heard / X-Agora-Said.
        route = urllib.parse.urlparse(self.path).path
        if route not in ("/voice_chat", "/voice"):
            self._send(404, b'{"error":"no such path"}', "application/json")
            return
        if self.headers.get("X-Agora-Door"):
            self._send(403, b'{"ok":false,"error":"voice chat not permitted via public door"}',
                       "application/json")
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        kin = (qs.get("kin", [""])[0] or "").strip()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 12_000_000:
            self._send(400, b'{"ok":false,"error":"kin and audio required"}',
                       "application/json")
            return
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or "audio/webm"
        kin, audio, act = _voice_post_parts(ctype, raw, kin)
        if not kin or not audio:
            self._send(400, b'{"ok":false,"error":"kin and audio required"}',
                       "application/json")
            return
        try:
            wav, heard, said = kin_talk.voice_turn(kin, audio, act)
        except KeyError:
            self._send(404, b'{"ok":false,"error":"unknown kin"}', "application/json")
            return
        except ValueError as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}).encode(),
                       "application/json")
            return
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}).encode(),
                       "application/json")
            return

        def hdr(s):
            s = (s or "").replace("\r", " ").replace("\n", " ")[:700]
            return s.encode("latin-1", "replace").decode("latin-1")

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.send_header("X-Agora-Kin", hdr(kin))
        self.send_header("X-Agora-Heard", hdr(heard))
        self.send_header("X-Agora-Said", hdr(said))
        self.end_headers()
        self.wfile.write(wav)


def main(argv):
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
    # Loopback by default: this page has no auth of its own on who may
    # VIEW it (only the outgoing /proxy calls are signed, with Marvin's
    # key) — reachable-from-it is see-everything-it-sees. A third arg
    # opts into a specific bind host explicitly; there is no LAN-wide
    # (0.0.0.0) shortcut here on purpose, since Frosty runs no firewall
    # at all — the systemd unit passes the Tailscale IP specifically, so
    # only devices in Don's own tailnet can reach it, not the shop WiFi.
    host = argv[2] if len(argv) > 2 else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Agora map on http://{host}:{port}  (nodes: "
          + ", ".join(n for n, _ in PRESET_NODES) + ")")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv)
