# isometric.tokyo

An independent illustrated isometric map of Tokyo in development, using open
Project PLATEAU data. The goal is a continuous, explorable map in the same product
category as [isometric.nyc](https://isometric.nyc/), with original Tokyo artwork.

**Current state:** an interactive, tiled Shinjuku source atlas, a repeatable chunk
renderer, geographic guide layers and a source-output review workflow. Expanded
source coverage, reconciled terrain and mapped vegetation now support a bounded
Shinjuku art-direction study. No final artwork or complete district is approved.

![Unstyled Shinjuku sample](previews/shinjuku-isometric.png)

Derived from Tokyo Metropolitan Government / Project PLATEAU, Shinjuku FY2025,
and PLATEAU-Ortho 2023. Terrain: **PLATEAU | Mapterhorn | 国土地理院**.
Selected, transformed, cropped and rendered by this project. No AI styling.
Independent project, not an official product or endorsement.
See [source terms and full attribution](THIRD_PARTY_NOTICES.md).

## What is here

- A roughly 800 × 800 metre sample around the Tokyo Metropolitan Government buildings.
- Scripts to download bounded building, orthophoto, terrain, road and water samples.
- A browser viewer with pan/zoom, touch and keyboard controls, aligned source
  comparison, an overview map and shareable views.
- Pixel-aligned Blender chunk rendering, verified against full-view references.
- Original building/context feature-ID, semantic, silhouette, depth and normal guides.
- Bounded-memory image pyramids with selective rebuilding.
- Resumable render jobs, a local review desk, seam regions and immutable repair candidates.
- [Geometry-only comparison](previews/shinjuku-geometry.png).
- Offline tests, source checksums, browser tests and saved-scene verification.
- A verified metadata inventory for all 23 wards, with explicit source-quality gaps.
- [The phased implementation roadmap](docs/PLAN.md), from the source study to a
  seamless illustrated atlas and interactive Tokyo map.
- [Detailed source-quality notes and provenance](README.txt).

Landmark geometry is useful, but some smaller buildings are untextured. The
original atlas above has photographic vegetation only. New studies supplement
it with separately attributed mapped tree points and woodland representations,
not a claimed tree survey. Source dates and LOD vary.
The FY2025 dataset edition does not mean that all photography was taken in 2025.

## Shinjuku art direction

![Unapproved Shinjuku material comparison](previews/shinjuku-art/style-comparison.png)

Source and mapped context compared with **Atlas ink**, **Garden city** and
**Terracotta blocks**. Same camera and source geometry, with artistic facade
marks, palettes and canopy representations. These are geometry-preserving
material studies, not final illustrations or approvals.

City model: Tokyo Metropolitan Government / Project PLATEAU. Imagery:
PLATEAU-Ortho 2023. Terrain: **PLATEAU | Mapterhorn | 国土地理院**.
Vegetation: **© OpenStreetMap contributors**, ODbL. Modified by this independent
project. The [derived vegetation database](datasets/shinjuku-vegetation/README.txt)
and [full source terms](THIRD_PARTY_NOTICES.md) accompany the preview.
See [art-direction notes](docs/STYLE_PILOT.md), [coverage audit](docs/COVERAGE.md)
and [vegetation evidence](docs/VEGETATION.md) before interpreting detail as measured.

Two separate AI-assisted treatments explore a more illustrated result:

| D. Pixel city | E. Printed atlas |
| --- | --- |
| ![AI-assisted Pixel city study](previews/shinjuku-art/ai-pixel-city.png) | ![AI-assisted Printed atlas study](previews/shinjuku-art/ai-printed-atlas.png) |

Both retain a recognizable composition but redraw details and building edges.
Pixel city adds green roofs and planting not established by the source. Neither
is a geometry-accurate comparison layer or approved artwork. Source credits and
terms above still apply. [Prompts, provenance and limitations](previews/shinjuku-art/ai-studies.json)
are retained; these images have not replaced the source atlas.

## Roadmap

The [implementation plan](docs/PLAN.md) covers the full path: a tiled Shinjuku
viewer, seamless geographic rendering, an approved art direction, production and
review tools, a complete illustrated district, then connected expansion toward
Tokyo's 23 special wards. Each phase has deliverables and acceptance criteria.
The current sample is the baseline, not the completed product.

## Explore the source atlas

Requirements: Python 3.11+, Node.js 26, `just` and `uv`. Blender 5.2 is needed only
for new geographic renders and scene checks, not for the viewer. No PLATEAU
account or API key is required for these sources.

```sh
just bootstrap
export PYTHON="$PWD/.venv/bin/python"
just check
just viewer-dev
```

The viewer task builds verified image tiles from the attributed previews already
in this repository. It prints the local development address; it does not open a
browser automatically. Original PNGs remain available from the map credits.

```sh
just viewer-browsers
just viewer-e2e
just viewer-build
just verify-atlas
```

## Reproduce geographic renders

```sh
just fetch
just ground
just terrain
just render
just verify
just verify-scene
just previews
```

Generated files default to `renders/`. Override with `OUTPUT_DIR` or
`just --set out /path/to/output render`. Downloads are cached under `data/`;
tool caches stay under `.cache/`. Source services may change after this snapshot.

`just check` runs Python, viewer and review-tool lints and unit tests without
geographic downloads or Blender. Install development dependencies first. The
first Ruff invocation needs network access; `UV_OFFLINE=true` works once the
pinned tool is cached. `just package` produces a source kit after a sample render.
Large source files, generated tile trees and Blender projects are not in Git.

For the new pipeline:

```sh
just atlas-plan
just seam-check
just atlas-batch
just chunk-tiles renders/batch-shinjuku/render-index.json renders/shinjuku-tiles
just viewer-dev-chunks renders/batch-shinjuku/render-index.json
just review
```

The batch runner resumes without repeating completed jobs and leaves output
awaiting review. See [rendering and guide layers](docs/RENDERING.md),
[production jobs](docs/PRODUCTION.md), [review controls](docs/REVIEW.md),
[sparse pyramids](docs/CHUNK_PYRAMID.md), [seam review and repairs](docs/REPAIR.md),
[source inventory](docs/SOURCE_INVENTORY.md)
and [current implementation status](docs/PROGRESS.md).
