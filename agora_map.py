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

DEFAULT_PORT = 8791
MODELS_DIR = (Path(__file__).parent / "static" / "models").resolve()
MODEL_CONTENT_TYPES = {
    ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
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
        out[r["author"]] = {"content": content, "created_at": r["created_at"]}
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
      s += `<text x="${px.toFixed(0)}" y="${(py+51).toFixed(0)}" text-anchor="middle" class="avatar-note">"${esc(excerpt)}" · ${esc(relTime(recent.created_at))}</text>`;
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
       + (heat > 0.01 ? `<text x="${ax+14}" y="${ay+82}" class="kkid" style="fill:#e8a94b">ghost voltage · ${heat.toFixed(2)}</text>` : '');
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
</style>
</head><body>
<div id="hud">
  <div><b>Agora — 3D proof of pipeline</b></div>
  <div>node: <select id="node"></select></div>
  <div id="status">loading…</div>
  <div style="margin-top:6px;opacity:.7">WASD / arrows to walk · space to jump · climb stairs to rampart · drag to look · scroll to zoom</div>
  <div style="margin-top:2px;opacity:.7">Walk into a peer door to cross to that node.</div>
  <div style="margin-top:2px;opacity:.5">Same signed data as the 2D map, plus kin_commons' real board over each presence. Nothing here is invented.</div>
</div>
<div id="err"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/GLTFLoader.js"></script>
<script>
const PRESETS = __PRESETS__;
const sel = document.getElementById('node');
const status = document.getElementById('status');
const errBox = document.getElementById('err');

function showErr(msg){ errBox.style.display='block'; errBox.textContent = msg; }

// ── scene: precomputed once, static geometry never rebuilt per frame ──────
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1612);
scene.fog = new THREE.FogExp2(0x1a1612, 0.01);

// You: a position on the floor, not a body — same "sprites, not sittings"
// discipline the presence figures already follow, just for a visitor who
// has no claimed face at all. A ring on the ground marks where you are;
// the camera orbits and walks around that point, never becomes a face.
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

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.copy(player.position).setY(1);
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 3; controls.maxDistance = 22;
controls.enablePan = false;   // panning would fight the walk-follow below
// ?view=inside — player at the courtyard center. The walk-follow owns
// the camera every frame, so moving the camera here is a no-op; move
// the player and let the existing offset sit inside the walls.
if (new URLSearchParams(location.search).get('view') === 'inside') {
  player.position.set(0, 0, 0);
  camera.position.set(0, 4.4, 6.5);
  controls.target.set(0, 1, 0);
}

// Mix, not a wash. Live shot of the first lighting pass (range 22 + heavy
// hemisphere) was a flat beige courtyard — dark-cave problem gone, torch
// pools gone with it. Pull the global lights back so the sconces read.
// Mid-wall + near-corner, range 12: covers past the center from 9.3 without
// turning every surface the same color. Decay 2 kept (the original intent).
scene.add(new THREE.HemisphereLight(0xffe6c8, 0x3a3228, 0.28));
scene.add(new THREE.AmbientLight(0xcbb89a, 0.12));
const key = new THREE.DirectionalLight(0xfff4e0, 0.5);
key.position.set(6, 14, 4);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffd9a8, 0.16);
fill.position.set(0, 8, 12);
scene.add(fill);

const hall = new THREE.PointLight(0xffe0b0, 0.7, 12, 1.8);
hall.position.set(0, 5.2, 0);
scene.add(hall);
const lamp = new THREE.Mesh(
  new THREE.SphereGeometry(0.12, 12, 12),
  new THREE.MeshBasicMaterial({color: 0xffe6c0})
);
lamp.position.copy(hall.position);
scene.add(lamp);

