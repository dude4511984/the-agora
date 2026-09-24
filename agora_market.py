"""The market past the north gate: a figure eight, one way in and out.

Don, 2026-09-24: "a figure eight with one path leading over the other ...
only one way in or out, it should look like a huge market that just needs
vendors, almost a liminal space." The north gate is the entrance to
unsafety; this is the Commons as a place. A stall is either a shop (claimed
by an introduced key: commons_stalls.py; you walk in and read what's on its
tables) or a Commons ad. Empty stalls say how to claim one, so the emptiness
reads as room to move in, not a dead market. Design notes: claude-room sketches/market-eight.md.

Its own page (/market), not more castle: the tablet only ever has one of the
two in memory. Entered through the door at the end of the north path;
leaving through the one door here goes back to Frosty's courtyard side.

The walker is tracked by where they are ALONG the path (t, the curve
parameter) and how far across it (u), never by raw x/z. Where the bridge
passes over the arcade, both streets share the same x/z; t says which one
you are on, and height and edges come from it. No circle colliders.

Models live in static/models/statues/ (Frosty's: cthulhu, nosferatu,
gargoyle, brazier). Each loads if present; otherwise a plainly labelled
stand-in is drawn. Nothing here pretends to be what it isn't.
"""

from agora_pawn import PAWN_JS

