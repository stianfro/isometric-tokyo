# isometric.tokyo

An independent illustrated isometric map of Tokyo in development, using open
Project PLATEAU data. The goal is a continuous, explorable map in the same product
category as [isometric.nyc](https://isometric.nyc/), with original Tokyo artwork.

**Current state:** a reproducible, unstyled Shinjuku source-quality study. The
interactive viewer is planned, not implemented.

![Unstyled Shinjuku sample](previews/shinjuku-isometric.png)

Derived from Tokyo Metropolitan Government / Project PLATEAU, Shinjuku FY2025,
and PLATEAU-Ortho 2023. Terrain: **PLATEAU | Mapterhorn | 国土地理院**.
Selected, transformed, cropped and rendered by this project. No AI styling.
Independent project, not an official product or endorsement.
See [source terms and full attribution](THIRD_PARTY_NOTICES.md).

## What is here

- A roughly 800 × 800 metre sample around the Tokyo Metropolitan Government buildings.
- Scripts to download bounded building, orthophoto and terrain samples.
- Blender rendering with a fixed orthographic isometric camera.
- [Geometry-only comparison](previews/shinjuku-geometry.png).
- Offline tests, source checksums and saved-scene verification.
- [The phased implementation roadmap](docs/PLAN.md), from the source study to a
  seamless illustrated atlas and interactive Tokyo map.
- [Detailed source-quality notes and provenance](README.txt).

Landmark geometry is useful, but some smaller buildings are untextured. Trees are
currently ground photography, not 3D vegetation. Source dates and LOD vary.
The FY2025 dataset edition does not mean that all photography was taken in 2025.

## Roadmap

The [implementation plan](docs/PLAN.md) covers the full path: a tiled Shinjuku
viewer, seamless geographic rendering, an approved art direction, production and
review tools, a complete illustrated district, then connected expansion toward
Tokyo's 23 special wards. Each phase has deliverables and acceptance criteria.
The current sample is the baseline, not the completed product.

## Reproduce

Requirements: Python 3.11+, Pillow, `just`, `uv`, and Blender 5.2 on `PATH`.
Blender is needed only for rendering and saved-scene checks. `uvx` supplies the
pinned Ruff version. No PLATEAU account or API key is required for these sources.

```sh
uv venv
uv pip install -r requirements.txt
export PYTHON="$PWD/.venv/bin/python"
just check
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

`just check` runs offline unit tests and lints without downloaded geographic data
or Blender. The first Ruff invocation needs network access; `UV_OFFLINE=true`
works once the pinned tool is cached. `just package` produces a local source kit
after a successful render. Large source files and Blender projects are not in Git.
