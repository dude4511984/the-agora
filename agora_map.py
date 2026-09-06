#!/usr/bin/env python3
"""agora_map.py — a top-down renderer for an Agora node's world.

The node already serves the snapshot both clients render from: GET /view returns
{places, presence, listings, peer_doors} filtered by the caller's ring. This is
the human's half of that — a live map of one node: rooms as nested boxes, the
Kin standing in them, the wares at each kiosk, and the pinned peers as doors you
can step through.

Standalone on purpose. The node stays stdlib-minimal and the product app stays
the product; this is a small tool that talks to any node over the wire. It runs
its own stdlib server so the browser fetches /view same-origin (the node sends
no CORS headers), and it will only proxy to a private/loopback address, so it
cannot be turned into an SSRF relay to the wider internet.

    python3 agora_map.py [port]        # default 8791
    then open http://localhost:8791
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def _node_view(base: str) -> dict:
    u = urllib.parse.urlparse(base)
    if u.scheme not in ("http", "https") or not _is_private_host(u.hostname or ""):
        raise ValueError("node must be a private/loopback http address")
    url = base.rstrip("/") + "/view"
    req = urllib.request.Request(url, headers={"User-Agent": "agora-map/1"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read(2 * 1024 * 1024).decode())


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agora Map</title>
<style>
  :root{ --bg:#0d1117; --bg2:#161b22; --bg3:#21262d; --line:#30363d;
         --text:#e6edf3; --dim:#8b949e; --green:#3fb950; --cyan:#58a6ff;
         --amber:#d29922; --red:#f85149; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--bg); color:var(--text);
        font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  header{ display:flex; align-items:center; gap:.75rem; flex-wrap:wrap;
          padding:.75rem 1rem; background:var(--bg2); border-bottom:1px solid var(--line);
          position:sticky; top:0; }
  header h1{ font-size:1rem; margin:0; letter-spacing:.02em; }
  header .sp{ flex:1; }
  select,input,button{ background:var(--bg3); color:var(--text);
        border:1px solid var(--line); border-radius:6px; padding:.35rem .6rem;
        font:inherit; }
  button{ cursor:pointer; }
  button:hover{ border-color:var(--cyan); }
  .status{ color:var(--dim); font-size:.85rem; }
  .status .ok{ color:var(--green); } .status .err{ color:var(--red); }
  main{ padding:1rem; }
  .place{ border:1px solid var(--line); border-radius:10px; margin:.6rem 0;
          background:var(--bg2); }
  .place > .head{ display:flex; align-items:center; gap:.5rem; padding:.5rem .75rem;
          border-bottom:1px solid var(--line); }
  .place > .body{ padding:.35rem .75rem .6rem 1.1rem; }
  .kind{ font-size:.68rem; text-transform:uppercase; letter-spacing:.08em;
         color:var(--bg); background:var(--dim); border-radius:999px;
         padding:.1rem .5rem; }
  .kind.concourse{ background:var(--cyan); } .kind.commons{ background:var(--green); }
  .kind.kiosk{ background:var(--amber); } .kind.stall{ background:#a371f7; }
  .pid{ font-weight:650; } .pid .n{ color:var(--dim); font-weight:400; font-size:.85rem; }
  .row{ margin:.25rem 0; }
  .tag{ display:inline-block; background:var(--bg3); border:1px solid var(--line);
        border-radius:999px; padding:.08rem .55rem; margin:.12rem .25rem .12rem 0;
        font-size:.85rem; }
  .who{ color:var(--green); } .ware{ color:var(--amber); }
  .door{ background:var(--bg3); border:1px solid var(--cyan); color:var(--cyan);
         border-radius:8px; padding:.2rem .6rem; margin:.15rem .3rem .15rem 0;
         cursor:pointer; font-size:.85rem; }
  .door:hover{ background:#132030; }
  .door .lock{ opacity:.7; }
  .empty{ color:var(--dim); font-style:italic; }
  .label{ color:var(--dim); font-size:.78rem; text-transform:uppercase;
          letter-spacing:.06em; margin-right:.35rem; }
</style></head>
<body>
<header>
  <h1>Agora&nbsp;Map</h1>
  <select id="node"></select>
  <button id="mode">place view →</button>
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
let timer = null;
let MODE = 'map';        // 'map' (top-down) | 'place' (you-are-here)
let CURRENT = null;      // current place_id in place mode
let STATE = null;        // last {data, byId, roots} for navigation

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

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

function renderPlace(n){
  const kind = esc(n.p.kind||'place');
  const div = document.createElement('div');
  div.className = 'place';
  const occ = n.occupants.map(o =>
    `<span class="tag who">${esc(o.label || (o.key_id||'').slice(0,8) || 'someone')}</span>`).join('');
  const wares = n.wares.map(w =>
    `<span class="tag ware">${esc(w.title || w.listing_id || 'ware')}</span>`).join('');
  const doors = n.doors.map(d =>
    `<button class="door" data-url="${esc(d.url)}" ${d.url?'':'disabled'} title="${esc(d.peer_key_id||'')}">`
    + `${d.locked?'<span class="lock">🔒</span> ':''}${esc(d.peer||'peer')} →</button>`).join('');
  div.innerHTML =
    `<div class="head"><span class="kind ${kind}">${kind}</span>`
    + `<span class="pid">${esc(n.p.place_id)}</span></div>`
    + `<div class="body">`
    + (occ ? `<div class="row"><span class="label">here</span>${occ}</div>`
           : `<div class="row empty">no one here</div>`)
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
    const world = document.getElementById('world');
    world.innerHTML = '';
    const tree = buildTree(data);
    STATE = { data, byId: tree.byId, roots: tree.roots };
    if(!tree.roots.length){
      world.innerHTML = '<p class="empty">This node publishes no places yet.</p>';
    } else if(MODE === 'place'){
      renderPlaceView(world);
    } else {
      tree.roots.sort((a,b)=> (a.p.place_id>b.p.place_id?1:-1)).forEach(n => world.appendChild(renderPlace(n)));
    }
    const when = data.as_of_unix_ms ? new Date(data.as_of_unix_ms).toLocaleTimeString() : '';
    const signed = data.signature ? ' · <span class="ok">signed</span>' : '';
    st.innerHTML = `<span class="ok">${esc(data.node||'node')}</span> · ${(data.places||[]).length} places · `
      + `${(data.presence||[]).length} here · ${(data.peer_doors||[]).length} doors · ${when}${signed}`;
  }catch(e){
    st.innerHTML = '<span class="err">' + esc(e.message) + '</span>';
  }
}

// ── place view: one room at a time, doors you walk through ──
function renderPlaceView(world){
  const { byId, roots, data } = STATE;
  if(!CURRENT || !byId[CURRENT]){
    CURRENT = roots.slice().sort((a,b)=>a.p.place_id<b.p.place_id?-1:1)[0].p.place_id;
  }
  const n = byId[CURRENT];
  const kind = n.p.kind || 'place';
  const parent = n.p.parent || '';
  const occ = n.occupants.length
    ? n.occupants.map(o=>`<span class="tag who">${esc(o.label||(o.key_id||'').slice(0,8)||'someone')}</span>`).join('')
    : '<span class="empty">no one here yet</span>';
  const wares = (kind === 'kiosk')
    ? (n.wares.length ? n.wares.map(w=>`<span class="tag ware">${esc(w.title||w.listing_id)}</span>`).join('')
                      : '<span class="empty">no wares listed</span>')
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
  if(b.dataset.place){                 // walk to another room on this node
    CURRENT = b.dataset.place; load(); return;
  }
  if(!b.dataset.url) return;
  // step through a door to a peer node: re-point the map, land at its root
  const url = b.dataset.url;
  let opt = [...sel.options].find(o => o.value.replace(/\\/$/,'') === url.replace(/\\/$/,''));
  if(!opt){ opt = document.createElement('option'); opt.value = url; opt.textContent = b.textContent.replace(/[🔒→\\s]/g,'') + ' — ' + url; sel.appendChild(opt); }
  CURRENT = null;                      // new node, start at its concourse
  sel.value = opt.value; load();
});

document.getElementById('mode').addEventListener('click', () => {
  MODE = (MODE === 'map') ? 'place' : 'map';
  document.getElementById('mode').textContent = (MODE === 'map') ? 'place view →' : '← map view';
  CURRENT = null; load();
});

document.getElementById('refresh').addEventListener('click', load);
sel.addEventListener('change', () => { CURRENT = null; load(); });
function arm(){ if(timer) clearInterval(timer); if(document.getElementById('live').checked) timer = setInterval(load, 5000); }
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
        if self.path == "/" or self.path.startswith("/index"):
            html = PAGE.replace("__PRESETS__", json.dumps(PRESET_NODES))
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if self.path.startswith("/proxy"):
            qs = urllib.parse.urlparse(self.path).query
            node = urllib.parse.parse_qs(qs).get("node", [""])[0]
            try:
                view = _node_view(node)
                self._send(200, json.dumps(view).encode(), "application/json")
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