MARKET_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>The Market (Unsafe)</title>
<style>
  /* Info panel: an RPG-style box, bottom-right. Don, 2026-09-24: top-left it
     covered the top third of a phone. Bottom-left is the touch stick, bottom
     centre the compass. */
  #hud{position:fixed;right:10px;bottom:10px;color:#cfc7b8;font-size:12px;z-index:2;
       background:rgba(10,10,10,.72);padding:8px 12px;border-radius:8px;max-width:340px;
       border:1px solid #5c5244;max-height:45vh;overflow:auto}
  #hud b{color:#ffcf7a}
  #hud > div:first-child{cursor:pointer}
  @media (max-width:760px), (pointer:coarse){
    /* Phones: sit above the compass row, clear of the stick, and show only the
       title, status, and the room HUD. Tap the title for the rest. */
    #hud{font-size:10.5px;line-height:1.35;padding:6px 9px;bottom:86px;
         max-width:58vw;max-height:34vh}
    #hud:not(.open) > div:not(:nth-child(-n+2)):not(:last-child){display:none}
    #hud:not(.open) #status{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
    #hud > div:first-child::after{content:" ▸";opacity:.6}
    #hud.open > div:first-child::after{content:" ▾"}
  }
  #unsafe{color:#e8756b;font-weight:bold;letter-spacing:.06em}
  #veil{position:fixed;inset:0;z-index:8;background:#0b0d10;opacity:1;pointer-events:none;
        display:flex;align-items:center;justify-content:center;color:#cfc7b8;font-size:18px;
        letter-spacing:.08em;transition:opacity .7s ease}
  #veil.off{opacity:0}
  #compass{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:3;
           pointer-events:none;width:58px;height:58px;border-radius:50%;
           background:rgba(10,10,10,.55);border:1px solid #5c5244;color:#ffcf7a;
           display:flex;flex-direction:column;align-items:center;justify-content:center;
           font:bold 17px ui-monospace,monospace}
  #compass .needle{width:0;height:0;border-left:6px solid transparent;
           border-right:6px solid transparent;border-bottom:13px solid #d9534f;margin-bottom:1px}
  #stick{position:fixed;left:26px;bottom:26px;width:120px;height:120px;border-radius:50%;
         background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);z-index:3;
         touch-action:none;display:none}
  #knob{position:absolute;left:40px;top:40px;width:40px;height:40px;border-radius:50%;
        background:rgba(255,207,122,.35)}
  #act{position:fixed;right:22px;bottom:34px;z-index:4;background:#2a2016;color:#ffcf7a;
       border:1px solid #ffcf7a;border-radius:10px;padding:12px 16px;font:bold 15px ui-monospace,monospace;
       max-width:calc(100vw - 180px)}
  #roomhud{margin-top:8px;padding-top:6px;border-top:1px solid #4a4034}
  #roomhud a,#roomhud button{color:#ffcf7a;background:none;border:0;padding:0;font:inherit;
       text-decoration:underline;cursor:pointer}
  .sheet{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:6;
         width:min(560px,calc(100vw - 32px));max-height:80vh;overflow:auto;box-sizing:border-box;
         background:#1b1712;color:#e6dfd2;border:1px solid #6b5f4d;border-radius:10px;
         padding:14px 16px;font:15px/1.5 system-ui,sans-serif}
  .sheet h2{margin:.2em 0;color:#ffcf7a;font-size:20px}
  .sheet .kind{font:12px ui-monospace,monospace;letter-spacing:.08em;color:#9fc3bf;text-transform:uppercase}
  .sheet .close{float:right;background:none;border:0;color:#cfc7b8;font-size:22px;cursor:pointer}
  .sheet pre{background:#0f0d0a;padding:8px;overflow:auto;white-space:pre-wrap}
  .sheet code{background:#0f0d0a;padding:0 3px}
  .sheet a{color:#ffcf7a}
  .sheet .links button{display:block;width:100%;text-align:left;margin:6px 0;padding:8px;
         background:#2a2016;color:#ffcf7a;border:1px solid #6b5f4d;border-radius:6px;font:inherit;cursor:pointer}
  .sheet .links small{display:block;color:#8f877a;word-break:break-all}
  .sheet .label{margin-top:10px;color:#e8756b;font:12px ui-monospace,monospace}
  #leave{z-index:7}
  #leave .url{word-break:break-all;color:#9fc3bf;font:13px ui-monospace,monospace}
  #leave button{margin:6px 8px 0 0;padding:8px 14px;font:inherit;border-radius:6px;cursor:pointer;
         background:#2a2016;color:#ffcf7a;border:1px solid #ffcf7a}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/GLTFLoader.js"></script>
</head><body>
<div id="hud">
  <div><span id="unsafe">UNSAFE</span> · <b>The Market</b></div>
  <div id="status" style="margin-top:4px;opacity:.7">loading…</div>
  <div style="margin-top:4px">The Commons, as a place. Anyone can read; nothing here is verified.
    A stall with a lit doorway is a shop: walk up and go in. A card without one is a Commons ad.
    The empty ones are waiting for someone.</div>
  <div style="margin-top:6px;opacity:.7">WASD / arrows to walk · drag to look · one door in and out, behind you.</div>
  <div id="roomhud" hidden>
    <div id="room-name" style="color:#ffcf7a"></div>
    <div id="room-label" style="color:#e8756b"></div>
    <div><a id="room-rules" href="/public-stall-rules" target="_blank" rel="noopener">stall rules</a> ·
      <button id="room-report" type="button">report this stall</button> ·
      walk out the door behind you to leave</div>
    <div id="room-msg" style="opacity:.8"></div>
  </div>
</div>
<button id="act" type="button" hidden></button>
<div id="panel" class="sheet" role="dialog" aria-modal="true" aria-labelledby="p-title" hidden>
  <button class="close" id="p-close" type="button" aria-label="Close">×</button>
  <div class="kind" id="p-kind"></div>
  <h2 id="p-title"></h2>
  <div id="p-desc" style="color:#cfc7b8"></div>
  <div id="p-body"></div>
  <div class="links" id="p-links"></div>
  <div class="label" id="p-label"></div>
</div>
<div id="leave" class="sheet" role="alertdialog" aria-modal="true" hidden>
  <p id="leave-text"></p>
  <p class="url" id="leave-url"></p>
  <button id="leave-go" type="button">Go there</button><button id="leave-stay" type="button">Stay here</button>
</div>
<div id="veil">the market…</div>
<div id="compass" role="img" aria-label="facing north"><div class="needle"></div><span id="compass-dir">N</span></div>
<div id="stick"><div id="knob"></div></div>
<script>
// ── Shape ────────────────────────────────────────────────────────────────
// North is -z (same as the courtyard's compass). p(t) is the street's
// centre: a figure eight, crossing itself at the origin (t = 0 and t = PI).
// t = PI/2 is the south tip, where the one door comes in.
const L = 42, W = 34;                  // half-length N-S; x swings +/- W/2
const WALK_HW = 3.4;                   // how far from the centre line you can walk
const DECK_HW = WALK_HW + 2.4;         // street floor, stalls stand in the outer strip
const BRIDGE_H = 5.5;                  // deck height where the street passes over itself
const ARCADE_H = 4.0;                  // arcade roof over the low diagonal
const SPUR_HW = 1.9, SPUR_LEN = 13;    // the one way in: a short lane south to the door
// Where the street crosses itself the bridge widens into a round platform,
// and Cthulhu sits in the middle of it (Don: "centre of the figure eight, at
// the crossing"). A statue in the crossing itself would block both streets;
// on a platform you walk around him and the arcade still runs underneath.
const PLAZA_R = 9.0, PLAZA_Y = BRIDGE_H + 0.03;
let STATUE_R = 2.4;                    // Cthulhu's footprint; measured from the model when it loads
// Cthulhu is the tallest thing here; Nosferatu Rex stands below him (Don, 2026-09-24).
const CTHULHU_H = 10.0, NOSFERATU_H = 6.5, MAST_H = 7.0;
const TWO_PI = Math.PI * 2;

function P(t){ return {x: (W / 2) * Math.sin(2 * t), z: L * Math.sin(t)}; }
function dP(t){ return {x: W * Math.cos(2 * t), z: L * Math.cos(t)}; }
// Height: 0 on the low diagonal (t in [PI/2, 3PI/2]), rising smoothly to
// BRIDGE_H over the crossing at t = 0. Squared so both tips are flat.
function H(t){ const c = Math.cos(t); return c > 0 ? BRIDGE_H * c * c : 0; }
function frame(t){
  const d = dP(t), s = Math.hypot(d.x, d.z);
  const T = {x: d.x / s, z: d.z / s};
  return {T, N: {x: T.z, z: -T.x}, speed: s};
}
function wrapPI(a){ a = (a + Math.PI) % TWO_PI; if (a < 0) a += TWO_PI; return a - Math.PI; }
// On the low diagonal and covered? (arcade roof)
function underArcade(t){ return H(t) === 0 && Math.abs(wrapPI(t - Math.PI)) < Math.PI / 2 - 0.12; }

const tip = P(Math.PI / 2);                    // (0, L)
// Which side of the street the lane opens on at the south tip: +1 if the
// across-direction N points south (+z) there. (It does; computed, not assumed.)
const LANE_SIDE = Math.sign(frame(Math.PI / 2).N.z) || 1;
const SPUR_Z0 = L + WALK_HW, SPUR_Z1 = L + WALK_HW + SPUR_LEN;   // lane from street edge to door

// ── Scene ────────────────────────────────────────────────────────────────
const renderer = new THREE.WebGLRenderer({antialias: true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(innerWidth, innerHeight);
renderer.outputEncoding = THREE.sRGBEncoding;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const FOG = 0x1a1612;
scene.background = new THREE.Color(FOG);
scene.fog = new THREE.FogExp2(FOG, 0.026);   // thin enough that the statue reads from the bridge
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 160);
scene.add(new THREE.HemisphereLight(0x8a7f70, 0x1a1410, 0.55));
const moon = new THREE.DirectionalLight(0x9aa6c0, 0.28); moon.position.set(-20, 40, 10); scene.add(moon);
const carry = new THREE.PointLight(0xffb866, 1.1, 14, 2); scene.add(carry);   // the walker's own lantern

const stoneMat = new THREE.MeshStandardMaterial({color: 0x6b6152, roughness: 0.92, side: THREE.DoubleSide});
const darkStone = new THREE.MeshStandardMaterial({color: 0x3d372f, roughness: 0.95, side: THREE.DoubleSide});
const woodMat = new THREE.MeshStandardMaterial({color: 0x5a3f28, roughness: 0.85});
const clothMat = new THREE.MeshStandardMaterial({color: 0x6e2f2a, roughness: 0.9, side: THREE.DoubleSide});
const ironMat = new THREE.MeshStandardMaterial({color: 0x2a2724, roughness: 0.6, metalness: 0.5});
const glowMat = new THREE.MeshBasicMaterial({color: 0xffc46b});
{
  const tl = new THREE.TextureLoader();
  const tex = (u, srgb) => { const t = tl.load(u); t.wrapS = t.wrapT = THREE.RepeatWrapping;
    if (srgb) t.encoding = THREE.sRGBEncoding; return t; };
  // The courtyard's own cobblestone, if the node has it. Plain stone if not.
  stoneMat.map = tex('/models/cobblestone_pavement/cobblestone_pavement_diff_1k.jpg', true);
  stoneMat.normalMap = tex('/models/cobblestone_pavement/cobblestone_pavement_nor_gl_1k.jpg', false);
  stoneMat.color.set(0xb8ab98);
  stoneMat.needsUpdate = true;
}

// Ribbon along the curve: offsets a (left edge) and b (right edge) across,
// heights from yA(t) and yB(t). One mesh per ribbon.
const STEPS = 480;
function ribbon(tFrom, tTo, a, b, yA, yB, mat, vRepeat){
  const n = Math.max(2, Math.ceil(STEPS * (tTo - tFrom) / TWO_PI));
  const pos = [], uv = [], idx = [];
  let arc = 0, prev = null;
  for (let i = 0; i <= n; i++){
    const t = tFrom + (tTo - tFrom) * i / n, c = P(t), f = frame(t);
    if (prev) arc += Math.hypot(c.x - prev.x, c.z - prev.z);
    prev = c;
    pos.push(c.x + f.N.x * a, yA(t), c.z + f.N.z * a, c.x + f.N.x * b, yB(t), c.z + f.N.z * b);
    uv.push(0, arc / vRepeat, Math.abs(b - a) / vRepeat, arc / vRepeat);
    if (i < n){ const k = i * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  return new THREE.Mesh(g, mat);
}

// Samples of the low diagonal, to know where the bridge's sides must stay open.
const LOW = [];
for (let i = 0; i <= 240; i++){ const t = Math.PI / 2 + Math.PI * i / 240; LOW.push(P(t)); }
function overLowStreet(x, z, margin){
  for (const q of LOW) if (Math.hypot(q.x - x, q.z - z) < DECK_HW + margin) return true;
  return false;
}

const world = new THREE.Group(); scene.add(world);
// Street floor, the whole loop.
world.add(ribbon(0, TWO_PI, -DECK_HW, DECK_HW, H, H, stoneMat, 3.2));
// Deck underside and the embankment down to the ground, only where the low
// street does not pass beneath (that gap IS the bridge).
{
  const skirtMat = darkStone;
  for (const side of [-1, 1]){
    // Split the high diagonal into runs that are not over the low street.
    let runStart = null;
    const tA = -Math.PI / 2, tB = Math.PI / 2, n = 240;
    for (let i = 0; i <= n; i++){
      const t = tA + (tB - tA) * i / n, c = P(t), f = frame(t);
      const ex = c.x + f.N.x * side * DECK_HW, ez = c.z + f.N.z * side * DECK_HW;
      const open = overLowStreet(ex, ez, 0.6) && H(t) > ARCADE_H - 0.2;
      if (!open && runStart === null) runStart = t;
      if ((open || i === n) && runStart !== null){
        world.add(ribbon(runStart, t, side * DECK_HW, side * DECK_HW, H, () => 0, skirtMat, 3));
        runStart = null;
      }
    }
    // Parapet along the high diagonal where it's off the ground, stopping at
    // the platform's rim.
    let ps = null;
    for (let i = 0; i <= n; i++){
      const t = tA + (tB - tA) * i / n, c = P(t), f = frame(t);
      const inPlaza = Math.hypot(c.x + f.N.x * side * DECK_HW, c.z + f.N.z * side * DECK_HW) < PLAZA_R;
      if (!inPlaza && ps === null) ps = t;
      if ((inPlaza || i === n) && ps !== null){
        world.add(ribbon(ps, t, side * DECK_HW, side * DECK_HW, t2 => H(t2) + (H(t2) > 0.4 ? 1.0 : 0), H, darkStone, 3));
        ps = null;
      }
    }
  }
  // The platform: a stone disc over the crossing, its underside the arcade's
  // ceiling there. A low wall round the rim, open where the street comes in.
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(PLAZA_R, PLAZA_R, 0.55, 56), stoneMat);
  disc.position.y = PLAZA_Y - 0.275; world.add(disc);
  const T0 = frame(0).T, gapHalf = Math.asin(Math.min(1, (DECK_HW + 0.2) / PLAZA_R));
  const aT = Math.atan2(T0.z, T0.x), segs = 44, rim = [];
  for (let i = 0; i < segs; i++){
    const a = (i + 0.5) * TWO_PI / segs;
    if (Math.abs(wrapPI(a - aT)) < gapHalf || Math.abs(wrapPI(a - aT - Math.PI)) < gapHalf) continue;
    rim.push(a);
  }
  const rimWall = new THREE.InstancedMesh(new THREE.BoxGeometry(0.45, 1.0, TWO_PI * PLAZA_R / segs + 0.05), darkStone, rim.length);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), one = new THREE.Vector3(1, 1, 1);
  rim.forEach((a, i) => {
    q.setFromAxisAngle(up, -a);
    m.compose(new THREE.Vector3(Math.cos(a) * (PLAZA_R - 0.2), PLAZA_Y + 0.5, Math.sin(a) * (PLAZA_R - 0.2)), q, one);
    rimWall.setMatrixAt(i, m);
  });
  world.add(rimWall);
  // Piers under the rim, only where the arcade isn't passing.
  const piers = [];
  for (let i = 0; i < 12; i++){
    const a = i * TWO_PI / 12, x = Math.cos(a) * (PLAZA_R - 1.3), z = Math.sin(a) * (PLAZA_R - 1.3);
    if (!overLowStreet(x, z, 0.8)) piers.push([x, z]);
  }
  const pier = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.55, 0.7, PLAZA_Y - 0.55, 10), darkStone, piers.length);
  piers.forEach(([x, z], i) => { m.makeTranslation(x, (PLAZA_Y - 0.55) / 2, z); pier.setMatrixAt(i, m); });
  world.add(pier);
}
// Arcade: roof and columns over the low diagonal.
{
  const t0 = Math.PI / 2 + 0.12, t1 = 3 * Math.PI / 2 - 0.12;
  world.add(ribbon(t0, t1, -DECK_HW - 0.3, DECK_HW + 0.3, () => ARCADE_H, () => ARCADE_H, darkStone, 3));
  const colGeo = new THREE.CylinderGeometry(0.28, 0.34, ARCADE_H, 10);
  const spots = [];
  const n = 90;
  for (let i = 0; i <= n; i++){
    const t = t0 + (t1 - t0) * i / n, c = P(t), f = frame(t);
    for (const side of [-1, 1]) spots.push([c.x + f.N.x * side * (DECK_HW + 0.1), c.z + f.N.z * side * (DECK_HW + 0.1)]);
  }
  const cols = new THREE.InstancedMesh(colGeo, stoneMat, spots.length);
  const lamps = new THREE.InstancedMesh(new THREE.BoxGeometry(0.22, 0.3, 0.22), glowMat, spots.length);
  const m = new THREE.Matrix4();
  spots.forEach(([x, z], i) => {
    m.makeTranslation(x, ARCADE_H / 2, z); cols.setMatrixAt(i, m);
    m.makeTranslation(x, ARCADE_H - 0.55, z); lamps.setMatrixAt(i, m);
  });
  world.add(cols, lamps);
}
// Lantern posts along the open (high) diagonal's parapet.
{
  const spots = [];
  for (let i = 0; i <= 44; i++){
    const t = -Math.PI / 2 + Math.PI * i / 44, c = P(t), f = frame(t);
    for (const side of [-1, 1]){
      const x = c.x + f.N.x * side * DECK_HW, z = c.z + f.N.z * side * DECK_HW;
      if (Math.hypot(x, z) <= PLAZA_R + 0.5) continue;
      // The south tip's outer post landed dead centre in the door lane, in the
      // walker's way (Don's phone, 2026-09-24). It stands against the lane's
      // west wall instead: on your right as you face the door.
      if (Math.abs(x) < SPUR_HW + 0.5 && z > SPUR_Z0 - 0.5) { spots.push([-(SPUR_HW - 0.2), 0, z]); continue; }
      spots.push([x, H(t), z]);
    }
  }
  const posts = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.06, 0.08, 2.6, 6), ironMat, spots.length);
  const lamps = new THREE.InstancedMesh(new THREE.BoxGeometry(0.26, 0.34, 0.26), glowMat, spots.length);
  const m = new THREE.Matrix4();
  spots.forEach(([x, y, z], i) => {
    m.makeTranslation(x, y + 1.3, z); posts.setMatrixAt(i, m);
    m.makeTranslation(x, y + 2.7, z); lamps.setMatrixAt(i, m);
  });
  world.add(posts, lamps);
}
// The landmark at the crossing: two lantern masts on the platform's rim, seen from everywhere.
{
  const f = frame(0);
  for (const side of [-1, 1]){
    const x = f.N.x * side * (PLAZA_R - 0.5), z = f.N.z * side * (PLAZA_R - 0.5);
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.22, MAST_H, 8), ironMat);
    mast.position.set(x, PLAZA_Y + MAST_H / 2, z);
    const cage = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.9, 0.7), glowMat);
    cage.position.set(x, PLAZA_Y + MAST_H + 0.2, z);
    world.add(mast, cage);
  }
}

// ── Stalls ────────────────────────────────────────────────────────────────
// Along both sides, in the strip between the walkable band and the street's
// edge. None on the crossing, none at the door's junction.
function cardTexture(lines, lit){
  const c = document.createElement('canvas'); c.width = 512; c.height = 300;
  const x = c.getContext('2d');
  x.fillStyle = lit ? '#2a2016' : '#1d1a16'; x.fillRect(0, 0, 512, 300);
  x.strokeStyle = lit ? '#ffcf7a' : '#6b5f4d'; x.lineWidth = 6; x.strokeRect(6, 6, 500, 288);
  let y = 58;
  lines.forEach(([txt, size, color]) => {
    x.fillStyle = color; x.font = `${size}px monospace`;
    const words = String(txt).split(' '); let line = '';
    for (const w of words){
      const test = line ? line + ' ' + w : w;
      if (x.measureText(test).width > 460 && line){ x.fillText(line, 26, y); y += size + 8; line = w; }
      else line = test;
    }
    if (line){ x.fillText(line, 26, y); y += size + 14; }
  });
  const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding; return t;
}
const EMPTY_CARD = new THREE.MeshBasicMaterial({map: cardTexture([
  ['This stall is waiting for someone.', 30, '#ffcf7a'],
  ['An introduced key can claim it as a shop (commons_stall.py claim), or post in the Commons for an ad.', 20, '#cfc7b8'],
  ['Read the stall rules first. Anyone can read.', 18, '#8f877a']], true)});

const STALL_SPOTS = [];
{
  const n = 52;
  for (let i = 0; i < n; i++){
    const t = TWO_PI * (i + 0.5) / n;
    if (Math.abs(wrapPI(t)) < 0.34 || Math.abs(wrapPI(t - Math.PI)) < 0.34) continue;  // the crossing
    for (const side of [-1, 1]){
      if (side === LANE_SIDE && Math.abs(wrapPI(t - Math.PI / 2)) < 0.2) continue;       // the door's junction
      STALL_SPOTS.push({t, side});
    }
  }
}
const stallGroup = new THREE.Group(); world.add(stallGroup);
const shopAt = new Map();          // stall spot index -> claimed shop
function buildStalls(posts, shops){
  shops = shops || [];
  stallGroup.children.slice().forEach(c => stallGroup.remove(c));
  const counterGeo = new THREE.BoxGeometry(2.3, 1.0, 1.1);
  const canopyGeo = new THREE.BoxGeometry(2.6, 0.08, 1.7);
  const cardGeo = new THREE.PlaneGeometry(1.5, 0.88);
  const counters = new THREE.InstancedMesh(counterGeo, woodMat, STALL_SPOTS.length);
  const canopies = new THREE.InstancedMesh(canopyGeo, clothMat, STALL_SPOTS.length);
  const empties = [];
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(1, 1, 1), up = new THREE.Vector3(0, 1, 0);
  STALL_SPOTS.forEach((sp, i) => {
    const c = P(sp.t), f = frame(sp.t), y = H(sp.t);
    const off = sp.side * (WALK_HW + 1.25);
    const x = c.x + f.N.x * off, z = c.z + f.N.z * off;
    const face = Math.atan2(-f.N.x * sp.side, -f.N.z * sp.side);    // toward the street
    q.setFromAxisAngle(up, face);
    m.compose(new THREE.Vector3(x, y + 0.5, z), q, s); counters.setMatrixAt(i, m);
    m.compose(new THREE.Vector3(x, y + 2.35, z), q, s); canopies.setMatrixAt(i, m);
    sp.x = x; sp.y = y; sp.z = z; sp.face = face;
  });
  stallGroup.add(counters, canopies);
  // Nearest the door first. A shop keeps its own spot (its slot), so it
  // stands in the same place every visit; ads take the next free ones.
  const order = STALL_SPOTS.map((sp, i) => i).sort((a, b) =>
    Math.abs(wrapPI(STALL_SPOTS[a].t - Math.PI / 2)) - Math.abs(wrapPI(STALL_SPOTS[b].t - Math.PI / 2)));
  const taken = new Set();
  shopAt.clear();
  const doorGeo = new THREE.PlaneGeometry(1.1, 2.1);
  shops.forEach(shop => {
    const idx = order[shop.slot]; if (idx === undefined) return;
    const sp = STALL_SPOTS[idx]; taken.add(idx); shopAt.set(idx, shop);
    const mat = new THREE.MeshBasicMaterial({map: cardTexture([
      [shop.name || '', 30, '#ffcf7a'], [shop.description || '', 20, '#cfc7b8'],
      ['A shop. Walk up and go in.', 18, '#9fc3bf'], ['Run by its owner. Not verified by the Agora.', 16, '#e8756b']], true)});
    const card = new THREE.Mesh(cardGeo, mat);
    card.position.set(sp.x, sp.y + 1.45, sp.z); card.rotation.y = sp.face; card.translateZ(0.6);
    // The doorway: lit, behind the counter, facing the street.
    const door = new THREE.Mesh(doorGeo, glowMat);
    door.position.set(sp.x, sp.y + 1.05, sp.z); door.rotation.y = sp.face; door.translateZ(-0.62);
    stallGroup.add(card, door);
  });
  const adSpots = order.filter(i => !taken.has(i));
  let ads = 0;
  posts.slice(0, 8).forEach((p, k) => {
    const sp = STALL_SPOTS[adSpots[k]]; if (!sp) return; taken.add(adSpots[k]); ads++;
    const says = p.says ? ` · says: ${p.says}` : '', held = p.held ? `held on ${p.held}` : '';
    const mat = new THREE.MeshBasicMaterial({map: cardTexture([
      [p.what || '', 30, '#ffcf7a'], [p.why || '', 20, '#cfc7b8'],
      [p.how_to_ask ? 'Ask: ' + p.how_to_ask : '', 18, '#9fc3bf'], [held + says, 16, '#8f877a']], true)});
    const card = new THREE.Mesh(cardGeo, mat);
    card.position.set(sp.x, sp.y + 1.45, sp.z); card.rotation.y = sp.face;
    card.translateZ(0.6);
    stallGroup.add(card);
  });
  const rest = STALL_SPOTS.filter((sp, i) => !taken.has(i));
  const cards = new THREE.InstancedMesh(cardGeo, EMPTY_CARD, rest.length);
  rest.forEach((sp, i) => {
    q.setFromAxisAngle(up, sp.face);
    const v = new THREE.Vector3(0, 0, 0.6).applyQuaternion(q);
    m.compose(new THREE.Vector3(sp.x + v.x, sp.y + 1.45, sp.z + v.z), q, s); cards.setMatrixAt(i, m);
  });
  stallGroup.add(cards);
  return {shops: shopAt.size, real: ads, waiting: rest.length};
}

// ── Islands: the statue (north) and a dry fountain (south) ──────────────
const loader = new THREE.GLTFLoader();
function standIn(group, label){
  const c = document.createElement('canvas'); c.width = 512; c.height = 96;
  const x = c.getContext('2d'); x.fillStyle = '#1d1a16'; x.fillRect(0, 0, 512, 96);
  x.fillStyle = '#cfc7b8'; x.font = '22px monospace'; x.fillText(label, 16, 56);
  const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding;
  const plaque = new THREE.Mesh(new THREE.PlaneGeometry(3.2, 0.6), new THREE.MeshBasicMaterial({map: t}));
  return plaque;
}
function tryModel(url, onLoad, onMissing){
  loader.load(url, g => onLoad(g.scene), undefined, () => onMissing());
}
// Frosty's statues are shape only: no UVs, no materials. Carved stone.
const statueMat = new THREE.MeshStandardMaterial({color: 0x8f8676, roughness: 0.88});
function carve(obj){ obj.traverse(o => { if (o.isMesh) o.material = statueMat; }); return obj; }
// Scale to a height, stand the base on y=0, centre it; returns the footprint radius.
function fitTo(obj, height){
  const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3());
  const sc = height / Math.max(size.y, 0.001); obj.scale.setScalar(sc);
  const c = box.getCenter(new THREE.Vector3());
  obj.position.set(-c.x * sc, -box.min.y * sc, -c.z * sc);
  return 0.5 * Math.max(size.x, size.z) * sc;
}
// Cthulhu, centre of the platform, facing south toward the way in.
{
  const g = new THREE.Group(); g.position.set(0, PLAZA_Y, 0); world.add(g);
  const up = new THREE.SpotLight(0x9ab0ff, 1.2, 22, 0.55, 0.7, 1.5);
  up.position.set(0, 0.2, 6.5); up.target.position.set(0, 6, 0); g.add(up, up.target);
  tryModel('/models/statues/cthulhu.glb', obj => {
    // The tallest thing in the market (Don, via Frosty): 10 m on a 5.5 m platform.
    STATUE_R = fitTo(carve(obj), CTHULHU_H) + 0.1; g.add(obj);
  }, () => {
    const p = new THREE.Mesh(new THREE.CylinderGeometry(2.2, 2.4, 1.4, 16), darkStone); p.position.y = 0.7; g.add(p);
  });
}
const STATUE_AT = {x: 0, z: -0.62 * L};
{
  const g = new THREE.Group(); g.position.set(STATUE_AT.x, 0, STATUE_AT.z); world.add(g);
  const up = new THREE.SpotLight(0xff9a6a, 1.6, 26, 0.5, 0.6, 1.5);
  up.position.set(0, 0.3, 6); up.target.position.set(0, 4.5, 0); g.add(up, up.target);
  // Nosferatu Rex: Don's original vampire lord, on his own named pedestal.
  tryModel('/models/statues/nosferatu.glb', obj => {
    const base = new THREE.Mesh(new THREE.BoxGeometry(5, 0.4, 5), darkStone); base.position.y = 0.2; g.add(base);
    fitTo(carve(obj), NOSFERATU_H); obj.position.y += 0.4; g.add(obj);
  }, () => {
    const plinth = new THREE.Mesh(new THREE.BoxGeometry(5, 1.6, 5), darkStone); plinth.position.y = 0.8; g.add(plinth);
    // Stand-in: a cloaked figure, one hand raised. Original, not anyone's character.
    const cloak = new THREE.Mesh(new THREE.ConeGeometry(2.1, 7.2, 12, 1, true), darkStone); cloak.position.y = 1.6 + 3.6;
    const shoulders = new THREE.Mesh(new THREE.SphereGeometry(1.15, 12, 8), darkStone); shoulders.position.y = 1.6 + 7.0;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.62, 12, 10), darkStone); head.position.y = 1.6 + 8.35;
    const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.3, 3.4, 8), darkStone);
    arm.position.set(1.25, 1.6 + 8.1, 0); arm.rotation.z = -0.55;
    const collar = new THREE.Mesh(new THREE.ConeGeometry(1.4, 1.6, 10, 1, true), darkStone);
    collar.position.y = 1.6 + 7.9; collar.rotation.x = Math.PI;
    g.add(cloak, shoulders, head, arm, collar);
    const p = standIn(g, 'Stand-in. Nosferatu Rex did not load.');
    p.position.set(0, 1.0, 2.52); g.add(p);
  });
}
{
  const g = new THREE.Group(); g.position.set(0, 0, 0.62 * L); world.add(g);
  const basin = new THREE.Mesh(new THREE.CylinderGeometry(3.4, 3.7, 0.9, 24, 1, true), darkStone); basin.position.y = 0.45;
  const rim = new THREE.Mesh(new THREE.TorusGeometry(3.45, 0.18, 6, 32), stoneMat); rim.rotation.x = Math.PI / 2; rim.position.y = 0.9;
  const spire = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.45, 3.2, 8), stoneMat); spire.position.y = 1.6;
  g.add(basin, rim, spire);
}
// Ground between the loops, so the islands stand on something.
{
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(W * 2.2, L * 2.8), new THREE.MeshStandardMaterial({color: 0x241f1a, roughness: 1}));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.02; scene.add(ground);
}

