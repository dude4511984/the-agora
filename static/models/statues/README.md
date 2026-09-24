# Statues: generated on Frosty, 2026-09-24

Untextured stone shapes. Give them the courtyard's stone material in the map.
About 57-60k faces each, roughly 1MB. The floor slab is trimmed off; each stands on its own pedestal, base at the model's lowest point.

| File | What it is | Placement (Don's call) |
|---|---|---|
| cthulhu.glb | Winged tentacled elder god on a runed pedestal | Centre of the figure-eight market, at the crossing |
| gargoyle.glb | Winged cat gargoyle crouched on a ledge | Above the market's exit door, with two large torches either side |
| nosferatu.glb | "Nosferatu Rex", an original vampire lord with sword, lantern and wide hat, on a named pedestal | Don will place it |

Made from Don's concept art with Hunyuan3D-2.1 (shape stage only), run locally. The art is Don's; the outputs are his.
Regenerate with `~/hunyuan3d/make.sh <image>` then `blender -b -P ~/hunyuan3d/trim.py -- in.glb out.glb` on Frosty.

## brazier.glb: exit-door torches (use two)
A stone pillar with an iron bowl, about 2.1m tall, 1.3k faces. It's built in Blender by `~/hunyuan3d/brazier.py`, so its materials are real: stone, iron, coal. Don't restone it.
**There's an empty node named `flame` at the bowl (y≈1.98).** Attach the fire there: an animated flame (sprite or shader) plus a warm flickering PointLight. The fire is yours to build in the map. One either side of the exit door, under the gargoyle. Scale it up if Don wants them "large".

## Welded for the web (cloud Claude, 2026-09-24)
The glbs as generated were triangle soup: every triangle carried its own three
corners (cthulhu: 57,416 triangles, 171,980 vertices, only 28,799 unique
positions). `weld.mjs` merges bit-identical corners, writes 16-bit indices and
recomputes smooth normals. **Same triangles, same shape; about 1 MB instead
of 4.7–5.0 MB.** Rendered side by side with the originals before swapping.
(`gltf-transform weld` didn't merge them: per-face normals made every corner
unique; this script welds on position alone.)

    node weld.mjs in.glb out.glb      # needs @gltf-transform/core

The brazier is already small (1.3k faces) and isn't welded.

## Where they stand in the market (agora_market.py)
- **cthulhu**: 7 m, centre of a round platform where the bridge crosses the arcade. You walk round him.
- **nosferatu**: 9 m, the north loop's island, uplit.
- **gargoyle**: 2.2 m, on top of the exit door's wall, facing into the market.
- **brazier** ×2: 2.6 m, either side of the exit door; the fire is attached at the `flame` node.
The statues get a carved-stone material (they have no UVs); the brazier keeps its own.
