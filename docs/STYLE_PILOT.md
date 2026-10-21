# Shinjuku art-direction studies

These studies compare candidate treatments, not an approved final map. Source
selection, terrain diagnostics and vegetation provenance remain separate from
art approval. The interactive baseline still displays the original source atlas.

## Geometry-preserving studies

`configs/shinjuku-art-presets.json` defines three original palettes:

- **A. Atlas ink:** ivory streets, blue-gray towers, jade canopy and dark glazing.
- **B. Garden city:** pale mineral facades, warm roofs and softer green canopy.
- **C. Terracotta blocks:** rose facades, cream roofs, teal glazing and olive canopy.

All candidates use identical building geometry, context and camera. The source
reference is rendered first, before replacing materials. Procedural window bands
are artistic facade marks, not recovered source windows. A shading-normal rule
separates roof and facade colors; it is not a source semantic classification.
Terrain heights are not changed to improve the style.

The mapped vegetation layer uses explicit tree points and wooded polygons.
Canopy placement within woodland, crown shape and tree dimensions are artistic.
Mapped trees, generated woodland instances and original PLATEAU vegetation retain
different provenance. They must not be described as one surveyed tree inventory.
See [vegetation provenance](VEGETATION.md) and [source terms](../THIRD_PARTY_NOTICES.md).

Roads and water retain their supplied geometry and heights. Grass polygons are
retained in the derived vegetation database but are not indiscriminately filled
into the rendered scene. No park-wide tree carpet is invented to hide missing
information. Palette replacement also removes photographed ground detail, so
paths, paving, gardens and rails still need deliberate treatment.

## Reproduce

Use the checked source inputs described in [coverage](COVERAGE.md) and
[rendering](RENDERING.md), then run:

```sh
just art-pilot configs/shinjuku.json \
  data/coverage-buildings/manifest.json data/ground-study-v2/manifest.json \
  data/terrain-study-v2/mesh.json data/context-study-v2/manifest.json \
  data/vegetation/manifest.json
```

This bounded task renders a matching reference and three candidates at a
1024-pixel longest side. The finalizer strips renderer metadata without changing
image pixels, verifies dimensions and nonblank content, computes output hashes
and creates an attributed comparison sheet. It never grants artwork approval.
The original, simpler palette experiment remains available through `just style-pilot`.

The output manifest records input hashes, camera, source counts, render settings,
palette parameters, attribution and representation limits. Its status remains
`complete-unapproved`, with `art_approval: pending-owner-review`.

## Before choosing a direction

Compare the same places across candidates:

1. Tokyo Metropolitan Government towers: silhouettes and stepped roof levels.
2. Shinjuku Central Park: mapped woodland, paths and open ground.
3. Small surrounding buildings: readable separation without false window detail.
4. Major roads and crossings: continuity, contrast and no vegetation over exclusions.
5. Building-ground contact: retain any source disagreement in the audit rather
   than hiding it behind invented heights or artwork.

A palette decision does not approve geographic coverage. A convincing AI
illustration also does not prove footprint or landmark fidelity. Any generated
style samples must remain labeled as AI-assisted, with their original reference,
prompt, output hash and known distortions preserved. No sample authorizes bulk
image generation or replacing the raw comparison layer.

The next art gate is owner selection or revision, followed by fidelity review on
ordinary streets, station infrastructure, park and water crops. Seam-aware
production and repair review follow only after that gate.

## Two AI-assisted image studies

The published [Pixel city](../previews/shinjuku-art/ai-pixel-city.png) and
[Printed atlas](../previews/shinjuku-art/ai-printed-atlas.png) studies were generated
from the same Atlas ink render in two bounded image-generation calls. The tool
returned 1254 by 1254 pixel images. The original reference is 1024 by 1024.
They are not aligned atlas tiles and have not been substituted into the viewer.

- **D. Pixel city** uses stronger blue glazing, brick accents and a simulation-game
  treatment. It is closer to the playful map direction, but adds unsupported
  green roofs, street trees and paving detail.
- **E. Printed atlas** uses finer contours and more restrained color. Its massing
  looks closer to the source, but small roofs, facade grids and surface details
  are still redrawn.

Both retain the recognizable park/tower composition. Both reinterpret tree
crowns, park lawns and paths, road widths, roof edges and facade details. A visual
inspection did not identify a district-scale relocation, but that is not a
registered footprint, height or occlusion audit. They are candidates for choosing
an aesthetic, not evidence that unconstrained image generation is ready for
production cartography.

[ai-studies.json](../previews/shinjuku-art/ai-studies.json) records exact prompts,
reference RGB hash, output hashes, dimensions, source notices and observed
limits. Model version and actual cost were not exposed by the tool and are not
invented. All candidates remain unapproved. The public source and material
comparison is retained beside them for independent inspection.