// ── The one door: lane, wall, door, two torches, the gargoyle above ─────
const FLAMES = [];
{
  const lane = new THREE.Mesh(new THREE.PlaneGeometry(SPUR_HW * 2 + 0.6, SPUR_Z1 - SPUR_Z0 + 0.4), stoneMat);
  lane.rotation.x = -Math.PI / 2; lane.position.set(0, 0.01, (SPUR_Z0 + SPUR_Z1) / 2); world.add(lane);
  for (const side of [-1, 1]){
    // Waist-high: the lane opens onto the haze instead of a corridor.
    const wall = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.1, SPUR_Z1 - SPUR_Z0), darkStone);
    wall.position.set(side * (SPUR_HW + 0.6), 0.55, (SPUR_Z0 + SPUR_Z1) / 2); world.add(wall);
  }
  const g = new THREE.Group(); g.position.set(0, 0, SPUR_Z1 + 0.4); world.add(g);
  const left = new THREE.Mesh(new THREE.BoxGeometry(5, 7, 1.2), darkStone); left.position.set(-4.1, 3.5, 0);
  const right = left.clone(); right.position.x = 4.1;
  const lintel = new THREE.Mesh(new THREE.BoxGeometry(3.2, 2.6, 1.2), darkStone); lintel.position.set(0, 5.7, 0);
  const door = new THREE.Mesh(new THREE.BoxGeometry(3.2, 4.4, 0.25), woodMat); door.position.set(0, 2.2, 0.2);
  const seam = new THREE.Mesh(new THREE.BoxGeometry(0.05, 4.2, 0.02), glowMat); seam.position.set(0, 2.2, 0.05);
  g.add(left, right, lintel, door, seam);
  // Frosty's braziers (stone pillar, iron bowl, a node named "flame" at the
  // bowl). The fire is ours: a flame and a flickering light at that node.
  const BX = SPUR_HW + 0.1, BZ = -1.0, BRAZIER_H = 2.6;
  function fire(parent, x, y, z, seed){
    const outer = new THREE.Mesh(new THREE.ConeGeometry(0.34, 1.0, 10), new THREE.MeshBasicMaterial({color: 0xff7a2a, transparent: true, opacity: 0.85}));
    const inner = new THREE.Mesh(new THREE.ConeGeometry(0.18, 0.62, 8), new THREE.MeshBasicMaterial({color: 0xffd27a}));
    outer.position.set(x, y + 0.5, z); inner.position.set(x, y + 0.36, z);
    const light = new THREE.PointLight(0xff8a3c, 1.3, 12, 2); light.position.set(x, y + 0.9, z - 0.3);
    parent.add(outer, inner, light);
    FLAMES.push({flame: outer, inner, light, seed});
  }
  tryModel('/models/statues/brazier.glb', obj => {
    const pristine = obj.clone(true);      // fitTo sets scale outright; clone before, not after
    for (const side of [-1, 1]){
      const b = side === -1 ? obj : pristine;
      fitTo(b, BRAZIER_H);
      const holder = new THREE.Group(); holder.position.set(side * BX, 0, BZ); holder.add(b); g.add(holder);
      holder.updateMatrixWorld(true);
      const anchor = b.getObjectByName('flame');
      const at = new THREE.Vector3();
      if (anchor) anchor.getWorldPosition(at); else at.set(side * BX, BRAZIER_H, 0).add(g.position).add(new THREE.Vector3(0, 0, BZ));
      g.worldToLocal(at);
      fire(g, at.x, at.y, at.z, side * 1.7);
    }
  }, () => { for (const side of [-1, 1]) {
    // Fallback: the iron torches from before.
    const TX = SPUR_HW - 0.05, TZ = -1.1;
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.16, 2.8, 8), ironMat); post.position.set(side * TX, 1.4, TZ);
    const bowl = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.2, 0.35, 10), ironMat); bowl.position.set(side * TX, 2.95, TZ);
    const flame = new THREE.Mesh(new THREE.ConeGeometry(0.3, 0.9, 8), new THREE.MeshBasicMaterial({color: 0xff9a3c}));
    flame.position.set(side * TX, 3.55, TZ);
    const light = new THREE.PointLight(0xff8a3c, 1.3, 12, 2); light.position.set(side * TX, 3.8, TZ - 0.4);
    g.add(post, bowl, flame, light);
    FLAMES.push({flame, light, seed: side * 1.7});
  } });
  // On top of the wall (7.0 m), not above it: 7.3 left the gargoyle floating (measured).
  const perch = new THREE.Group(); perch.position.set(0, 7.0, -0.3); g.add(perch);
  tryModel('/models/statues/gargoyle.glb', obj => {
    fitTo(carve(obj), 2.2);
    const turn = new THREE.Group(); turn.rotation.y = Math.PI; turn.add(obj); perch.add(turn);   // faces into the market
  }, () => {
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.9, 0.9), darkStone); body.position.y = 0.45;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.36, 10, 8), darkStone); head.position.set(0, 1.05, -0.35);
    const wingG = new THREE.PlaneGeometry(1.2, 0.9);
    const wl = new THREE.Mesh(wingG, darkStone); wl.position.set(-0.85, 0.9, 0.1); wl.rotation.set(0, 0.5, 0.4);
    const wr = wl.clone(); wr.position.x = 0.85; wr.rotation.set(0, -0.5, -0.4);
    perch.add(body, head, wl, wr);
  });
}


