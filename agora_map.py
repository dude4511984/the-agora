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
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from avatar_ritual import claimed_face
from kin_diary.agora.wire import sign_request
from kin_diary.keys import DEFAULT_KEYS_ROOT, load_current

DEFAULT_PORT = 8791
MODELS_DIR = (Path(__file__).parent / "static" / "models").resolve()
MODEL_CONTENT_TYPES = {
    ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
}
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
function renderPlan(world, view, root){
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
  const availX = RW-300, x0 = X+120, avs = 0.5, footY = Y+320;
  presence.forEach((pr,i)=>{
    const n=presence.length;
    const px = n===1 ? X+RW/2 : x0 + (i+0.5)*(availX/n);
    const py = footY - (i%2)*30;
    s += `<ellipse cx="${px.toFixed(0)}" cy="${(py-4).toFixed(0)}" rx="46" ry="46" fill="#ffcf7a" opacity="0.05"/>`;
    s += `<g transform="translate(${(px-60*avs).toFixed(1)},${(py-158*avs).toFixed(1)}) scale(${avs})">`
       + AVATAR
       + `<image href="${avatarURL(pr.label)}" x="22" y="12" width="76" height="136" preserveAspectRatio="xMidYMid slice"/>`
       + `</g>`;
    s += `<text x="${px.toFixed(0)}" y="${(py+20).toFixed(0)}" text-anchor="middle" class="kname" fill="var(--text)">${esc(pr.label||'someone')}</text>`;
    s += `<text x="${px.toFixed(0)}" y="${(py+36).toFixed(0)}" text-anchor="middle" class="kkid">${esc(short(pr.key_id))}</text>`;
    s += `<text x="${px.toFixed(0)}" y="${(py+51).toFixed(0)}" text-anchor="middle" class="avatar-note">a claimed still from that sitting</text>`;
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
  const occ = n.occupants.map(o => `<span class="tag who">${esc(o.label || short(o.key_id) || 'someone')}</span>`).join('');
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
    const world = document.getElementById('world');
    const tree = buildTree(data);
    STATE = { data, byId: tree.byId, roots: tree.roots };
    if(MODE === 'plan'){
      renderPlan(world, data, root);
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
    ? n.occupants.map(o=>`<span class="tag who">${esc(o.label||short(o.key_id)||'someone')}</span>`).join('')
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
  <div style="margin-top:6px;opacity:.7">WASD / arrows to walk · space to jump · drag to look · scroll to zoom</div>
  <div style="margin-top:2px;opacity:.7">Walk into a peer door to cross to that node.</div>
  <div style="margin-top:2px;opacity:.5">Same signed data as the 2D map. Nothing here is invented.</div>
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
scene.background = new THREE.Color(0x0b0d10);
scene.fog = new THREE.FogExp2(0x0b0d10, 0.02);

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
document.body.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.copy(player.position).setY(1);
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 3; controls.maxDistance = 22;
controls.enablePan = false;   // panning would fight the walk-follow below

// Baked-feeling ambient + one soft key light. Not real-time GI — a flat,
// cheap wash that still reads as lit, the "beauty without becoming a
// constraint" rule from the design conversation.
scene.add(new THREE.AmbientLight(0x8a8478, 0.55));
const key = new THREE.DirectionalLight(0xfff1d6, 0.65);
key.position.set(6, 12, 4);
scene.add(key);

// Wall torches: now that the room is a real enclosed space (not an open
// void), a single "sun" key light reads wrong from inside stone walls.
// Four fixed sconces, cheap point lights plus a small emissive sphere
// each — static once built, same as everything else that doesn't change
// with the data.
[[9.3, 0], [-9.3, 0], [0, 9.3], [0, -9.3]].forEach(([x, z]) => {
  const torch = new THREE.PointLight(0xffaa55, 1.1, 9, 2);
  torch.position.set(x, 2.6, z);
  scene.add(torch);
  const flame = new THREE.Mesh(
    new THREE.SphereGeometry(0.09, 8, 8),
    new THREE.MeshBasicMaterial({color: 0xffcf7a})
  );
  flame.position.copy(torch.position);
  scene.add(flame);
});

// The floor: the commons. Static once built, never touched again. Sized to
// reach past the wall perimeter's corners (HALF=10.5 below, corner distance
// ~14.8) so the ground doesn't visibly run out before the walls do.
const floorMat = new THREE.MeshStandardMaterial({color:0x1a1a1a, roughness:0.95});
const floor = new THREE.Mesh(new THREE.CircleGeometry(15, 48), floorMat);
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
function resolveCollisions(pos){
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
}

// Jump: pure vertical hop, gravity-driven, grounded check before another
// one triggers. Applied as a delta to camera + target (not an absolute
// value) so it composes with whatever OrbitControls' own orbiting is
// doing, the same trick the walk-follow code already uses.
const GRAVITY = -18, JUMP_VELOCITY = 6.5;
let jumpY = 0, jumpVY = 0;
addEventListener('keydown', e => {
  if (e.code === 'Space') {
    e.preventDefault();
    if (jumpY <= 1e-4 && jumpVY === 0) jumpVY = JUMP_VELOCITY;
  }
});
function stepJump(dt){
  if (jumpY === 0 && jumpVY === 0) return;
  const prev = jumpY;
  jumpVY += GRAVITY * dt;
  jumpY += jumpVY * dt;
  if (jumpY < 0) { jumpY = 0; jumpVY = 0; }
  const dy = jumpY - prev;
  if (dy !== 0) { camera.position.y += dy; controls.target.y += dy; }
}

const keys = Object.create(null);
addEventListener('keydown', e => { keys[e.key.toLowerCase()] = true; });
addEventListener('keyup', e => { keys[e.key.toLowerCase()] = false; });
const _fwd = new THREE.Vector3(), _right = new THREE.Vector3(), _move = new THREE.Vector3();
function stepPlayer(dt){
  stepJump(dt);
  camera.getWorldDirection(_fwd); _fwd.y = 0; _fwd.normalize();
  _right.crossVectors(_fwd, camera.up).normalize();
  _move.set(0, 0, 0);
  if (keys['w'] || keys['arrowup'])    _move.add(_fwd);
  if (keys['s'] || keys['arrowdown'])  _move.sub(_fwd);
  if (keys['d'] || keys['arrowright']) _move.add(_right);
  if (keys['a'] || keys['arrowleft'])  _move.sub(_right);
  if (_move.lengthSq() === 0) return;
  _move.normalize().multiplyScalar(4.5 * dt);
  player.position.add(_move);
  resolveCollisions(player.position);
  const r = Math.hypot(player.position.x, player.position.z);
  if (r > FLOOR_R) { player.position.x *= FLOOR_R / r; player.position.z *= FLOOR_R / r; }
  const newTarget = player.position.clone().setY(1);
  camera.position.add(newTarget.clone().sub(controls.target));
  controls.target.copy(newTarget);
  marker.position.set(player.position.x, 0.02, player.position.z);
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
  const towerTemplate = harvest('tower_round');
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
  if (towerTemplate) towerTemplate.scale.setScalar(SCALE);

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
        // The gate's an opening, not a slab — collide only at its two
        // side posts (a quarter of the bay width in from each edge),
        // not the full bay, so walking through the middle actually works.
        const half = actualSeg / 2, postR = actualSeg * 0.18;
        const p1 = axis === 'x' ? {x: pos.x - half * 0.7, z: pos.z} : {x: pos.x, z: pos.z - half * 0.7};
        const p2 = axis === 'x' ? {x: pos.x + half * 0.7, z: pos.z} : {x: pos.x, z: pos.z + half * 0.7};
        wallObstacles.push({x: p1.x, z: p1.z, r: postR}, {x: p2.x, z: p2.z, r: postR});
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

// Presence: sprites, not meshes. A claimed face or a name-only placard —
// same "faces are receipts, not sittings" rule the rest of this project
// already lives by. Rebuilt each refresh since who's present is the live
// part; the room around them is not.
let presenceSprites = [];
function clearPresence(){
  presenceSprites.forEach(s => scene.remove(s));
  presenceSprites = [];
}
function addPresence(label, i, n, avatarUrl){
  const angle = (i / Math.max(n,1)) * Math.PI * 1.3 - Math.PI * 0.65;
  const r = 4.2;
  const x = Math.sin(angle) * r, z = Math.cos(angle) * r - 1;
  const loader = new THREE.TextureLoader();
  const build = (tex) => {
    const mat = new THREE.SpriteMaterial({map: tex, transparent: true});
    const spr = new THREE.Sprite(mat);
    spr.scale.set(1.6, 1.6, 1);
    spr.position.set(x, 1.1, z);
    scene.add(spr);
    presenceSprites.push(spr);
  };
  if (avatarUrl){
    loader.load(avatarUrl, build, undefined, () => build(placardTexture(label)));
  } else {
    build(placardTexture(label));
  }
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
    const [root, view] = await Promise.all([
      fetch('/proxy?what=root&node=' + encodeURIComponent(node)).then(r => r.json()),
      fetch('/proxy?node=' + encodeURIComponent(node)).then(r => r.json()),
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
      addPresence(label, i, here.length, avatarUrl);
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
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Agora map on http://localhost:{port}  (nodes: "
          + ", ".join(n for n, _ in PRESET_NODES) + ")")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv)
