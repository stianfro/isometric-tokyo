# Source rendering and guide layers

The atlas renderer retains **all loaded source geometry** for every chunk. It
renders an expanded image rectangle, then crops pixels, not buildings or shadows.
The camera comes from the common [atlas contract](ATLAS.md). Orthophoto UVs use
inverse ENU-to-geodetic coordinates followed by Web Mercator coordinates at each
mesh vertex. Interpolation inside a terrain triangle remains an approximation.

The original sample renderer remains available. The atlas renderer does not
replace the untouched source GLBs or the source-study baseline images.

## Development tasks

- `just atlas-render`: render and finalize a bounded source atlas from the
  downloaded building, ground and terrain manifests.
- `just atlas-finalize <directory>`: verify dimensions and the chunk partition,
  crop overscan, remove image metadata, record hashes and assemble a small
  diagnostic mosaic.
- `just seam-check`: render the synthetic crossing-tower fixture and compare
  independently rendered chunks against one full-view reference.
- `just chunk-tiles <index> <output>`: build the browser pyramid from finalized
  chunks without assembling a whole city image in memory.

The Blender entry point is `scripts/render_atlas.py`. Its optional `--guides`
flag enables the source-identity and numeric passes described below. `--reference`
also renders one full-view image for a bounded comparison. With a reference,
finalization automatically requires exact guide equivalence, if guides exist.
`--verify-seams` on the finalizer also checks the shaded image residuals.

Every output directory must be fresh. A failed render leaves an index marked
`rendering`, which cannot be finalized or mistaken for a finished release.
Successful Blender work changes the status to `rendered-overscan`; finalization
changes it to `complete`. Completion describes the requested assets, not source
coverage, art approval or publication readiness.

Default safety limits are 16 render chunks, 128 source tiles, 512 MiB of source
files, 4096 squared pixels per rendered frame and 1024 squared guide rays per
frame. Limits can be changed explicitly. Compressed source bytes do not predict
decoded mesh or texture memory. The diagnostic mosaic has a separate 4096 squared
pixel cap and is not a city-scale assembly method.

## Source identities and categorical guides

For the tested Shinjuku data, Blender retains `_BATCHID` as an integer vertex
attribute. The guide builder checks:

1. The original b3dm checksum and its byte-identical embedded GLB.
2. The batch count and the original `gml_id`/official building-ID columns.
3. The attribute type and vertex count after import.
4. Agreement of all three vertex batch IDs on every triangle.

One registry is created from the complete supplied building and optional context
manifests before any camera runs. It is sorted by source semantic kind and genuine
`gml_id`, not camera or traversal order. Numeric IDs are **release-local**; the
semantic kind and `gml_id` retain the source identity across releases.
All chunk jobs for a release must use the same registry and source selection.
Changing the selected source set may change numeric IDs in the next release.

`guides/feature-registry.json` reserves 0 for background and 1 for terrain/ground.
Other entries identify source buildings or original transportation, water and
vegetation features when supplied. Context IDs are never labeled building IDs.
If an input lacks a usable batch
attribute/table, its guide explicitly uses an `unresolved-source-tile` identity,
not a fabricated building identity. Missing or conflicting IDs block claims that
all building identities were preserved.

Categorical guides use one BVH ray at the center of each global atlas pixel.
There is no antialiasing, interpolation, shading or color management. The rays
use opaque geometry, not photographic appearance or texture transparency.

| Artifact | Encoding | Meaning |
| --- | --- | --- |
| `feature-id` | RGB PNG, `R*65536 + G*256 + B` | Exact release registry ID |
| `semantic` | 8-bit grayscale PNG | 0 background, 1 ground, 2 building, 3 transportation, 4 water, 5 vegetation |
| `silhouette` | 8-bit grayscale PNG | 255 building, 0 for every other category |
| `depth` | Row-major little-endian float32 metres | Signed distance along the canonical camera-forward direction |
| `normal` | Row-major little-endian float32 ENU XYZ triples | Geometric triangle normal in the atlas world frame |
| `depth-preview` | 16-bit grayscale PNG | Scaled preview, with 0 reserved for background |
| `normal-preview` | RGB PNG | Normal components mapped from `[-1, 1]` to `[0, 255]` |

Depth is `dot(hit - canonical_camera_location, camera_forward)`, where the
canonical camera location is the configured target plus camera offset. It does
not restart at each chunk's camera. Preview scaling uses the full configured
source bounds, height interval and shadow buffer, so changing only the current
chunk bounds does not change the encoding. Values outside the preview interval
clip; `.f32` values are authoritative. Background numeric values are zero and
must be interpreted with the semantic mask. Normals are face normals, not
interpolated material/shading normals.

Guide files are recorded separately in `chunk.guides`. The public image layers
remain `textured` and `geometry`. Finalization verifies checksums, file dimensions,
finite numeric values, unit normals, registry membership and semantic agreement.
When a full-view reference exists, every guide PNG and every numeric byte must
match the corresponding reference crop exactly.