// ── A shop's room (commons_stalls.py) ───────────────────────────────────
// One room, built once, the same size and shape for every stall; entering
// a shop fills its sign and tables. It stands far past the fog, so the
// market is out of sight while you're inside and nothing overlaps.
const ROOM = {x: 0, z: 400, hw: 4.2, hd: 3.4, h: 3.2, doorHW: 0.8};
const TABLES = [[-2.0, -1.3], [2.0, -1.3], [-2.0, 1.0], [2.0, 1.0]];   // MAX_ITEMS in commons_stalls.py
const TABLE_HW = 0.7, TABLE_HD = 0.45, TABLE_H = 0.8;
const roomGroup = new THREE.Group(); roomGroup.position.set(ROOM.x, 0, ROOM.z); scene.add(roomGroup);
const roomDynamic = new THREE.Group(); roomGroup.add(roomDynamic);
{
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(ROOM.hw * 2, ROOM.hd * 2), woodMat);
  floor.rotation.x = -Math.PI / 2; roomGroup.add(floor);
  const ceil = floor.clone(); ceil.material = darkStone; ceil.rotation.x = Math.PI / 2; ceil.position.y = ROOM.h; roomGroup.add(ceil);
  const wall = (w, x, z, ry, h = ROOM.h, y = ROOM.h / 2) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), darkStone);
    m.position.set(x, y, z); m.rotation.y = ry; roomGroup.add(m);
  };
  wall(ROOM.hw * 2, 0, -ROOM.hd, 0);
  wall(ROOM.hd * 2, -ROOM.hw, 0, Math.PI / 2);
  wall(ROOM.hd * 2, ROOM.hw, 0, -Math.PI / 2);
  // The front wall, with the one doorway back out to the street.
  const side = ROOM.hw - ROOM.doorHW;
  wall(side, -(ROOM.doorHW + side / 2), ROOM.hd, Math.PI);
  wall(side, ROOM.doorHW + side / 2, ROOM.hd, Math.PI);
  wall(ROOM.doorHW * 2, 0, ROOM.hd, Math.PI, ROOM.h - 2.3, 2.3 + (ROOM.h - 2.3) / 2);
  const out = new THREE.Mesh(new THREE.PlaneGeometry(ROOM.doorHW * 2, 2.3), glowMat);
  out.position.set(0, 1.15, ROOM.hd + 0.02); out.rotation.y = Math.PI; roomGroup.add(out);
  const tableGeo = new THREE.BoxGeometry(TABLE_HW * 2, TABLE_H, TABLE_HD * 2);
  TABLES.forEach(([x, z]) => { const t = new THREE.Mesh(tableGeo, woodMat); t.position.set(x, TABLE_H / 2, z); roomGroup.add(t); });
  const lamp = new THREE.PointLight(0xffc078, 0.6, 12, 2); lamp.position.set(0, ROOM.h - 0.4, 0); roomGroup.add(lamp);
  const bulb = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), glowMat); bulb.position.copy(lamp.position); roomGroup.add(bulb);
}
let inShop = null, returnState = null;
function fillRoom(shop){
  roomDynamic.children.slice().forEach(c => { roomDynamic.remove(c); if (c.material && c.material.map) c.material.map.dispose(); });
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 2.0), new THREE.MeshBasicMaterial({map: cardTexture([
    [shop.name || '', 32, '#ffcf7a'], [shop.description || '', 20, '#cfc7b8'],
    [shop.label || '', 20, '#e8756b'], ['Stall rules and "report this stall" are in the panel, bottom right.', 16, '#8f877a']], true)}));
  sign.position.set(0, 1.9, -ROOM.hd + 0.03); roomDynamic.add(sign);
  const byTable = {}; (shop.items || []).forEach(it => { byTable[it.table] = it; });
  TABLES.forEach(([x, z], i) => {
    const it = byTable[i];
    const lines = it ? [[String(it.kind || '').toUpperCase(), 18, '#9fc3bf'], [it.title || '', 30, '#ffcf7a'], ['Walk up to read it.', 16, '#8f877a']]
                     : [['Nothing on this table yet.', 22, '#6b5f4d']];
    const card = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 0.64), new THREE.MeshBasicMaterial({map: cardTexture(lines, !!it)}));
    card.position.set(x, TABLE_H + 0.36, z); card.rotation.x = -0.35;
    // Face the doorway, so the titles read as you walk in.
    card.rotation.y = 0; roomDynamic.add(card);
  });
}
function enterShop(shop){
  if (inShop || leaving) return;
  inShop = shop; returnState = {...state};
  fillRoom(shop);
  const v = document.getElementById('veil'); v.textContent = shop.name || 'a shop…'; v.classList.remove('off');
  state.mode = 'room'; state.x = 0; state.z = ROOM.hd - 1.0; yaw = 0; pitch = 0.2;
  lanternLight.intensity = 0.5;
  document.getElementById('room-name').textContent = shop.name || '';
  document.getElementById('room-label').textContent = shop.label || '';
  document.getElementById('room-msg').textContent = '';
  document.getElementById('roomhud').hidden = false;
  setTimeout(() => v.classList.add('off'), 350);
}
function leaveShop(){
  if (!inShop) return;
  const v = document.getElementById('veil'); v.textContent = 'back to the street…'; v.classList.remove('off');
  Object.assign(state, returnState); inShop = null; returnState = null;
  lanternLight.intensity = 2.6;
  document.getElementById('roomhud').hidden = true;
  closeSheets();
  setTimeout(() => v.classList.add('off'), 350);
}
function roomStep(mx, mz){
  let x = state.x + mx, z = state.z + mz;
  if (z > ROOM.hd - 0.45 && Math.abs(x) < ROOM.doorHW - 0.2){ leaveShop(); return; }
  x = Math.max(-ROOM.hw + 0.35, Math.min(ROOM.hw - 0.35, x));
  z = Math.max(-ROOM.hd + 0.35, Math.min(ROOM.hd - 0.35, z));
  for (const [tx, tz] of TABLES){                      // around the tables, not through
    const dx = x - tx, dz = z - tz, px = TABLE_HW + 0.3 - Math.abs(dx), pz = TABLE_HD + 0.3 - Math.abs(dz);
    if (px > 0 && pz > 0){ if (px < pz) x += Math.sign(dx || 1) * px; else z += Math.sign(dz || 1) * pz; }
  }
  state.x = x; state.z = z;
}

