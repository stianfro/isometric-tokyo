# Roadmap to an illustrated Tokyo map

## End goal

Build **isometric.tokyo** into a continuous, explorable, illustrated map of Tokyo,
in the same product category as [isometric.nyc](https://isometric.nyc/): a fixed
isometric viewpoint, recognizable buildings, a consistent art direction, and
smooth navigation from city overview to individual blocks.

The current Shinjuku source study is the starting material, not the finished
product. Neither a larger render nor a page displaying the existing PNG is enough.
The solution needs three connected parts:

1. **Geographic renderer:** reproducible, pixel-aligned imagery from open data.
2. **Art production and review:** consistent artwork, seam correction, provenance
   and a manageable process for approving thousands of patches.
3. **Public viewer:** a small application that loads only the image tiles needed
   for the current view.

**Working coverage target:** Tokyo's 23 special wards, delivered as connected
coverage releases. This does not mean all of Tokyo prefecture or its islands.
Phase 2 must check source coverage and quality across that target. Gaps must be
resolved with compatible data, documented lower-detail treatment, or an explicit
scope decision, not silently filled with invented geography.

### What counts as finished

- The agreed coverage polygon is complete, with no unexplained holes or failed
  tiles. Coverage boundaries and source dates are visible.
- Buildings, roads, railways, parks and water form one coherent illustrated map.
  Art can simplify detail but must preserve recognizable geography.
- Desktop and touch users can pan, zoom, use a minimap, reset, jump to curated
  places, and share a link that restores the same view.
- Artwork joins without cut buildings, repeated structures, obvious seams or
  changes in scale and lighting. Critical landmarks pass manual comparison.
- Credits, source limitations and the distinction between source geometry and
  artistic treatment are available in the viewer.
- The pipeline can resume, repair a region and produce a verified new release
  without rebuilding everything or losing approved artwork.

Live 3D, free camera rotation, accounts, user uploads and runtime AI are not
required. Seasonal effects, animated water and exhaustive address search are
later additions, not substitutes for completing the map.

## Reference findings and technical direction

The NYC source uses React, TypeScript and OpenSeadragon for the viewer, with an
offline rendering and image-generation pipeline. Its image export uses a
multiresolution WebP pyramid and DZI. The author's account describes neighbor-
conditioned generation and substantial manual correction, especially for trees,
water and joins. This supports treating review tools as part of the solution,
not a cleanup task at the end. See the [reference sources](#reference-sources).

Use the same broad architecture, with original Tokyo artwork and our own data:

```text
source inventory + area configuration
                  |
       cached geographic inputs
                  |
    aligned source render + guide layers
                  |
   style patches + review + local repairs
                  |
     approved atlas + image pyramids
                  |
        interactive browser viewer
```

Proposed defaults, to validate in the first milestones:

- Keep Python and Blender for geographic preparation and offline rendering.
- Use a versioned JSON atlas configuration and manifests. Keep render chunks,
  style patches and browser image tiles separate; their optimal sizes differ.
- Use React, TypeScript and OpenSeadragon for the viewer. The first bounded
  pyramid exporter uses Pillow with sparse windowed reduction and measured
  allocation limits. Reconsider libvips after profiling district throughput. Use
  image coordinates, not a Web Mercator map forced onto an isometric image.
- Keep the atlas origin, camera basis and pixel scale fixed. Recenter individual
  Blender jobs for numerical precision without changing their global placement.
- Keep raw reference imagery separate from approved artwork. Use deterministic
  materials first; add AI only if it meets the same geographic-fidelity checks.
- Begin batch tracking with SQLite and bounded local worker processes. A larger
  processing system is justified only by measured workload, not by the city name.

The NYC code is MIT-licensed. Preserve its copyright and licence notice if code
is copied. That licence does not establish permission to reuse every external
asset or model involved in the reference. The live site also has controls absent
from the inspected public app source, so it is not an exact ready-made template.

## Starting point of this roadmap

This section describes the original baseline. See [implementation status](PROGRESS.md)
for completed work and remaining gates.

**Implemented:** a roughly 800 × 800 metre Shinjuku sample; bounded PLATEAU
building, orthophoto and terrain downloads; textured and geometry-only renders;
source checksums; saved-scene checks; preview metadata sanitation; and 35 offline
unit tests. The original input assets and raw reference outputs remain the
baseline for comparisons.

**Not implemented:** a browser viewer, global atlas configuration, seamless chunk
rendering, image pyramids, style generation, a review interface, resumable batch
jobs or district-scale coverage.

Current limitations that must be fixed rather than multiplied:

- Sample bounds and camera framing are hardcoded in several places.
- Geometry is cropped before rendering, which can cut towers and remove shadows
  or occluders that should cross image boundaries.
- Ground imagery needs correct geographic reprojection instead of interpolating
  Web Mercator imagery across local-coordinate corners.
- Source loading handles only a subset of 3D Tiles layouts. Building identities
  and per-feature quality information do not yet reach the render output.
- Trees are mostly ground photography; mixed LOD, missing textures, terrain
  alignment and photographic shadows need explicit treatment.
- Existing image checks can detect blank output, but not plausible-looking
  imagery in the wrong position.

## Phases and release gates

Every phase ends with a demonstrable result and recorded acceptance evidence.
Implementation is tracked separately in [PROGRESS.md](PROGRESS.md); a working
component does not by itself complete its entire phase. Timing and total cost depend on
measurements from the district pilot; no citywide generation budget is assumed.

| Phase | Demonstrable result | Depends on |
| --- | --- | --- |
| 1 | Interactive Shinjuku source map | Existing sample |
| 2 | Seamless, repeatable source atlas | Phase 1 atlas contract |
| 3 | Approved Tokyo art direction and seamless styled patch | Phase 2 guide renders |
| 4 | Resumable production and review workflow | Phase 2; style integration after Phase 3 |
| 5 | Complete illustrated Shinjuku release | Phases 3 and 4 |
| 6 | Connected central Tokyo, then 23-ward coverage | Phase 5 measurements and approval |
| 7 | Completed coverage and final product-quality release | Phase 6 |

Viewer work and renderer work can proceed in parallel once the atlas contract is
fixed. Polish, accessibility and source-quality fixes continue throughout; they
are not deferred until Phase 7.

### Phase 1: establish the atlas contract and ship a real viewer

**Work**

- Define the coordinate frame, vertical convention, camera orientation,
  pixels-per-metre, image axes, coverage polygon and release identifier.
  Record data editions, hashes and renderer settings in manifests.
- Implement pure world-to-atlas-pixel math. An inverse must specify a ground
  surface or known depth; a roof pixel alone does not uniquely identify a
  geographic position.
- Generate a real multiresolution pyramid from the existing matching textured
  and geometry-only images. Preserve the original PNGs for inspection. The
  differently framed detail render remains a separate close-up.
- Build full-window drag, wheel/pinch zoom, keyboard controls, minimap, reset,
  textured/geometry switching and shareable URL state. Keep comparison layers
  aligned and retain the viewport while switching.
- Add loading, missing-tile and outside-coverage states, credits and a clear
  unstyled-source label. Keep navigation usable on narrow screens.

**Gate**

A user can explore Shinjuku in a browser, compare the two layers and reopen a
shared view. Tests cover projection round trips at known elevations, URL state,
layer alignment, keyboard interaction and tile failures. No view requires
loading the whole atlas. This is the first usable map, not the final art result.

### Phase 2: make source rendering seamless and repeatable

**Work**

- Replace sample-only constants with validated area and render configurations.
  Separate source preparation, chunk planning and scene rendering.
- Compute cameras from integer atlas-pixel bounds. Include all geometry that can
  affect each image rectangle, render with overscan, then crop pixels. The buffer
  must account for tower height, occlusion and shadows, not just ground bounds.
- Correct orthophoto mapping; reconcile terrain edges and building elevations.
  Fix lighting, exposure and sampling settings across jobs.
- Preserve feature identifiers and produce textured, geometry-only, depth,
  normal, silhouette, semantic and building-ID guide layers. Flag gaps instead
  of treating absent data as empty city.
- Inventory the intended 23-ward coverage: source edition, acquisition dates
  where available, format, building coverage, LOD, textures, terrain and terms.
  Add only the format adapters needed by that inventory.
- Test roads, railway corridors, vegetation and water explicitly. Where current
  imagery is insufficient, select compatible additional data before expansion.

**Gate**

A contiguous 3 × 3 render-chunk patch has no cut or duplicated buildings, missing
occluders, terrain cracks or visible joins. Include a tall building and shadow
crossing a boundary and a four-way corner. Compare chunked renders against a
single larger reference render. The same world point lands at the same atlas
pixel in every job. A coverage and source-quality report explains the remaining
Tokyo-wide gaps.

### Phase 3: approve an original Tokyo art direction

**Work**

- Compare a small set of art directions on identical crops: high-rise Shinjuku,
  ordinary low-rise streets, a station/rail corridor, a park and water.
- Start with deterministic palette, materials, lighting and vegetation treatment.
  If AI improves the result, use the source render and guide layers as constraints,
  with a fixed style reference and recorded generation settings.
- Define the fidelity rubric before choosing the prettiest image: landmark
  silhouette, relative height, building count in critical areas, street and rail
  continuity, park/water boundaries, invented details and visual consistency.
- Style overlapping patches with accepted neighbor context. Preserve approved
  pixels and repair boundary strips deliberately. Independent tile generation
  followed by generic blending is not the seam strategy.
- Keep raw/styled comparisons and permit manual correction. Record model/version,
  prompt, seed where supported, input hashes, masks, retries and actual cost.
  Recorded settings improve traceability but do not guarantee identical output
  from an external image service.

**Gate**

The owner approves one style on the representative crop set and a contiguous
3 × 3 styled-patch test, including horizontal, vertical and four-way joins.
Every critical landmark passes comparison. Obvious invented buildings, broken
streets or altered station layouts block approval. If AI cannot pass, continue
with a controlled illustrated render rather than relaxing geographic fidelity.
No bulk image generation happens before this gate and an explicit spend limit.

### Phase 4: build the production and review workflow

**Work**

- Add a job ledger with pending, running, failed, needs-review and approved states.
  Job identity includes configuration, source hashes and relevant tool versions.
- Make downloads atomic, checksummed and retryable. Bound worker concurrency,
  classify failures and enforce retry and generation-cost limits.
- Track dependencies so changed sources or settings invalidate affected chunks,
  style patches and pyramid ancestors. Keep previous approved versions intact.
- Build a lightweight review interface: select region, compare raw/artwork,
  inspect guides and joins, flag defects, approve, retry, and export/import manual
  repairs. Approval of a replacement invalidates stale neighboring seam checks.
- Assemble imagery and build pyramids in bounded windows or chunks, including
  sparse coverage. Never require the full native-resolution city image in memory.
- Export versioned pyramids and a manifest with extents, zoom levels, tile sizes,
  coverage, layers, source attribution and approved release identity.
- Check completeness, image dimensions, tile borders, metadata and mixed-version
  mistakes. Failed tiles must never masquerade as intentionally empty coverage.

**Gate**

Interrupt and resume a multi-chunk run without repeating approved generation.
Recover from a corrupted download and failed render. A one-region edit rebuilds
only its dependency closure. The review interface can reject and repair a patch,
then produce a complete, internally consistent release with no unreviewed art.
A large synthetic atlas with a small changed region must demonstrate bounded
peak memory and regeneration of only the affected pyramid ancestors.

### Phase 5: finish one illustrated Shinjuku district

**Work**

- Define and complete a connected district polygon larger than the original
  sample, containing towers, ordinary blocks, rail infrastructure and green space.
  Choose the exact boundary from the source inventory, not an arbitrary tile count.
- Run the full source-to-art-to-pyramid workflow. Review every artwork patch at
  native resolution and all joins. Fix missing geometry and repeated defects at
  their source before approving the district.
- Add curated landmark jumps with Japanese and English names where verified.
  Place markers using known coordinates and elevation, with explicit placement
  for roofs or landmarks, rather than an assumed universal screen inverse.
- Add browser regression tests for desktop and touch navigation, direct shared
  links, credits, layers, missing assets and long pan/zoom sessions.
- Measure download volume, render duration, peak memory, accepted area, generation
  retries, review/repair hours, output bytes and total cost.

**Gate**

This is a complete small illustrated map, not a gallery of disconnected demos.
Every patch is approved, the district is navigable end to end, and all known
blocking defects are resolved. Record cost and labor per accepted square kilometre
and per megapixel, including rejected attempts. Those measurements determine the
next coverage batch and budget.

Initial performance targets, not current measurements: first usable map within
5 seconds on a documented 10 Mbps/100 ms latency test, at most 3 MiB of initial
map imagery, and no full-atlas decode during navigation. Benchmark a named
mid-range phone and desktop, record frame times and memory, and agree any target
changes rather than claiming unmeasured performance.

### Phase 6: expand connected coverage to Tokyo scale

**Work**

- Expand in connected batches: Shinjuku and neighbors, a Shibuya/central-city
  connection, broader central Tokyo, then the remaining special wards. The exact
  order follows source readiness and the approved coverage polygon.
- Before each batch, confirm source terms, quality, predicted render volume,
  generation allowance and review capacity. Use separate estimates for dense
  towers, low-rise neighborhoods, parks and waterfronts.
- Reuse the same projection and approved art direction. Revisit diverse test
  crops before any global style change; do not mix incompatible styles silently.
- Inspect joins across source-dataset and release boundaries, not only within a
  batch. Repair height discontinuities, duplicate buildings and date mismatches.
- Publish coverage/version notes and known non-blocking limitations. Incomplete
  regions remain clearly outside the available map until accepted.

**Gate for each batch**

Coverage is contiguous with the prior release, all required tiles exist, art and
boundary checks pass, and measured cost is within the approved allowance.
Unexpected failure or rejection rates pause expansion for a pipeline fix.
Continue until the agreed 23-ward target is complete, or obtain an explicit
scope revision backed by the documented data constraints.

### Phase 7: complete the product, not just the coverage

- Audit the entire agreed coverage against the manifest and source inventory.
  Resolve all blocking defects, including mismatched district joins.
- Finish place navigation, readable controls, touch behavior, keyboard focus,
  reduced-motion behavior, empty/error states and credit presentation.
- Test deep links at different viewport sizes, every zoom level, extended
  navigation, asset-version changes and representative lower-memory devices.
- Verify that a clean checkout can run the documented source, render, review and
  tile workflow for a bounded area, and that the full atlas can be repaired through
  the same workflow.
- Publish the completed coverage definition, source dates, artistic limitations
  and a repeatable procedure for future data updates.

**Final gate:** the end-goal checklist at the top of this document passes. Extra
visual effects can follow, but a single district, an unstyled atlas or an
unfinished collection of patches must not be labelled the completed Tokyo map.

## Next implementation queue

The source pipeline and first interactive preview are implemented. The current
queue follows the preferred Pixel city aesthetic without treating one attractive
image as a completed map:

| Order | Work | Acceptance evidence |
| --- | --- | --- |
| 3A | Exercise Pixel city on high-rise, ordinary blocks, station, park and water crops | Source/guide comparisons and recorded geographic defects, not style alone |
| 3B | Produce a contiguous nine-patch illustration pilot with controlled overlap | Horizontal, vertical and four-way joins reviewed at native size; no broken roads or invented landmarks |
| 4A | Add transactional generation reservations and measured production accounting | Finite call/spend ceilings, retry costs, interruption recovery and unknown costs never treated as zero |
| 4B | Connect map-based region review to repair and assembled-art acceptance | Exact output hashes and dependency states bind each decision; rejected art cannot enter a production release |
| 5A | Define and complete the first connected illustrated district | Full coverage and seam review, accepted-output cost and review-time measurements |

The service-facing preview is a separate, useful milestone: an interactive
Pixel city view, independent source view, explicit provenance, immutable image
versions and a checked web bundle. It does not waive any of the production-art
acceptance gates above. See [preview releases](PREVIEW_RELEASE.md).

Use `just` as the entry point. `just preview-package` builds the selected preview
and a standalone checked artifact. Existing source, render, review and repair
tasks remain available. Use worktree-local `.cache/` directories, validate YAML
with `yq`, and use conventional commits.

## Decisions and measurement checkpoints

| Checkpoint | Decision or evidence required |
| --- | --- |
| Phase 1 | Lock projection, camera orientation and native pixel scale before expanding the renderer |
| Phase 2 | Confirm source suitability across the 23-ward working target; identify missing data adapters and layers |
| Phase 3 | Owner approves style and fidelity rubric; choose deterministic-only or controlled AI treatment |
| Before bulk generation | Approve a finite pilot spend and retry cap; no assumed unlimited generation |
| Phase 5 | Measure cost, labor, rejection rate and bytes per accepted area; estimate low/base/high expansion cases |
| Each Phase 6 batch | Approve coverage and allowance using measured results; resolve blockers before proceeding |

Render time and cost do not scale only with land area: native resolution, tall-
building overscan, source density, rejection rate and manual repairs matter.
The NYC project's historical model prices and tile counts are not Tokyo budget
estimates. A credible whole-city schedule follows the district benchmark.

## Reference sources

- [isometric.nyc](https://isometric.nyc/): product reference, inspected 2026-10-21.
- [Isometric NYC source](https://github.com/cannoneyed/isometric-nyc),
  [MIT licence](https://github.com/cannoneyed/isometric-nyc/blob/main/LICENSE),
  [viewer documentation](https://github.com/cannoneyed/isometric-nyc/blob/main/docs/app.md)
  and [viewer implementation](https://github.com/cannoneyed/isometric-nyc/blob/main/src/app/src/components/IsometricMap.tsx).
- [Author's project account](https://cannoneyed.com/projects/isometric-nyc):
  generation approach and the need for manual correction.
- [Generation workflow](https://github.com/cannoneyed/isometric-nyc/blob/main/docs/generation.md)
  and [data documentation](https://github.com/cannoneyed/isometric-nyc/blob/main/docs/data.md):
  references for review operations and image-pyramid production.
- [London, drawn](https://sheets.works/data-viz/london): a separate reference for
  place discovery and map navigation, not a source of Tokyo imagery or a licence
  basis for copying its assets.
- [This project's source terms](../THIRD_PARTY_NOTICES.md) and
  [source-quality notes](../README.txt): the basis for the current Tokyo inputs.
