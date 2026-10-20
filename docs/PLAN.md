# isometric.tokyo proof-of-concept plan

Start with the existing roughly 800 × 800 metre Shinjuku sample and
2400 × 2400 renders. The first milestone is an interactive source-quality
viewer, not a citywide map or an AI-styled reconstruction.

## Phase 1: preserve the baseline and build the viewer

- Preserve the original textured render and matching geometry-only render.
  Keep the differently framed detail image as a separate close-up.
- Provide drag, pinch and scroll navigation, keyboard controls, reset view,
  mode switching and a shareable URL containing image position and zoom.
- Keep the textured and geometry-only comparison aligned while switching
  modes. Retain the current view so defects can be compared directly.
- Show PLATEAU attribution, dataset edition, known mixed-LOD limitations and
  a clear "No AI styling" label. Offer the original-resolution image.
- Start with a single overview image. Measure image size and mobile loading
  before introducing additional formats or image pyramids. Load comparison
  images on demand and retain the original PNG for quality inspection.
- Record camera orientation, origin, pixel scale, lighting, render settings and
  asset version alongside the existing source manifests.

**Acceptance:** desktop and mobile navigation work; the geometry toggle stays
aligned; a shared view reopens correctly; landmarks and source defects can be
inspected; credits are visible; no console errors or third-party telemetry.

## Phase 2: assess quality, then choose a style

Review roofs, building identity, terrain alignment, floating geometry, texture
gaps and photographic shadows against the geometry-only baseline. Record
defects instead of hiding them. Check close-up views as well as the overview.

Try deterministic materials and lighting before AI styling. Any AI experiment
remains a separate version with a source comparison. Judge changes by whether
they preserve recognizable buildings, streets and spatial relationships.

**Acceptance:** choose a visual direction based on recognizability and continuity,
not appearance alone. Preserve the unstyled reference and document distortions.

## Phase 3: expand one neighboring patch

Use one global projected pixel grid with fixed camera orientation and pixels per
metre. Include neighboring geometry and enough overscan for towers and shadows,
then crop rendered images. Cutting buildings at each image boundary creates
seams. Test one adjacent patch before increasing coverage substantially.

Introduce offline static image pyramids only when measured loading or image-size
limits justify them. Generate lower resolutions from the same master imagery.
This is an isometric image coordinate system, not automatically Web Mercator.

**Acceptance:** adjacent images join without broken buildings, missing geometry,
shadow jumps or scale changes. Dataset and style revisions remain reproducible.

## Initial scope

The proof of concept focuses on exploring and comparing the existing Shinjuku
imagery. Citywide coverage, live 3D, accounts, uploads, runtime AI, geocoding and
accurate image-click-to-world coordinates are outside the initial scope.