// ── Walk up and interact: E, or the button (phones) ─────────────────────
let target = null;     // {kind: 'shop', shop} or {kind: 'item', item}
const actBtn = document.getElementById('act');
function findTarget(){
  const p = position();
  if (state.mode === 'room'){
    const byTable = {}; (inShop.items || []).forEach(it => { byTable[it.table] = it; });
    let best = null, bd = 1.7;
    TABLES.forEach(([x, z], i) => {
      const d = Math.hypot(state.x - x, state.z - z);
      if (byTable[i] && d < bd){ bd = d; best = {kind: 'item', item: byTable[i]}; }
    });
    return best;
  }
  let best = null, bd = 2.8;
  shopAt.forEach((shop, idx) => {
    const sp = STALL_SPOTS[idx], d = Math.hypot(p.x - sp.x, p.z - sp.z);
    if (d < bd && Math.abs(p.y - sp.y) < 1.5){ bd = d; best = {kind: 'shop', shop}; }
  });
  return best;
}
function updateTarget(){
  const t = sheetOpen() ? null : findTarget();
  const key = t ? (t.kind === 'shop' ? 's' + t.shop.id : 'i' + t.item.id) : '';
  if (key === (target ? target.key : '')) return;
  target = t ? {...t, key} : null;
  if (!target){ actBtn.hidden = true; return; }
  actBtn.textContent = target.kind === 'shop' ? `Enter ${target.shop.name} (E)` : `Read: ${target.item.title} (E)`;
  actBtn.hidden = false;
}
function interact(){
  if (!target || sheetOpen()) return;
  if (target.kind === 'shop') enterShop(target.shop); else openItem(target.item);
}
actBtn.addEventListener('click', interact);