## Optional source context

Pass `--context data/context/manifest.json` to include a bounded download from
[scripts/fetch_context.py](../scripts/fetch_context.py). The context manifest must
be complete, contain known PLATEAU feature types and use self-contained GLBs.
The renderer verifies original b3dm, extracted GLB and batch-table file hashes
before import. External texture dependencies currently need a separate adapter
and are rejected, not silently dropped. Tile and byte caps include context.

Every loaded context mesh is retained for every image chunk. Original source
transforms and elevations are used without invented height shifts. Provenance
records source item IDs, public source URLs, editions, terms, feature counts,
original ENU bounds and `height_adjustment_m: 0`.

Bounded numeric diagnostics sample at most 2,048 vertices per context category
against the original terrain. Positive separation means above terrain, not an
error by itself: bridges, water surfaces and source dates can legitimately differ.
The diagnostic reports missing terrain intersections, min/max and percentile
separation without modifying geometry or claiming alignment has been reviewed.

The first context sample contains 28 transportation tiles and two water tiles,
with 500 and 107 original source features respectively. Both available vegetation
sources have zero tile intersection with this sample's geographic selection.
There are no downloaded trees to render. Water tiles intersecting a selection may
still have no visible water pixels in the chosen camera view. Source coverage
remains unverified, regardless of whether guide IDs are complete.

## Measured acceptance evidence

Headless checks on 2026-10-21 used Blender 5.2.0, Cycles CPU, fixed seeds, no
adaptive sampling and no denoising. Lower sample counts initially failed the
same image-error gate. The thresholds were not loosened to pass.

| Check | Result |
| --- | --- |
| Synthetic 3 x 3 patch, 288 x 216 pixels, 128 samples | Passed, including partial edge chunks, crossing towers, shadows and four-way joins |
| Synthetic textured mean / worst boundary-band error | 0.481 / 0.741 in 8-bit channel units |
| Synthetic geometry mean / worst boundary-band error | 0.532 / 0.990 |
| Shinjuku 3 x 3 patch, 720 x 720 pixels, 256 samples | Passed against a single full-view reference |
| Shinjuku textured mean / worst boundary-band error | 0.784 / 1.173 |
| Shinjuku geometry mean / worst boundary-band error | 0.914 / 1.498 |
| Image thresholds | Mean at most 1.0, worst boundary-band mean at most 2.0 |
| Shinjuku guide patch, 360 x 360 pixels | All seven artifacts exactly match the full-view reference across nine chunks |
| Source guide identity checks | 1,076 source building records, 26 resolved meshes, zero unresolved meshes |
| Source geometry used | 440,109 building vertices; 147,726 guide triangles including terrain |
| Source with road/water context, 360 x 360 pixels, 256 samples | Shaded seam gate passed; all seven guide artifacts match the reference exactly |
| Context textured mean / worst boundary-band error | 0.957 / 1.620 |
| Context geometry mean / worst boundary-band error | 0.974 / 1.678 |
| Context identity checks | 500 transportation and 107 water features; 28 and two resolved meshes; zero unresolved |
| Context geometry added | 200,358 vertices; 214,512 total guide triangles including buildings and terrain |

The two shaded layers are not bit-identical: Cycles sample noise changes with
image dimensions. Reports retain the measured residuals and maximum errors.
The categorical and numeric guide comparison is exact.

The context elevation diagnostic sampled 2,048 vertices per category. Road
samples had 2,047 terrain intersections, median separation -0.114 m, 10th/90th
percentiles -1.806/+2.544 m, and extrema -5.706/+14.122 m. Water tiles extend far
outside the terrain footprint: only 52 samples intersected it, with a median of
-0.396 m and extrema -16.155/+2.627 m. These measurements do not establish the
reason for any discrepancy. Geometry was left unchanged and alignment remains
unreviewed. A fixed offset would not resolve these varying separations.

## Limits still to resolve

- Loaded geometry is not the same as complete geometry. Selected source tiles may
  omit offscreen occluders or shadow casters. Every render still records
  `source_coverage_verified: false`.
- Terrain extends beyond the orthophoto footprint. Neutral gray means unavailable
  imagery, not a claim about the actual surface.
- Terrain edges have not been explicitly reconciled and building-ground offsets
  still require inspection. Shared source geometry alone does not prove that the
  source terrain itself has no cracks.
- Mixed LOD, missing textures, photographic shadows and source dates remain source
  quality issues. Optional context distinguishes transportation, water and
  vegetation only where source meshes exist. It does not infer trees from imagery
  or subdivide transportation features into unverified road/rail classes.
- The source manifest and the current atlas are small enough to load all selected
  meshes into one Blender process. City-scale jobs need a verified influence
  footprint and a consistent release-wide feature registry before reducing that
  loaded geometry set.
- Neither a passed renderer check nor a complete source-ID registry approves an
  illustrated style or completes the district/23-ward release gates.

Source attribution and separate geographic-data terms remain in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
