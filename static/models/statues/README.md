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