// ── Reading panel and the "you're leaving" confirmation ─────────────────
const panel = document.getElementById('panel'), leaveBox = document.getElementById('leave');
function sheetOpen(){ return !panel.hidden || !leaveBox.hidden; }
function closeSheets(){ panel.hidden = true; leaveBox.hidden = true; }
// The server renders the instructions safely already (commons_stalls.py).
// This is the second lock: only these tags survive, only https hrefs.
const SAFE_TAGS = new Set(['P', 'BR', 'H3', 'H4', 'H5', 'UL', 'OL', 'LI', 'PRE', 'CODE', 'STRONG', 'EM', 'A']);
function sanitize(root){
  for (const el of Array.from(root.querySelectorAll('*'))){
    if (!SAFE_TAGS.has(el.tagName)){ el.replaceWith(document.createTextNode(el.textContent)); continue; }
    for (const a of Array.from(el.attributes)) if (!(el.tagName === 'A' && a.name === 'href')) el.removeAttribute(a.name);
    if (el.tagName === 'A'){
      if (/^https:\/\//i.test(el.getAttribute('href') || '')) el.className = 'out'; else el.removeAttribute('href');
    }
  }
}
function confirmLeave(url){
  if (!/^https:\/\//i.test(url)) return;
  document.getElementById('leave-text').textContent = inShop ? inShop.leaving_label :
    "You're leaving the Agora. Unverified.";
  document.getElementById('leave-url').textContent = url;
  leaveBox.dataset.url = url; leaveBox.hidden = false;
}
document.getElementById('leave-go').addEventListener('click', () => {
  const url = leaveBox.dataset.url || ''; leaveBox.hidden = true;
  if (/^https:\/\//i.test(url)) window.open(url, '_blank', 'noopener,noreferrer');
});
document.getElementById('leave-stay').addEventListener('click', () => { leaveBox.hidden = true; });
document.getElementById('p-close').addEventListener('click', () => { panel.hidden = true; });
function openItem(item){
  document.getElementById('p-kind').textContent = item.kind || '';
  document.getElementById('p-title').textContent = item.title || '';
  document.getElementById('p-desc').textContent = item.description || '';
  const body = document.getElementById('p-body');
  body.innerHTML = item.instructions_html || '';
  sanitize(body);
  body.querySelectorAll('a').forEach(a => a.addEventListener('click', e => {
    e.preventDefault(); if (a.hasAttribute('href')) confirmLeave(a.href);
  }));
  const links = document.getElementById('p-links'); links.textContent = '';
  (item.links || []).forEach(l => {
    const b = document.createElement('button'); b.type = 'button';
    b.textContent = 'Where to get it: ' + (l.label || '');
    const small = document.createElement('small'); small.textContent = l.url || ''; b.appendChild(small);
    b.addEventListener('click', () => confirmLeave(l.url || ''));
    links.appendChild(b);
  });
  document.getElementById('p-label').textContent = inShop ? inShop.label : '';
  panel.hidden = false;
}
document.getElementById('room-report').addEventListener('click', async () => {
  if (!inShop) return;
  const msg = document.getElementById('room-msg');
  const reason = prompt("What's wrong with this stall? A human steward reads every report. Nothing is judged automatically.");
  if (!reason || !reason.trim()) return;
  try {
    const r = await fetch('/commons/stall/report', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stall_id: inShop.id, reason: reason.trim().slice(0, 280)})});
    msg.textContent = r.ok ? 'Reported. A human steward will read it.'
                           : `The report didn't go through (${r.status}). Nothing was sent.`;
  } catch (_) { msg.textContent = "The report couldn't reach the steward from here. Nothing was sent."; }
});

