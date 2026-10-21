# Implementation status

Updated 2026-10-21. This tracks the [full roadmap](PLAN.md), without treating a
source-quality result as the finished illustrated Tokyo map.

| Phase | Status | Implemented and remaining work |
| --- | --- | --- |
| 1: interactive source map | Implemented and tested | Tiled source/geometry viewer, minimap, keyboard/touch controls, shared views, credits, errors and matching atlas contract |
| 2: repeatable source atlas | In progress | Global pixel grid, no geometry cuts at chunk edges, corrected ground mapping, real and synthetic 3 x 3 seam checks, exact building-ID/depth/normal/semantic guides, 23-ward metadata inventory; road/water context with original feature IDs; buffered source completeness and terrain reconciliation remain |
| 3: art direction | Not approved | Three deterministic material candidates and a matching raw reference are rendered; broader representative crops, fidelity review and owner approval are required before bulk artwork |
| 4: production workflow | In progress | Resumable bounded render batches, leases/retries, checksum-gated review desk, sparse image pyramids and incremental rebuilds; bounded paired seam review, immutable repair import and reviewed repair-aware chunk export; map-based review navigation and art-generation accounting remain |
| 5: illustrated Shinjuku | Pending | Requires approved art direction, complete local source treatment and district-wide review |
| 6: connected Tokyo expansion | Pending | Requires district measurements and accepted coverage batches |
| 7: completed product | Pending | Requires the agreed coverage and final acceptance audit |

## Verified results

- The viewer uses actual multiresolution WebP tiles, not a full-image fallback.
  Its baseline has 294 image tiles across two aligned layers.
- Automated desktop/mobile tests cover navigation, keyboard and touch gestures,
  URL restoration, aligned layer changes, failure recovery, credits and clean
  component remounts. Screenshots wait for all visible tiles, not the first tile.
- Shaded Shinjuku renders at 720 x 720 pixels pass the same whole-image and
  boundary-band thresholds as the synthetic fixture. Exact results and sampling
  settings are recorded in [RENDERING.md](RENDERING.md).
- All seven categorical/numeric guide artifacts match the monolithic reference
  exactly across nine real-source chunks. The registry preserves 1,076 source
  building records from 26 meshes; it does not infer identities from colors.
- Sparse pyramid tests cover a million-pixel-square extent without allocating a
  full image. Incremental results must match a fresh build byte for byte.
- Two actual bounded Blender jobs recovered from failed attempts, completed and
  entered `needs_review`. Repeating the batch rendered zero jobs. Their combined
  index exported successfully through the chunk-pyramid pipeline.
- A 1024 x 1024 context preview completed through the batch worker. Repeating the
  unchanged job skipped rendering. It remains a source study awaiting review.
- Manual repair fixtures exercise exact bounded splicing, transitive approvals,
  source hash checks, preserved guides and incremental retile equivalence. No real
  source or art repair candidates were approved. AI-assisted edits remain labeled
  as such through the resulting viewer manifest.
- The actual review API passed authenticated image/checksum and unauthenticated
  access checks. Automated approval/rejection tests use disposable fixtures,
  not approvals of the real source outputs.
- The 23-ward source inventory confirms published building inputs for every ward,
  but marks none production-ready merely because a catalogue entry exists.
- Three original deterministic material candidates were rendered with an identical
  camera and unchanged geometry. They are structural studies, not approved final
  artwork; see [STYLE_PILOT.md](STYLE_PILOT.md).
- Road/water context was downloaded and rendered with 500 transportation and
  107 water feature IDs. A real nine-chunk comparison passed unchanged shaded
  seam thresholds, with exact categorical/numeric guides. Original-elevation
  diagnostics record varying terrain separations without shifting the source.
  Published Shinjuku vegetation items have no intersecting tiles in this bbox at
  LOD1 or LOD3. Missing trees are not replaced with invented source claims.

## What the map does not yet claim

The displayed atlas is still the attributed raw source study. It has mixed LOD,
missing textures, photographic shadows and cropped coverage edges. The new
renderer retains whole loaded geometry, but loaded geometry is not proof that
all offscreen shadows and occluders are present. Every new render continues to
record `source_coverage_verified: false` until an influence-area audit passes.

No final art direction has been approved, no paid bulk image generation has run,
and no district or 23-ward completion claim is made. Source-review approvals do
not constitute final artwork approval.

## Next acceptance gates

1. Review the measured road/water terrain separations. Resolve the vegetation
   gap using compatible data or an explicitly documented artistic treatment.
2. Prove that the source influence envelope includes every required occluder and
   shadow caster. Reconcile terrain edges and inspect building-ground offsets.
3. Compare original art-direction candidates on representative geography, then
   obtain style approval. Preserve raw-reference comparison and guide constraints.
4. Exercise repair review and regeneration on real artwork after source/style
   approval, then add map-based region navigation and art-generation accounting.
5. Complete an illustrated Shinjuku district, measure accepted-output cost and
   review effort, then expand connected coverage under the roadmap's gates.

## Checks

`just check` runs all Python, TypeScript and review-model unit tests and lints.
`just viewer-e2e` runs headless map/review tests on desktop and mobile viewports.
`just seam-check` renders and verifies a fresh Blender fixture, including guides.
`just verify-atlas` checks the generated baseline release.
The inactive `ci/checks.yml` workflow template runs the
non-Blender suites and browser tests once installed as a GitHub workflow. The
current publishing credential cannot add workflow files, so no remote CI result
is claimed. YAML changes are validated with `yq`.
