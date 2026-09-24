// Weld a triangle-soup statue: merge bit-identical positions, 16-bit indices,
// smooth area-weighted normals. Same triangles, same shape; ~1/6 the vertices.
import { NodeIO } from '@gltf-transform/core';
const [, , src, dst] = process.argv;
const io = new NodeIO();
const doc = await io.read(src);
const prim = doc.getRoot().listMeshes()[0].listPrimitives()[0];
const pos = prim.getAttribute('POSITION').getArray();
const oldIdx = prim.getIndices().getArray();
const map = new Map(), outPos = [], remap = new Uint32Array(pos.length / 3);
for (let v = 0; v < pos.length / 3; v++) {
  const k = pos[3*v] + ',' + pos[3*v+1] + ',' + pos[3*v+2];
  let n = map.get(k);
  if (n === undefined) { n = outPos.length / 3; map.set(k, n); outPos.push(pos[3*v], pos[3*v+1], pos[3*v+2]); }
  remap[v] = n;
}
const nV = outPos.length / 3;
const idx = [];
for (let i = 0; i < oldIdx.length; i += 3) {
  const a = remap[oldIdx[i]], b = remap[oldIdx[i+1]], c = remap[oldIdx[i+2]];
  if (a !== b && b !== c && a !== c) idx.push(a, b, c);   // drop triangles welded to nothing
}
const nrm = new Float32Array(nV * 3);
for (let i = 0; i < idx.length; i += 3) {
  const [a, b, c] = [idx[i], idx[i+1], idx[i+2]];
  const ux = outPos[3*b]-outPos[3*a], uy = outPos[3*b+1]-outPos[3*a+1], uz = outPos[3*b+2]-outPos[3*a+2];
  const vx = outPos[3*c]-outPos[3*a], vy = outPos[3*c+1]-outPos[3*a+1], vz = outPos[3*c+2]-outPos[3*a+2];
  const nx = uy*vz-uz*vy, ny = uz*vx-ux*vz, nz = ux*vy-uy*vx;          // length = 2*area: area-weighted
  for (const v of [a, b, c]) { nrm[3*v] += nx; nrm[3*v+1] += ny; nrm[3*v+2] += nz; }
}
for (let v = 0; v < nV; v++) {
  const l = Math.hypot(nrm[3*v], nrm[3*v+1], nrm[3*v+2]) || 1;
  nrm[3*v] /= l; nrm[3*v+1] /= l; nrm[3*v+2] /= l;
}
const buf = doc.getRoot().listBuffers()[0];
const old = [prim.getAttribute('POSITION'), prim.getAttribute('NORMAL'), prim.getIndices()];
prim.setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(new Float32Array(outPos)).setBuffer(buf));
prim.setAttribute('NORMAL', doc.createAccessor().setType('VEC3').setArray(nrm).setBuffer(buf));
prim.setIndices(doc.createAccessor().setType('SCALAR').setArray(nV < 65536 ? new Uint16Array(idx) : new Uint32Array(idx)).setBuffer(buf));
old.forEach(a => a && a.dispose());
await io.write(dst, doc);
console.log(JSON.stringify({verts_before: pos.length / 3, verts_after: nV, tris_before: oldIdx.length / 3, tris_after: idx.length / 3}));
