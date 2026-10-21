# Implementation status

Updated 2026-10-21. This tracks the [full roadmap](PLAN.md), without treating a
source-quality result as the finished illustrated Tokyo map.

| Phase | Status | Implemented and remaining work |
| --- | --- | --- |
| 1: interactive source map | Implemented and tested | Tiled source/geometry viewer, minimap, keyboard/touch controls, shared views, credits, errors and matching atlas contract |
| 2: repeatable source atlas | In progress | Global pixel grid, no geometry cuts at chunk edges, corrected ground mapping, real and synthetic 3 x 3 seam checks, exact building-ID/depth/normal/semantic guides, 23-ward metadata inventory; road/water context with original feature IDs; bounded camera/sun source-selection audit, native terrain edge welding and separately attributed mapped vegetation; actual-world completeness and remaining vertical disagreements still need review |
| 3: art direction | Pixel city preferred | Owner preference recorded separately from geographic approval; explorable illustrated preview and independent source view; representative crops and a seamless nine-patch fidelity review remain before bulk artwork |
| 4: production workflow | In progress | Resumable bounded render batches, leases/retries, checksum-gated review desk, sparse image pyramids and incremental rebuilds; bounded paired seam review, immutable repair import and reviewed repair-aware chunk export; content-addressed preview and complete web bundles with standalone checksum verification; map-based review navigation and art-generation accounting remain |
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

## Expanded source and art study

- The fixed world camera now loads 138 building tiles across Shinjuku, Shibuya
  and Nakano, with 16,859 source building identities. The bounded audit finds
  zero missing or mismatched planned tiles, including 16 shadow-only tiles.
- A 132-tile native-z17 orthophoto mosaic covers the visible ground. Its pixels
  match the checked original XYZ tiles in their correct positions.
- Native z15 terrain preserves interior source heights, welds 49 matching tile
  boundaries with at most 0.000619 m correction, and has no open interior edges.
  No uniform geoid adjustment or height smoothing was applied.
- Expanded context includes 2,388 transportation, 107 water and 59 original
  vegetation features. Separately, 182 mapped tree locations and 901 artistic
  woodland crowns are represented with explicit OSM/ODbL provenance.
- The actual adapted vegetation database is published under ODbL, separate from
  the code licence. Park boundaries are never treated as woodland footprints.
- The corrected 360 px, nine-chunk source render passes unchanged seam thresholds
  at 1,024 samples: textured mean/boundary error 0.9291/1.1245; geometry
  0.8916/1.0697. All seven guide artifacts match the full-view reference exactly,
  with zero background pixels and all 138 building/101 context meshes resolved.
- A scoped Blender import adapter preserves scalar uint16 batch IDs that the
  importer previously dropped. An affected tile retains all 470 genuine IDs and
  identical vertex/triangle hashes; source files are not rewritten.
- Three fixed-geometry material studies and two AI-assisted treatments are
  available in [the art comparison](STYLE_PILOT.md). The AI candidates preserve
  recognizable composition but redraw details; neither is an approved map layer.

## What the map does not yet claim

The preview now opens the AI-assisted Pixel city image and retains the expanded
source render as an independent view. Their native dimensions and image
coordinates differ; switching resets framing. The earlier source/geometry atlas
is still available. Source data has mixed LOD, missing textures, photographic
shadows and cropped coverage edges. The new
renderer retains whole loaded geometry, but loaded geometry is not proof that
all real-world features are present. The new bounded audit verifies selected
metadata volumes for this view and directional sun, full terrain support and the
orthophoto tile grid. `source_coverage_verified: false` still records unknown
real-world completeness and source accuracy, rather than contradicting those
narrower passed checks. Diffuse illumination is not covered by the sun-cone audit.

Pixel city is the preferred aesthetic direction, not approval of geographic
fidelity or production artwork. No bulk image generation has run, and no district
or 23-ward completion claim is made. Source-review approvals do
not constitute final artwork approval.

## Next acceptance gates

1. Review the expanded source selection and its explicit metadata/sun-cone
   assumptions. Catalog completeness is not completeness of the real city.
2. Inspect remaining building/road/water terrain separations and mapped vegetation
   placement. Source heights stay unchanged unless a defensible correction exists.
3. Apply the preferred Pixel city direction to representative geography, then
   inspect source fidelity and nine-patch joins. Preserve reference and guide constraints.
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

Latest verification: 284 Python tests, 88 viewer tests, 6 review-model tests and
42 headless browser tests pass (420 total). Preview packaging, standalone bundle
verification and the existing source-atlas verification pass. The prior geographic source comparison remains unchanged; this work does
not reinterpret its results as art approval. No remote CI execution is claimed.

## Interactive preview and immutable app release

- The map fills the screen, with one compact toolbar and a small credits button.
  There are no place cards or full-width bars. Optional overview, sharing, reset
  and detailed controls sit in the information panel.
- Pixel city opens as an independently tiled 1254 by 1254 image. The expanded
  source render remains 1024 by 1024, with no rescaling to imply registration.
- View selection, position and zoom survive shared links. View switches reset
  framing, browser history works, and failed view loads retain a path back.
- AI and geographic-review status remain visible. Credits include all three
  source wards, orthophotography, terrain and the OSM vegetation supplement.
- Each image version is content-addressed by public inputs, notices, tooling and
  exporter code. The active catalog changes only after a full verification.
- Web bundles contain only built browser assets and the selected verified
  preview, with original PNGs, evidence, licences and the actual ODbL database.
  Unrelated public folders, symlinks, modified inputs and corrupt prior releases
  fail validation. Native-resolution tiles preserve original pixels exactly.
- The illustrated image is a bounded preview, not a completed district or a
  seamless generated tile set. No additional model calls are needed to build it.

See [preview releases](PREVIEW_RELEASE.md) for the repeatable artifact workflow.

Cold first-view tile payload in the automated minimal-UI smoke test was
2,909,598 bytes on the desktop viewport and 1,782,468 bytes on the Pixel 7
emulation. Both are below the 3 MiB initial-imagery target. These are local
encoded-image byte counts, not physical-phone or throttled-network benchmarks.
The viewer does not fetch original PNGs during normal navigation.