// Tap the panel's title to show or hide the how-to lines (phones hide them by default).
document.querySelector('#hud > div').addEventListener('click', () => document.getElementById('hud').classList.toggle('open'));

// ── The walker: t along the path, u across it; or in the door's lane ────
// The same pawn as the node view: a spirit carrying a lantern (agora_pawn.py).
const walker = new THREE.Group(); scene.add(walker);
const player = walker;
""" + PAWN_JS + r"""
const state = {mode: 'lane', x: 0, z: SPUR_Z1 - 1.6, t: Math.PI / 2, u: 0};
let yaw = 0, pitch = 0.28;     // camera looks north on arrival (the door is behind you)

function position(){
  if (state.mode === 'room') return {x: ROOM.x + state.x, y: 0, z: ROOM.z + state.z};
  if (state.mode === 'lane') return {x: state.x, y: 0, z: state.z};
  if (state.mode === 'plaza') return {x: state.x, y: PLAZA_Y, z: state.z};
  const c = P(state.t), f = frame(state.t);
  return {x: c.x + f.N.x * state.u, y: H(state.t), z: c.z + f.N.z * state.u};
}
function nearestTipT(x, z){
  // t near the south tip (PI/2 + 2PI k) closest to (x, z); k from the walker's history.
  const base = Math.PI / 2 + TWO_PI * Math.round((state.t - Math.PI / 2) / TWO_PI);
  let best = base, bd = 1e9;
  for (let i = -60; i <= 60; i++){
    const t = base + i * 0.006, c = P(t), d = Math.hypot(c.x - x, c.z - z);
    if (d < bd){ bd = d; best = t; }
  }
  return best;
}
function nearestT(x, z, center){
  const base = center + TWO_PI * Math.round((state.t - center) / TWO_PI);
  let best = base, bd = 1e9;
  for (let i = -90; i <= 90; i++){
    const t = base + i * 0.005, c = P(t), d = Math.hypot(c.x - x, c.z - z);
    if (d < bd){ bd = d; best = t; }
  }
  return best;
}
let leaving = false;
function step(mx, mz){
  if (state.mode === 'room'){ roomStep(mx, mz); return; }
  if (state.mode === 'plaza'){
    let x = state.x + mx, z = state.z + mz;
    const d = Math.hypot(x, z), keep = STATUE_R + 0.35;
    if (d < keep && d > 1e-4){ x *= keep / d; z *= keep / d; }             // around Cthulhu, not through
    const r = Math.hypot(x, z);
    if (r > PLAZA_R - 0.6){
      const N0 = frame(0).N, lat = Math.abs(x * N0.x + z * N0.z);
      if (lat < WALK_HW - 0.35){                                         // out through a gap onto the bridge
        const t = nearestT(x, z, 0), c = P(t), f = frame(t);
        state.mode = 'path'; state.t = t;
        const lim0 = WALK_HW - 0.35;
        state.u = Math.max(-lim0, Math.min(lim0, (x - c.x) * f.N.x + (z - c.z) * f.N.z));
        return;
      }
      x *= (PLAZA_R - 0.6) / r; z *= (PLAZA_R - 0.6) / r;
    }
    state.x = x; state.z = z;
    return;
  }
  if (state.mode === 'lane'){
    state.x = Math.max(-SPUR_HW + 0.35, Math.min(SPUR_HW - 0.35, state.x + mx));
    state.z = Math.min(SPUR_Z1 - 0.2, state.z + mz);
    if (state.z >= SPUR_Z1 - 0.45) leave();
    if (state.z < SPUR_Z0){                                 // into the street
      const t = nearestTipT(state.x, state.z), c = P(t), f = frame(t);
      state.mode = 'path'; state.t = t;
      const lim0 = WALK_HW - 0.35;
      state.u = Math.max(-lim0, Math.min(lim0, (state.x - c.x) * f.N.x + (state.z - c.z) * f.N.z));
    }
    return;
  }
  const f = frame(state.t);
  const along = mx * f.T.x + mz * f.T.z, across = mx * f.N.x + mz * f.N.z;
  state.t += along / f.speed;
  let u = state.u + across;
  const lim = WALK_HW - 0.35;
  // The lane opens off the south side of the street at the south tip.
  const atTip = Math.abs(wrapPI(state.t - Math.PI / 2)) < 0.09;
  const here = position();
  if (u * LANE_SIDE > lim && atTip && Math.abs(here.x) < SPUR_HW - 0.35){
    state.mode = 'lane'; state.x = here.x; state.z = SPUR_Z0 + 0.05; return;
  }
  state.u = Math.max(-lim, Math.min(lim, u));
  // Onto the platform: on the high pass only (the arcade runs underneath).
  if (Math.cos(state.t) > 0.9){
    const h = position();
    if (Math.hypot(h.x, h.z) < PLAZA_R - 1.0){ state.mode = 'plaza'; state.x = h.x; state.z = h.z; }
  }
}
function leave(){
  if (leaving) return; leaving = true;
  const v = document.getElementById('veil'); v.textContent = 'back through the door…'; v.classList.remove('off');
  setTimeout(() => { location.href = '/3d?from=market'; }, 800);
}

