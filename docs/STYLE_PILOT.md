# Deterministic material pilot

These are **unapproved structural studies**, not the finished Tokyo artwork.
They compare three original, restrained palettes on identical PLATEAU geometry
and an identical isometric camera. No external image-generation service, paid
model call or invented geometry is involved.

The palette candidates are defined in `configs/style-presets.json`:

- **Paper and slate:** warm neutral ground, slate facades and pale roofs.
- **Cool mineral:** cool neutral ground, blue-gray facades and light roofs.
- **Warm clay:** warm gray ground, muted clay facades and cream roofs.

A shading-normal threshold chooses roof versus facade color. This is an artistic
shader rule, not a claim that the source contains semantic roof labels. The
original textured source reference is rendered before materials are substituted.
Neither the input files nor saved source-study previews are changed.

## Reproduce and inspect

The `scripts/render_style_pilot.py` Blender entry point accepts a `render`
command with the same configuration/building/ground/terrain inputs as the source
renderer. It defaults to a 720-pixel longest side and 64 samples. The separate
`finalize` command runs in ordinary Python, removes renderer metadata, verifies
nonblank images and matching dimensions, and creates a labeled comparison sheet.
Use the project's style-pilot task rather than maintaining a separate command.

Output files:

- `source-reference.png`: the matching unstyled reference.
- `paper.png`, `mineral.png`, `clay.png`: candidate material studies.
- `style-comparison.png`: labeled comparison sheet.
- `style-pilot.json`: source hashes, camera, resolution, settings, palettes,
  attribution, output hashes and limitations.

The manifest always retains `art_approval: pending-owner-review`. Finalization
can verify assets but cannot approve an art direction. The configured status is
also required to remain `unapproved-structural-studies`.

## What this pilot can and cannot decide

It can compare massing, silhouette readability, roof/facade contrast and broad
palette. Geometry and camera remain fixed, so visual differences are not caused
by rearranged buildings or reframing.

It cannot establish the final street-level or district-wide illustration style.
Ground is intentionally neutral, with no reconstructed street markings, railway
tracks, trees, park planting or water treatment. Replacing photographic ground
with a uniform material removes surface information, it does not solve that
information gap. The missing representative ordinary-street, station, park and
water crops remain part of the Phase 3 gate.

The next decision is to inspect the material studies, select or revise a palette,
then apply it to an expanded source-complete representative crop set. No candidate
should be promoted as approved artwork, and none authorizes bulk AI generation.
