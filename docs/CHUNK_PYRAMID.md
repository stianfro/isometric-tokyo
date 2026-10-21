# Sparse chunk pyramids

`scripts/chunk_pyramid.py` implements the bounded-memory image-export part of
Phase 4. It consumes a finalized renderer index, not arbitrary pictures or
unreviewed AI patches. It does not complete the production/review workflow or
certify geographic coverage.

## Input and output

The input is a version 1 `render-index.json` with `status: complete`, global
`bounds_px`, `chunk_size_px`, unique `layers`, and `config_sha256`. Each chunk
provides `id`, integer `bounds_px` and `outputs` for every layer. An output entry
contains a relative `path`, SHA256, byte count, width and height.

Chunks must occupy unique cells of the global zero-anchored chunk grid. The
outermost cells can be clipped to the atlas rectangle. Source dimensions and
checksums are verified before construction, and hashes are checked again before
publishing. Missing declared files, missing layers, duplicate cells, off-grid
bounds and path escapes fail the build.

Omitted grid cells are explicitly unavailable coverage, not successful empty
renders. A declared chunk cannot silently disappear into that category. DZI
`DisplayRect` elements describe the available rectangles at every level. Empty
space outside those rectangles does not receive millions of placeholder tiles.
The exported record keeps `source_coverage_verified: false` and
`artwork_approved: false`. Approval belongs to the separate review workflow.

Each layer produces a `.dzi` descriptor and a sparse `LAYER_files/LEVEL/X_Y.webp`
image tree. The native level preserves source pixels in lossless RGBA WebP,
including transparency. The DZI image origin is the top-left of `bounds_px`; the
manifest retains the global offset for other tools.

## Bounded pixel memory

The exporter never constructs a full-resolution atlas image. It reads one source
chunk at a time into tile-sized output windows. A chunk may decode at most
16 million pixels. Tile size is limited to 16 through 1024 pixels, with at most
16 pixels of overlap. Adjust those hard limits deliberately before processing
larger chunks; a limit error is not a reason to disable validation.

Lower levels are generated from the immediately higher level. Each new pixel
represents an aligned 2 × 2 block, reduced with BOX filtering. The right and bottom
edges are padded transparent when dimensions are odd. All windows use the same
integer grid, avoiding independent resampling offsets at tile joins. This differs
from the bounded sample exporter's full-image Lanczos resize and is recorded as
algorithm `rgba-box2-sparse-v1`.

A lower-level window uses at most `4 * (tile_size + 2 * overlap)^2` input pixels,
plus its output, a decoded child tile and temporary crops. Source decoding is
separately bounded by the maximum chunk size. Metadata grows with actual source
chunks and covered tiles, not the number of empty native pixels. The exporter
caps the number of covered positions across pyramid levels at one million before
creating output.

Reported `largest_window_pixels` and `largest_source_chunk_pixels` describe these
buffers, not a measured process-RSS benchmark. Tests instrument image allocation
on a 1,000,000 × 1,000,000 pixel sparse canvas containing two 32-pixel chunks. They
also verify that fewer than 200 actual tiles are written for two layers. This is
bounded-memory evidence, not a city-scale throughput estimate.

## Incremental rebuilding and release safety

The exporter records logical chunk IDs, bounds and per-layer source hashes.
With unchanged extent, configuration, layers and tile settings:

1. Unchanged inputs reuse their verified existing tiles.
2. Added, removed or changed chunk regions invalidate intersecting native tiles,
   including overlap pixels.
3. The changed rectangles are projected to each lower level, invalidating only
   intersecting ancestors and their overlaps.
4. Unaffected layers and distant branches remain reusable.

A change to the global extent, configuration or tile parameters triggers a full
rebuild. Rebasing a DZI canvas moves its tile grid even if global atlas pixels are
unchanged, so reusing the old pyramid would be incorrect. This first implementation
copies reusable files into staging rather than requiring hard links. Reused images
are not decoded and encoded again, although validation still reads their bytes.

Construction and validation happen in a sibling staging directory. A complete
replacement is renamed into place only after verification; failures preserve the
previous output. Unknown output directories are not overwritten. A stale backup
requires inspection rather than being silently deleted. The content identifier
includes the algorithm, global settings and checked source hashes. The asset
inventory validates descriptors, sparse coverage, tile dimensions and checksums.
No-op exports retain the same identity, although build statistics change.

## Tasks and tests

Use `just chunk-tiles INDEX OUTPUT` to export a finalized render index and
`just chunk-verify OUTPUT` to verify an existing result. The CLI also supports
`--tile-size`, `--overlap` and `--full-rebuild`. The latter deliberately avoids
reusing a damaged previous pyramid; it still validates all source assets.

Tests compare every native tile and every reduced level with a small full-image
reference. They cover exact overlaps, odd dimensions, negative origins, sparse
coverage, no-op reuse, one-layer edits, removed chunks, configuration changes,
source changes during export, corrupt outputs and protection of existing files.
An incremental build must match a fresh full rebuild byte for byte. `just check`
runs these alongside the atlas, renderer and job-ledger tests.

## Viewer-ready chunk releases

```sh
just chunk-viewer renders/atlas-context/render-index.json renders/context-tiles
```

The optional `--viewer-metadata` input supplies explicit title, source edition,
HTTPS attribution, geographic study bounds and a source notice. The included
`configs/shinjuku-viewer.json` applies to this Shinjuku source study only. Use
separate metadata for another district or dataset, rather than relabeling it.

The exporter writes `manifest.json` and `NOTICE.txt` into the verified asset
inventory. Release identity covers this metadata as well as source hashes and
pyramid settings. Global integer pixel bounds and rendered chunk rectangles are
preserved, including negative origins and sparse gaps. The browser validates
paired DZI dimensions and rectangles before display. A missing chunk is distinct
from a failed tile request and does not count as verified geographic coverage.

There is no assembled full-resolution PNG in a chunk release. The viewer omits
that download instead of promising a nonexistent file. The bounded flat-image
adapter remains available for the original small source study. Source coverage
and artwork approval remain false for this exporter, regardless of input claims.

AI styling provenance must be an explicit boolean in both the finalized render
index and viewer metadata, and they must agree. A metadata file for an unstyled
source release cannot silently label AI-assisted repairs as unstyled artwork.