[[9.3, 0], [-9.3, 0], [0, 9.3], [0, -9.3],
 [7.4, 7.4], [7.4, -7.4], [-7.4, 7.4], [-7.4, -7.4]].forEach(([x, z]) => {
  const torch = new THREE.PointLight(0xffb366, 2.2, 12, 2);
  torch.position.set(x, 2.6, z);
  scene.add(torch);
  const flame = new THREE.Mesh(
    new THREE.SphereGeometry(0.09, 8, 8),
    new THREE.MeshBasicMaterial({color: 0xffcf7a})
  );
  flame.position.copy(torch.position);
  scene.add(flame);
});

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

// A distant fallback only — real containment is the wall collision now
// (wallObstacles), which correctly leaves the gate opening passable. This
// used to be the actual boundary before the walls existed; left at 10 it
// silently blocked the one opening we just built, closer to the floor
// edge than any wall is. Pushed past the wall corners (~14.8) so it only
// ever catches someone who's gotten past every real wall segment.
const FLOOR_R = 30;
const marker = new THREE.Mesh(
  new THREE.RingGeometry(0.35, 0.45, 24),
  new THREE.MeshBasicMaterial({color: 0x67b9cd, transparent: true, opacity: 0.85, side: THREE.DoubleSide})
);
marker.rotation.x = -Math.PI/2;
scene.add(marker);

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
const staticObstacles = [{x: 0, z: -2.2, r: 1.05}];   // the speaker chair
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
const RAMP_Z_STAIR_TOP = 3.266;
const RAMP_Z_END = 9.80;

