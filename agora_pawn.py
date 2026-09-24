"""The visitor's pawn: a spirit carrying a lantern.

One copy, used by the node view (/3d) and the market (/market), so a
walker looks the same on both sides of the north gate (Don, 2026-09-24).
The host page must define THREE (with GLTFLoader), `scene`, `camera`
and `player` (an Object3D whose position is the walker's feet) before
this runs, and call placeLantern(t) each frame after moving the player.
"""

PAWN_JS = """// Visitor mark: a lantern that follows, with atmospheric volumetric haze,
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
  ctx.moveTo(235, 250);
  ctx.bezierCurveTo(195, 450, 205, 750, 215, 940);
  ctx.bezierCurveTo(300, 960, 360, 960, 430, 940);
  ctx.bezierCurveTo(420, 750, 405, 480, 385, 340);
  ctx.bezierCurveTo(385, 260, 355, 230, 325, 230);
  ctx.bezierCurveTo(280, 230, 250, 240, 235, 250);
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
  ctx.moveTo(235, 250);
  ctx.bezierCurveTo(195, 450, 205, 750, 215, 940);
  ctx.bezierCurveTo(300, 960, 360, 960, 430, 940);
  ctx.bezierCurveTo(420, 750, 405, 480, 385, 340);
  ctx.bezierCurveTo(385, 260, 355, 230, 325, 230);
  ctx.bezierCurveTo(280, 230, 250, 240, 235, 250);
  ctx.closePath();
  ctx.fillStyle = bodyGrad;
  ctx.fill();
  ctx.restore();

  // Drapery fold glow
  ctx.save();
  ctx.filter = 'blur(8px)';
  ctx.beginPath();
  ctx.moveTo(325, 260);
  ctx.bezierCurveTo(310, 450, 300, 700, 290, 900);
  ctx.moveTo(365, 280);
  ctx.bezierCurveTo(355, 480, 345, 720, 340, 900);
  ctx.lineWidth = 14;
  ctx.strokeStyle = 'rgba(255, 230, 165, 0.16)';
  ctx.stroke();
  ctx.restore();

  // --- Layer 2: Hooded Cowl / Head (Soft Ethereal Mirage) ---
  ctx.save();
  ctx.filter = 'blur(16px)';
  ctx.beginPath();
  ctx.moveTo(325, 55);
  ctx.bezierCurveTo(370, 65, 380, 120, 370, 175);
  ctx.bezierCurveTo(365, 205, 375, 225, 385, 250);
  ctx.bezierCurveTo(335, 265, 295, 265, 260, 255);
  ctx.bezierCurveTo(240, 220, 270, 180, 270, 150);
  ctx.bezierCurveTo(270, 85, 290, 60, 325, 55);
  ctx.closePath();
  ctx.fillStyle = 'rgba(255, 215, 145, 0.35)';
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.filter = 'blur(7px)';
  ctx.beginPath();
  ctx.moveTo(325, 58);
  ctx.bezierCurveTo(368, 68, 376, 120, 368, 175);
  ctx.bezierCurveTo(362, 205, 375, 225, 385, 250);
  ctx.bezierCurveTo(335, 265, 295, 265, 260, 255);
  ctx.bezierCurveTo(240, 220, 270, 180, 270, 150);
  ctx.bezierCurveTo(270, 88, 290, 63, 325, 58);
  ctx.closePath();
  const hoodGrad = ctx.createRadialGradient(325, 135, 15, 325, 135, 75);
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
  ctx.ellipse(322, 145, 26, 40, 0.08, 0, Math.PI * 2);
  ctx.lineWidth = 6;
  ctx.strokeStyle = 'rgba(255, 225, 160, 0.35)';
  ctx.stroke();
  ctx.restore();

  // --- Layer 3: Arm Reaching to Lantern ---
  ctx.save();
  ctx.filter = 'blur(8px)';
  ctx.beginPath();
  ctx.moveTo(335, 225);
  ctx.bezierCurveTo(345, 230, 352, 240, 358, 248);
  ctx.bezierCurveTo(350, 275, 342, 285, 325, 280);
  ctx.bezierCurveTo(320, 265, 325, 240, 335, 225);
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

const spiritPlaneGeom = new THREE.PlaneGeometry(0.93, 1.58);
spiritPlaneGeom.translate(-0.225, -0.410, 0); // Origin at bail apex under hand
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
  // Held-lantern seat: centered on the walker ahead in the look direction,
  // with gentle walking bob and sway. Lateral offset brought to zero so
  // steering by the lantern aligns directly with player.position and
  // door crossing triggers on both nodes.
  const S = lantern.userData.roomScale || 1.0;
  const bob = Math.sin(t * 1.7) * 0.05 * S;
  const sway = Math.sin(t * 1.1) * 0.04 * S;
  let fx = player.position.x - camera.position.x;
  let fz = player.position.z - camera.position.z;
  const fl = Math.hypot(fx, fz) || 1;
  fx /= fl; fz /= fl;
  const rx = fz, rz = -fx;
  lantern.position.set(
    player.position.x + fx * (0.35 * S) + rx * sway,
    player.position.y + (0.76 * S) + bob,
    player.position.z + fz * (0.35 * S) + rz * sway
  );
  lantern.rotation.y = Math.atan2(fx, fz);
  updateLanternAtmosphere(t, bob, rx, rz);
}
"""
