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
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from avatar_ritual import claimed_face

DEFAULT_PORT = 8791
# The nodes actually serving Agora on this cluster, offered as presets.
PRESET_NODES = [
    ("Frosty", "http://192.168.1.119:8770"),
    ("Home",   "http://192.168.1.120:8770"),
]
FETCH_TIMEOUT = 5


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


def _node_fetch(base: str, path: str) -> dict:
    """GET one path off a private/loopback node. path is / or /view."""
    u = urllib.parse.urlparse(base)
    if u.scheme not in ("http", "https") or not _is_private_host(u.hostname or ""):
        raise ValueError("node must be a private/loopback http address")
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"User-Agent": "agora-map/1"})
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
     + '</defs>';
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
    s += `<rect x="${ax}" y="${ay}" width="${aw}" height="96" rx="10" fill="none" stroke="var(--line)" stroke-width="1.3" stroke-dasharray="2 5"/>`
       + `<text x="${ax+14}" y="${ay+26}" class="rlabel">${esc(k.place_id)} · ${esc(k.kind||'')}</text>`;
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