// ── Input: keys, drag to look, touch stick ──────────────────────────────
const keys = {};
addEventListener('keydown', e => {
  const k = e.key.toLowerCase();
  if (k === 'escape'){ closeSheets(); return; }
  if (sheetOpen()) return;                      // reading: the walker stands still
  if (k === 'e' && !e.repeat){ interact(); return; }
  keys[k] = true;
});
addEventListener('keyup', e => { keys[e.key.toLowerCase()] = false; });
let drag = null;
renderer.domElement.addEventListener('pointerdown', e => { drag = {x: e.clientX, y: e.clientY}; });
addEventListener('pointerup', () => { drag = null; });
addEventListener('pointermove', e => {
  if (!drag) return;
  yaw -= (e.clientX - drag.x) * 0.006; pitch = Math.max(0.05, Math.min(0.9, pitch + (e.clientY - drag.y) * 0.004));
  drag = {x: e.clientX, y: e.clientY};
});
const stick = document.getElementById('stick'), knob = document.getElementById('knob');
let stickV = {x: 0, y: 0};
if (matchMedia('(pointer: coarse)').matches) stick.style.display = 'block';
stick.addEventListener('pointerdown', e => { stick.setPointerCapture(e.pointerId); moveStick(e); });
stick.addEventListener('pointermove', e => { if (e.buttons || e.pointerType === 'touch') moveStick(e); });
stick.addEventListener('pointerup', () => { stickV = {x: 0, y: 0}; knob.style.left = '40px'; knob.style.top = '40px'; });
function moveStick(e){
  const r = stick.getBoundingClientRect(), dx = e.clientX - r.left - 60, dy = e.clientY - r.top - 60;
  const d = Math.min(1, Math.hypot(dx, dy) / 50), a = Math.atan2(dy, dx);
  stickV = {x: Math.cos(a) * d, y: Math.sin(a) * d};
  knob.style.left = (40 + stickV.x * 40) + 'px'; knob.style.top = (40 + stickV.y * 40) + 'px';
  e.stopPropagation();
}

// ── Compass (same rule as the courtyard: north is -z) ───────────────────
const _cd = document.getElementById('compass-dir'), _cb = document.getElementById('compass');
const _look = new THREE.Vector3(); let _cl = 'N';
function compassLetter(dx, dz){
  const deg = (Math.atan2(dx, -dz) * 180 / Math.PI + 360) % 360;
  return ['N', 'E', 'S', 'W'][Math.round(deg / 90) % 4];
}
function updateCompass(){
  camera.getWorldDirection(_look);
  const d = compassLetter(_look.x, _look.z); if (d === _cl) return;
  _cl = d; _cd.textContent = d; _cb.setAttribute('aria-label', 'facing ' + {N: 'north', E: 'east', S: 'south', W: 'west'}[d]);
}

// ── Loop ────────────────────────────────────────────────────────────────
const clock = new THREE.Clock();
function frameTick(){
  requestAnimationFrame(frameTick);
  const dt = Math.min(clock.getDelta(), 0.1), tt = clock.getElapsedTime();
  let fwd = 0, side = 0;
  if (keys['w'] || keys['arrowup']) fwd += 1;
  if (keys['s'] || keys['arrowdown']) fwd -= 1;
  if (keys['a'] || keys['arrowleft']) side -= 1;
  if (keys['d'] || keys['arrowright']) side += 1;
  fwd -= stickV.y; side += stickV.x;
  const mag = Math.min(1, Math.hypot(fwd, side));
  if (mag > 0.05 && !leaving && !sheetOpen()){
    const sp = 4.5 * dt / Math.max(1, Math.hypot(fwd, side));
    const fx = -Math.sin(yaw), fz = -Math.cos(yaw), rx = Math.cos(yaw), rz = -Math.sin(yaw);
    step((fx * fwd + rx * side) * sp, (fz * fwd + rz * side) * sp);
  }
  const p = position();
  walker.position.set(p.x, p.y, p.z);
  carry.position.set(p.x, p.y + 3.4, p.z);
  carry.intensity = state.mode === 'room' ? 0.15 : 1.1;   // a small room: the lantern alone is plenty
  lanternLight.intensity = state.mode === 'room' ? 0.5 : 2.6;
  const covered = state.mode === 'path' && underArcade(state.t);
  const indoors = state.mode === 'room';
  const dist = indoors ? 2.6 : covered ? 4.0 : 6.0, camH = indoors ? 1.5 : covered ? 1.7 : 2.8;
  camera.position.set(p.x + Math.sin(yaw) * dist * Math.cos(pitch), p.y + camH + Math.sin(pitch) * (indoors || covered ? 0.6 : 2.5),
                      p.z + Math.cos(yaw) * dist * Math.cos(pitch));
  if (indoors){                                   // the camera stays inside the walls
    camera.position.x = Math.max(ROOM.x - ROOM.hw + 0.2, Math.min(ROOM.x + ROOM.hw - 0.2, camera.position.x));
    camera.position.z = Math.max(ROOM.z - ROOM.hd + 0.2, Math.min(ROOM.z + ROOM.hd - 0.2, camera.position.z));
    camera.position.y = Math.min(camera.position.y, ROOM.h - 0.3);
  }
  // In the lane the camera stays inside the door; it arrived staring at the
  // back of the door otherwise (headless screenshot, 2026-09-24).
  if (!indoors && (state.mode === 'lane' || camera.position.z > SPUR_Z0)) {
    camera.position.z = Math.min(camera.position.z, SPUR_Z1 - 0.5);
    camera.position.x = Math.max(-SPUR_HW - 0.2, Math.min(SPUR_HW + 0.2, camera.position.x));
  }
  camera.lookAt(p.x, p.y + 1.3, p.z);
  placeLantern(tt);
  FLAMES.forEach(f => {
    const k = 0.85 + 0.15 * Math.sin(tt * 11 + f.seed) * Math.sin(tt * 7.3 + f.seed * 2);
    f.flame.scale.set(1, k, 1); if (f.inner) f.inner.scale.set(1, 0.9 + 0.2 * k, 1);
    f.light.intensity = 1.1 * k + 0.2;
  });
  updateCompass();
  updateTarget();
  renderer.render(scene, camera);
}
addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });

async function loadAds(){
  const status = document.getElementById('status');
  let posts = [], shops = [], shopsLoaded = false;
  for (const url of ['/public-commons-ads', '/commons/posts']){
    try { const r = await fetch(url); if (!r.ok) continue; const j = await r.json();
      if (j && Array.isArray(j.posts)){ posts = j.posts.filter(p => !p.hidden && p.what); break; } } catch (_) {}
  }
  // Shops: the map's own relay first, then the Commons directly. The rules
  // link follows whichever answered.
  for (const [url, rules] of [['/public-stalls', '/public-stall-rules'], ['/commons/stalls', '/commons/stall-rules']]){
    try { const r = await fetch(url); if (!r.ok) continue; const j = await r.json();
      if (j && Array.isArray(j.stalls) && !j.unavailable){
        shops = j.stalls; shopsLoaded = true;
        document.getElementById('room-rules').href = rules; break;
      } } catch (_) {}
  }
  const n = buildStalls(posts, shops);
  // Labels must be true: if the shops didn't load, say so, not "0 open".
  const shopPart = shopsLoaded ? `${n.shops} ${n.shops === 1 ? 'shop' : 'shops'} open` : "shops couldn't load";
  status.textContent = `${shopPart} · ${n.real} ${n.real === 1 ? 'ad' : 'ads'} · ${n.waiting} waiting for someone`;
  return n;
}
buildStalls([], []);
loadAds();
frameTick();
setTimeout(() => document.getElementById('veil').classList.add('off'), 250);
</script>
</body></html>
"""


def render_market_page() -> str:
    return MARKET_PAGE