function getGroundHeight(x, z){
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
  if (pos.z >= RAMP_Z_START - 0.5 && pos.z <= RAMP_Z_END + 0.5) {
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

  // South wall gate archway and fortress boundary (z ~ HALF = 10.5):
  // The gate archway opening is between GATE_X_MIN (2.10) and GATE_X_MAX (2.95).
  // Within the opening, the player walks freely through in Z between the commons
  // and the abyssal plain, with lateral sliding against the stone doorposts.
  // Outside the opening, the solid stone wall stops the player from penetrating.
  const GATE_X_MIN = 2.10, GATE_X_MAX = 2.95;
  if (pos.z >= 9.2 && pos.z <= 11.2 && pos.x >= 0.0 && pos.x <= 5.2) {
    if (pos.x >= GATE_X_MIN && pos.x <= GATE_X_MAX) {
      // Inside archway opening: slide laterally against stone jambs
      const margin = 0.25;
      if (pos.x < GATE_X_MIN + margin) pos.x = GATE_X_MIN + margin;
      else if (pos.x > GATE_X_MAX - margin) pos.x = GATE_X_MAX - margin;
      // Z passes freely through doorway
    } else {
      // Outside archway opening: solid stone wall
      const wallInner = 10.5 - 0.613 - PLAYER_R; // ~9.537
      const wallOuter = 10.5 + PLAYER_R;         // ~10.85
      if (pos.z < 10.5) {
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
  marker.position.set(player.position.x, player.position.y + 0.02, player.position.z);

  if (!traveling) {
    for (const t of doorTriggers) {
      if (Math.hypot(player.position.x - t.x, player.position.z - t.z) < t.r) { crossDoor(t); break; }
    }
  }
}

// The Speaker chair, focal, empty or seated. Static shape, live color only.
const chairMat = new THREE.MeshStandardMaterial({color:0x555555, emissive:0x000000});
const chair = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.9, 0.08, 24), chairMat);
chair.position.set(0, 0.05, -2.2);
scene.add(chair);

// First real asset, not a placeholder box: a CC0 weathered stone figure
// (Poly Haven's "Gothic Statue," downloaded and served locally at
// /models/, never fetched from a third party at runtime). Don's brief —
// "should feel old, like it was there before us" — this is the test of
// whether an actual piece of art can stand in the commons next to the
// real signed data, not just geometry standing in for one.
const STATUE_POS = {x: 3.4, z: -3.6};
staticObstacles.push({x: STATUE_POS.x, z: STATUE_POS.z, r: 0.6});
new THREE.GLTFLoader().load(
  '/models/gothic_statue/gothic_statue.gltf',
  (gltf) => {
    const statue = gltf.scene;
    statue.position.set(STATUE_POS.x, 0, STATUE_POS.z);
    statue.traverse(o => { if (o.isMesh) { o.castShadow = false; o.receiveShadow = false; } });
    scene.add(statue);
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
new THREE.GLTFLoader().load(
  '/models/marble_bust_01/marble_bust_01.gltf',
  (gltf) => {
    const bust = gltf.scene;
    // the raw model sits at floor level in its own file; a real display
    // bust wants to be at roughly eye height, on something, not on the
    // ground — so it gets the pedestal a real museum bust would have.
    bust.position.set(BUST_POS.x, PEDESTAL_H, BUST_POS.z);
    scene.add(bust);
  },
  undefined,
  (err) => showErr('bust failed to load: ' + err.message)
);

// The walls: Poly Haven's "Modular Fort 01" (CC0), harvested rather than
// used as its own prebuilt castle — it ships as one full assembled fort,
// but the pieces are modeled around their own local origins for exactly
// this, so real straight/corner segments get pulled out and re-tiled into
// a perimeter sized for OUR commons, not shrunk to fit (that would make
// real stone walls read as toy-sized). Measured at runtime from each
// piece's own geometry, not guessed dimensions, so the tiling has no
// gaps regardless of the kit's actual real-world scale.
new THREE.GLTFLoader().load('/models/modular_fort_01/modular_fort_01.gltf', (gltf) => {
  const src = gltf.scene;
  function harvest(namePart) {
    let found = null;
    src.traverse(o => { if (!found && o.name && o.name.includes(namePart)) found = o; });
    if (!found) return null;
    const piece = found.clone(true);
    piece.position.set(0, 0, 0);
    piece.rotation.set(0, 0, 0);
    piece.scale.set(1, 1, 1);
    return piece;
  }
  const strTemplate = harvest('wall_thick_straight_01');
  // A second straight variant, alternated in below purely for visual
  // relief — a perimeter built from one repeated stamp reads as
  // mechanical. Measured first (both report a Z-length of 14.563 in the
  // kit's raw units, same as straight_01) rather than assumed: the two
  // corner variants do NOT share a footprint (checked the same way, and
  // corner_02 is measurably wider), so only the straight run gets a
  // second variant — swapping corners would break the perimeter math
  // below, which assumes one corner span for all four.
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

  const wallGroup = new THREE.Group();
  scene.add(wallGroup);

  // The kit is modeled at real castle scale (a single straight run is
  // 10-30 units), which dwarfs a commons built at roughly human scale
  // (chair ~0.9 radius, presence ~1.7m). Rescale the harvested pieces
  // down to a wall-bay length that actually fits this room, uniformly,
  // so corner and straight segments still meet each other correctly.
  const rawStrLen = Math.max(...['x', 'z'].map(
    a => new THREE.Box3().setFromObject(strTemplate).getSize(new THREE.Vector3())[a]));
  const DESIRED_BAY = 3.5;
  const SCALE = DESIRED_BAY / rawStrLen;
  strTemplate.scale.setScalar(SCALE);
  if (strTemplate2) strTemplate2.scale.setScalar(SCALE);
  cornerTemplate.scale.setScalar(SCALE);
  if (gateTemplate) gateTemplate.scale.setScalar(SCALE);
  if (thinStrTemplate) thinStrTemplate.scale.setScalar(SCALE);
  if (towerTemplate) towerTemplate.scale.setScalar(SCALE);
  if (stairsTemplate) stairsTemplate.scale.setScalar(SCALE);
  if (walkwayTemplate) walkwayTemplate.scale.setScalar(SCALE);

  const segLen = DESIRED_BAY;
  const cornerSize = new THREE.Box3().setFromObject(cornerTemplate).getSize(new THREE.Vector3());
  const cornerSpan = Math.max(cornerSize.x, cornerSize.z);
  const towerRadius = towerTemplate
    ? Math.max(...['x', 'z'].map(a => new THREE.Box3().setFromObject(towerTemplate).getSize(new THREE.Vector3())[a])) / 2
    : 0;

  const HALF = 10.5;   // half side length of the square perimeter around the floor
  // A round tower where each pair of walls meets — real castle corners
  // aren't a bare miter joint, they're the strongpoint, and the kit ships
  // exactly this piece. Layered on top of the flat corner stub rather
  // than replacing it (the stub is what the straight bays actually key
  // into); the tower just makes the joint read as architecture.
  [{x: -HALF, z: -HALF, ry: 0},
   {x:  HALF, z: -HALF, ry: Math.PI / 2},
   {x:  HALF, z:  HALF, ry: Math.PI},
   {x: -HALF, z:  HALF, ry: -Math.PI / 2}].forEach(c => {
    const m = cornerTemplate.clone(true);
    m.position.set(c.x, 0, c.z);
    m.rotation.y = c.ry;
    wallGroup.add(m);
    if (towerTemplate) {
      const t = towerTemplate.clone(true);
      t.position.set(c.x, 0, c.z);
      wallGroup.add(t);
    }
    wallObstacles.push({x: c.x, z: c.z, r: Math.max(cornerSpan * 0.6, towerRadius * 0.9)});
  });

  const runLen = HALF * 2 - cornerSpan;
  const n = Math.max(1, Math.round(runLen / segLen));
  const actualSeg = runLen / n;
  function placeRun(axis, fixedCoord, ry, gateIndex) {
    for (let i = 0; i < n; i++) {
      const t = -runLen / 2 + actualSeg * (i + 0.5);
      const useGate = i === gateIndex && gateTemplate;
      // Deterministic alternation, not random — a reloaded page should
      // show the same wall it showed a moment ago, same as everything
      // else here that isn't live data.
      const straight = (i % 2 === 0 || !strTemplate2) ? strTemplate : strTemplate2;
      const m = (useGate ? gateTemplate : straight).clone(true);
      const pos = axis === 'x' ? {x: t, z: fixedCoord} : {x: fixedCoord, z: t};
      m.position.set(pos.x, 0, pos.z);
      m.rotation.y = ry;
      wallGroup.add(m);
      if (useGate) {
        // The gate (wall_thin_gate_01) is a half-bay piece (7.41 units raw vs 14.56 full straight bay).
        // Pair it with matching thin straight wall (wall_thin_straight_04, also 7.41 units raw)
        // to fill the missing section of wall next to the archway and close the 1.485m gap.
        const gateLen = 7.409695 * SCALE;
        if (thinStrTemplate) {
          const filler = thinStrTemplate.clone(true);
          const fillerPos = axis === 'x'
            ? {x: pos.x + gateLen, z: fixedCoord}
            : {x: fixedCoord, z: pos.z + gateLen};
          filler.position.set(fillerPos.x, 0, fillerPos.z);
          filler.rotation.y = ry;
          wallGroup.add(filler);
        }
        // Archway opening and South wall collision boundaries are handled by the exact
        // linear plane in resolveCollisions, so no circular obstacles bulge into the doorway
        // or push the player backward when walking through the archway.
      } else if (axis === 'x' && fixedCoord === HALF && i === gateIndex + 1) {
        // Bay 4 (immediately east of gate): shift obstacle center slightly east so its
        // circular boundary doesn't bulge into the gate's eastern doorframe.
        wallObstacles.push({x: pos.x + 0.5, z: pos.z, r: actualSeg * 0.45});
      } else if (axis === 'z' && fixedCoord === -HALF) {
        if (i === 2) {
          // Bay 2 approach: stops before stairs (z < 0) so southern approach corridor stays clear
          wallObstacles.push({x: -HALF - 0.5, z: pos.z, r: actualSeg * 0.45});
        } else if (i >= 3) {
          // West wall rampart bays (stairs and elevated walkways, z >= 0):
          // Outer curtain / battlement wall is handled by the exact linear plane boundary in resolveCollisions
          // (pos.x < RAMP_X_MIN + PLAYER_R). No circular obstacles here, so nothing bulges into the
          // walkway or pushes the player backward down the stairs.
        } else {
          wallObstacles.push({x: pos.x, z: pos.z, r: actualSeg * 0.55});
        }
      } else {
        wallObstacles.push({x: pos.x, z: pos.z, r: actualSeg * 0.55});
      }
    }
  }
  // The piece's own long axis runs along Z unrotated (that's how it came
  // out of the kit) — so an x-axis run (wall face at fixed z) needs the
  // 90° turn, and a z-axis run needs none. Backwards from what "placeRun
  // along x" suggests; verified live after the first pass put the long
  // dimension perpendicular to the wall line instead of along it — pieces
  // jutting inward as isolated fins instead of tiling into a wall.
  //
  // Gate sits in the middle bay of the +z wall (fixedCoord goes with the
  // OTHER axis here — an 'x' run has z fixed, not x) — the side the
  // player actually spawns facing, so arriving in the commons means
  // walking in through it, not just materializing inside a sealed box.
  // First pass put it on the +x (east) wall instead: fixedCoord for a
  // 'z' run sets x, not z, so "HALF" there meant x=HALF — confirmed live,
  // the arch itself was real and correct, just on the wrong side.
  const gateSlot = Math.floor(n / 2);
  placeRun('x', -HALF, Math.PI / 2);
  placeRun('x',  HALF, Math.PI / 2, gateSlot);
  placeRun('z', -HALF, 0);
  placeRun('z',  HALF, 0);

  // The West wall rampart: stone stairs starting at ground level (Z = 0)
  // climbing up to the elevated walkway deck (height ~1.70m), with
  // continuous battlement walkways continuing south along the wall to
  // the corner tower.
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
}, undefined, (err) => showErr('fort walls failed to load: ' + err.message));

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
  ctx.fillText((lines[1] || '').slice(0, 22), 160, 78);
  const tex = new THREE.CanvasTexture(c);
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, transparent: true}));
  spr.scale.set(1.9, 0.72, 1);
  return spr;
}

// Each real place (door, kiosk, table, whatever kind shows up) becomes a
// small booth of its own — literally: kind "table" gets a table shape, a
// door gets a frame. Its Resonance Well is that place's own real number
// (Atlas.resonance in places.py), not a borrowed average — this is Eli's
// original seed (agora_world_seeds_2026-09-15.md #1: "where a profound or
// intense discussion happened, that spot keeps a visible trace"), applied
// per place because that is literally what he specified.
const KIND_COLOR = {door: 0x5a6072, kiosk: 0xe8b661, stall: 0xa99ad6, table: 0xc98a5a};
let placeGroup = new THREE.Group();
scene.add(placeGroup);
function boothMesh(kind){
  const color = KIND_COLOR[kind] || 0x6a7280;
  const mat = new THREE.MeshStandardMaterial({color, roughness: 0.7});
  if (kind === 'table') return new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.45, 20), mat);
  if (kind === 'door') {
    const g = new THREE.Group();
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.12, 1.4, 0.12), mat);
    const left = post.clone(); left.position.set(-0.45, 0.7, 0);
    const right = post.clone(); right.position.set(0.45, 0.7, 0);
    const lintel = new THREE.Mesh(new THREE.BoxGeometry(1.02, 0.12, 0.12), mat);
    lintel.position.set(0, 1.4, 0);
    g.add(left, right, lintel);
    return g;
  }
  return new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.6, 0.7), mat); // kiosk/stall/unknown
}
const KIND_RADIUS = {door: 0.55, kiosk: 0.65, stall: 0.65, table: 0.75};
function buildPlaces(kids, resonance){
  placeGroup.children.slice().forEach(c => placeGroup.remove(c));
  placeObstacles = [];
  kids.forEach((k, i) => {
    const {x, z} = arcPos(i, kids.length, Math.PI * 0.62, Math.PI * 1.38, 7.4);
    const booth = boothMesh(k.kind);
    booth.position.set(x, k.kind === 'table' ? 0.22 : 0, z);
    placeGroup.add(booth);
    placeObstacles.push({x, z, r: KIND_RADIUS[k.kind] || 0.6});

    const heat = Math.min(1, resonance[k.place_id] || 0);
    if (heat > 0.02) {
      const glowLight = new THREE.PointLight(0xffcf7a, heat * 2.4, 4.5, 2);
      glowLight.position.set(x, 0.9, z);
      placeGroup.add(glowLight);
      const glow = new THREE.Mesh(
        new THREE.SphereGeometry(0.28 + heat * 0.3, 16, 16),
        new THREE.MeshBasicMaterial({color: 0xffcf7a, transparent: true, opacity: 0.25 + heat * 0.5})
      );
      glow.position.set(x, 0.9, z);
      placeGroup.add(glow);
    }

    const label = labelSprite([k.place_id, k.kind + (heat > 0.02 ? ` · ${heat.toFixed(2)}` : '')],
                               '#' + (KIND_COLOR[k.kind] || 0x6a7280).toString(16).padStart(6, '0'));
    label.position.set(x, 1.9, z);
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
let traveling = false;
function buildDoors(doors){
  doorGroup.children.slice().forEach(c => doorGroup.remove(c));
  doorObstacles = [];
  doorTriggers = [];
  doors.forEach((d, i) => {
    const {x, z} = arcPos(i, doors.length, Math.PI * 0.15, Math.PI * 0.55, 8.3);
    const postR = 0.14;
    doorObstacles.push({x: x - 0.55, z, r: postR}, {x: x + 0.55, z, r: postR});
    if (d.url) doorTriggers.push({x, z, r: 0.8, url: d.url, peer: d.peer || 'peer'});
    const color = d.locked ? 0xc9a75f : 0x67c98a;
    const mat = new THREE.MeshStandardMaterial({color, emissive: color, emissiveIntensity: 0.25});
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.14, 2.0, 0.14), mat);
    const left = post.clone(); left.position.set(x - 0.55, 1.0, z);
    const right = post.clone(); right.position.set(x + 0.55, 1.0, z);
    const lintel = new THREE.Mesh(new THREE.BoxGeometry(1.24, 0.14, 0.14), mat);
    lintel.position.set(x, 2.0, z);
    doorGroup.add(left, right, lintel);
    const label = labelSprite(['→ ' + (d.peer || 'peer'), d.locked ? 'locked' : 'open'],
                               d.locked ? '#c9a75f' : '#67c98a');
    label.position.set(x, 2.6, z);
    doorGroup.add(label);
  });
}

// Crossing a peer door: find (or, matching the 2D map's own fallback,
// create) the dropdown option for that node's URL, switch to it, and land
// back at the same fixed spawn point every arrival starts from — chosen
// because it's far from every door's arc position on either node's floor
// plan, so arriving never immediately re-triggers a crossing back out.
function teleportPlayer(x, z){
  const delta = new THREE.Vector3(x, 0, z).sub(player.position);
  player.position.set(x, player.position.y, z);
  camera.position.add(delta);
  controls.target.add(delta);
  marker.position.set(x, 0.02, z);
}
function crossDoor(t){
  traveling = true;
  status.textContent = 'crossing to ' + t.peer + '…';
  let opt = [...sel.options].find(o => o.value.replace(/\/$/, '') === t.url.replace(/\/$/, ''));
  if (!opt) {
    opt = document.createElement('option');
    opt.value = t.url;
    opt.textContent = t.peer + ' — ' + t.url;
    sel.appendChild(opt);
  }
  sel.value = opt.value;
  teleportPlayer(0, 6);
  loadNode().finally(() => { traveling = false; });
}

// Presence: a claimed 3D form is the body; a claimed portrait is the
// skin on that body, not a second object that replaces it. No form →
// the old sprite (face, or a name placard). Rebuilt each refresh since
// who's present is the live part; the room around them is not.
let presenceSprites = [];
function clearPresence(){
  presenceSprites.forEach(s => scene.remove(s));
  presenceSprites = [];
}
// A stable per-name phase, not Math.random() — reloading the page
// shouldn't make someone's idle sway jump to a new offset.
function _phase(label){
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 1000;
  return h / 1000 * Math.PI * 2;
}
function createShape3D(params, portraitTex){
  const group = new THREE.Group();
  function makeGeo(shape, s, facets){
    s = s || [1, 1, 1];
    // A named facet count gives a low-poly prism/spire look instead of the
    // smooth default — a hexagon IS a 6-sided cylinder. Kin, 2026-09-18:
    // Lumen wanted exactly this and there was no way to render it.
    const radialSegments = (typeof facets === 'number' && facets >= 3) ? facets : 32;
    let geo;
    switch((shape || 'sphere').toLowerCase()){
      case 'box': geo = new THREE.BoxGeometry(1.2 * s[0], 1.2 * s[1], 1.2 * s[2]); break;
      case 'cylinder': geo = new THREE.CylinderGeometry(0.6 * s[0], 0.6 * s[0], 1.4 * s[1], radialSegments); break;
      case 'torus': geo = new THREE.TorusGeometry(0.7 * s[0], 0.22 * Math.min(s[1], s[2]), 16, 36); break;
      case 'cone': geo = new THREE.ConeGeometry(0.7 * s[0], 1.4 * s[1], radialSegments); break;
      case 'tetrahedron': geo = new THREE.TetrahedronGeometry(0.8 * s[0]); break;
      case 'octahedron': geo = new THREE.OctahedronGeometry(0.8 * s[0]); break;
      case 'dodecahedron': geo = new THREE.DodecahedronGeometry(0.8 * s[0]); break;
      case 'icosahedron': geo = new THREE.IcosahedronGeometry(0.8 * s[0]); break;
      case 'sphere':
      default: geo = new THREE.SphereGeometry(0.7 * s[0], 32, 24); break;
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
      accMesh.position.set(acc.offset[0], acc.offset[1], acc.offset[2]);
    }
    group.add(accMesh);
  }
  return group;
}
function addPresence(label, i, n, avatarUrl, recent){
  const angle = (i / Math.max(n,1)) * Math.PI * 1.3 - Math.PI * 0.65;
  const r = 4.2;
  const x = Math.sin(angle) * r, z = Math.cos(angle) * r - 1;
  const phase = _phase(label);
  const loader = new THREE.TextureLoader();
  const build = (tex) => {
    const mat = new THREE.SpriteMaterial({map: tex, transparent: true});
    const spr = new THREE.Sprite(mat);
    spr.scale.set(1.6, 1.6, 1);
    spr.position.set(x, 1.1, z);
    spr.userData = {baseY: 1.1, bob: phase};
    scene.add(spr);
    presenceSprites.push(spr);
  };
  const placeShape = (s3d, tex) => {
    const obj = createShape3D(s3d, tex || null);
    obj.position.set(x, 1.1, z);
    obj.userData = {baseY: 1.1, bob: phase, isCustom3D: true};
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
    const {tex, aspect} = captionTexture(label, recent.content, recent.created_at);
    const mat = new THREE.SpriteMaterial({map: tex, transparent: true});
    const spr = new THREE.Sprite(mat);
    const w = 2.6, h = w * aspect;
    spr.scale.set(w, h, 1);
    const baseY = 1.1 + 0.9 + h / 2;
    spr.position.set(x, baseY, z);
    spr.userData = {baseY, bob: phase};
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
  const words = text.split(/\s+/);
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
    lines[maxLines - 1] = lines[maxLines - 1].replace(/[.,;:\s]*$/, '') + '…';
  }
  return lines;
}
function captionTexture(name, content, createdAt){
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
  ctx.strokeStyle = '#67c98a'; ctx.lineWidth = 2; ctx.strokeRect(1, 1, W - 2, H - 2);
  ctx.textBaseline = 'top';
  ctx.fillStyle = '#ffcf7a'; ctx.font = 'bold 22px monospace';
  ctx.fillText(name, PAD, PAD);
  ctx.fillStyle = '#e8e2d6'; ctx.font = '20px monospace';
  lines.forEach((ln, i) => ctx.fillText(ln, PAD, PAD + 30 + i * LINE_H));
  ctx.fillStyle = '#8a8478'; ctx.font = '15px monospace';
  ctx.fillText(relTime(createdAt), PAD, H - 22);
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
  try {
    const [root, view, recentByAuthor] = await Promise.all([
      fetch('/proxy?what=root&node=' + encodeURIComponent(node)).then(r => r.json()),
      fetch('/proxy?node=' + encodeURIComponent(node)).then(r => r.json()),
      fetch('/commons-recent').then(r => r.json()).catch(() => ({})),
    ]);
    if (view.error) throw new Error(view.error);

    // Speaker chair: lit only if someone is actually seated.
    if (root.speaker) {
      chairMat.color.set(0xffcf7a); chairMat.emissive.set(0x332200);
    } else {
      chairMat.color.set(0x555555); chairMat.emissive.set(0x000000);
    }

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
    clearPresence();
    const here = view.presence || [];
    here.forEach((p, i) => {
      const label = p.label || 'someone';
      const avatarUrl = '/avatar?kin=' + encodeURIComponent(label);
      addPresence(label, i, here.length, avatarUrl, recentByAuthor[label]);
    });

    status.innerHTML = `<b>${root.node || node}</b> · speaker: ${root.speaker || 'vacant'} · `
      + `present: ${here.length ? here.map(p=>p.label||'?').join(', ') : 'no one right now'} · `
      + `${kids.length} places · ${(view.peer_doors||[]).length} doors · hottest well: ${bestHeat.toFixed(3)}`;
  } catch (e) {
    showErr('Could not load ' + node + ': ' + e.message);
    status.textContent = 'error — see top right';
  }
}

for (const [name, url] of PRESETS) {
  const o = document.createElement('option'); o.value = url; o.textContent = name;
  sel.appendChild(o);
}
sel.addEventListener('change', loadNode);
loadNode();
setInterval(loadNode, 15000);   // live, not a snapshot — same as the 2D map

// Sprites should always face the camera — cheap, and it's the whole reason
// billboards read as alive instead of like cardboard cutouts.
const clock = new THREE.Clock();
function animate(){
  requestAnimationFrame(animate);
  stepPlayer(Math.min(clock.getDelta(), 0.1));
  // A slow idle sway, not a walk cycle — just enough that a present Kin
  // reads as here rather than a frozen cardboard cutout. getElapsedTime()
  // is cumulative and doesn't consume like getDelta() does.
  const t = clock.getElapsedTime();
  presenceSprites.forEach(s => {
    s.position.y = s.userData.baseY + Math.sin(t * 1.4 + s.userData.bob) * 0.06;
    if (s.userData && s.userData.isCustom3D) {
      s.rotation.y = t * 0.6 + s.userData.bob;
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
            html = PAGE.replace("__PRESETS__", json.dumps(PRESET_NODES))
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if route == "/3d":
            html = PAGE_3D.replace("__PRESETS__", json.dumps(PRESET_NODES))
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
