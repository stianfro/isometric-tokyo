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

Blender 5.2 otherwise drops unsigned 16-bit scalar custom attributes on import.
The importer adapter preserves these as exact Blender integer attributes during
the synchronous import, then restores Blender's original helper. Normalized
uint16 custom attributes are rejected rather than reinterpreted. Original source
buffers are unchanged, and the guide checks still require valid feature-table
ranges and agreement at all three triangle vertices. A real affected tile retained
the same 32,052 vertices and 10,684 triangles while recovering all 470 original
batch values, with a byte-identical geometry digest.

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

The first context sample contained 28 transportation tiles and two water tiles,
with 500 and 107 original source features respectively. Both available vegetation
sources have zero tile intersection with this sample's geographic selection.
It therefore contained no downloaded trees. The expanded study instead includes
101 context tiles: 92 transportation, seven vegetation and two water tiles. The
vegetation selection includes three plant-cover tiles and four tree tiles in the
eastern part of the view. These genuine source meshes retain their original IDs,
separately from the mapped vegetation representations described below.
Water tiles intersecting a selection may
still have no visible water pixels in the chosen camera view. Source coverage
remains unverified, regardless of whether guide IDs are complete.

## Bounded native terrain support

The corrected study uses a full camera-ground support patch, not just terrain
intersecting the original building-selection box. The rectangle is computed from
the actual terrain height interval and the fixed camera, plus a 25 m support
margin. It does not require a city-sized mesh or the whole building-shadow source
envelope. All image chunks share this same patch.

`fetch_terrain.py` now defaults to `--method native`. It validates source triangle
orientation, projected tile area and edge incidence; checks matching horizontal
samples at neighboring tile boundaries; welds them to common vertices; and clips
only at the outer support perimeter. A global coordinate key and deterministic
source ownership make the joins repeatable. Nonmatching edge samples or more than
0.02 m of join disagreement fail rather than hiding the problem. Only small
measured edge quantization differences are reconciled. Interior native vertices
and sharp height features remain intact.

The bounded sample uses 30 available zoom-15 tiles, 697,132 source bytes, 47,914
output vertices and 94,188 triangles. All 49 neighboring tile-edge pairs match.
The maximum join correction is 0.000619 m, with zero open interior edges. Terrain
shading normals are smooth, but geometry heights are not filtered. The source
geometry still contains steep local grades; smooth shading is not a survey repair.
Zoom 16 returned 404 for this study despite the service-wide maximum of 18, so the
sample records zoom 15 rather than claiming an unavailable native resolution.

A regular global lattice remains an explicit experimental `--method lattice`
option. It evaluates source heights barycentrically and reports error against
native vertices. At quarter-arcsecond spacing, the trial had a median difference
of 0.210 m, 95th percentile 1.401 m and maximum 9.763 m. That maximum is material,
so the trial was rejected for the styled reference in favor of native TIN detail.
It is not a hidden smoothing step.

The [official terrain documentation](https://docs.plateauview.mlit.go.jp/datasets/terrain/)
states that delivered heights already include the fixed source geoid correction
and are ellipsoidal. The renderer applies no additional geoid or global height
shift. Road/terrain separation remains a measured local-data issue, not evidence
for adding an arbitrary constant height. Previous apparent folds did not coincide
with reversed triangles or nonmanifold source edges.

The fetch command has explicit tile, source-byte and output-vertex caps. Original
tiles and their hashes remain cached alongside the derived support mesh. A new
mesh is published atomically only after validation succeeds.

## Source-located vegetation representations

The optional `--vegetation` input accepts a checked source-vegetation manifest and
verifies its original response/query/notice assets. Crowns use either mapped tree
points or deterministic artistic samples within mapped woodland, not all park
boundaries. Crown dimensions and woodland fill positions are explicitly artistic,
not a surveyed tree inventory. Trees are draped to the shared terrain without
changing terrain heights. Grass, plazas and polygon holes are not filled with
unverified tree geometry.

Objects carry `atlas_semantic=vegetation` and `vegetation_part=canopy|trunk` for
style materials. Their guide identities retain the original mapped source ID and
a separate representation ID. They are excluded from genuine PLATEAU source
feature counts and never labeled building IDs or measured tree geometry. OSM
attribution, ODbL terms and the public data reference travel with render metadata.

## Corrected expanded study

The corrected headless comparison retains the original full study view at
360 x 360 pixels, split into nine chunks, plus a separately rendered full-view
reference. It includes 138 building tiles from Shinjuku, Shibuya and Nakano,
101 official context tiles, the shared native terrain patch, the expanded
orthophoto and 1,083 mapped/artistic vegetation representations.

| Check | Result |
| --- | --- |
| Building meshes with verified source IDs | 138 of 138, zero unresolved |
| Official context meshes with verified source IDs | 101 of 101, zero unresolved |
| Genuine feature registry | 16,859 buildings; 2,388 transportation; 107 water; 59 vegetation |
| Separate vegetation representations | 182 mapped tree points and 901 artistic woodland placements; no measured dimensions |
| Geometry sampled by guides | 999,378 triangles |
| Core pixels without a geometric surface | Zero |
| All seven categorical/numeric guide artifacts | Exact match to the full-view reference across all nine chunks |
| Cycles samples | 1,024, fixed seed; no adaptive sampling or denoising |
| Textured mean / worst boundary-band error | 0.929 / 1.125 in 8-bit channel units |
| Geometry mean / worst boundary-band error | 0.892 / 1.070 |
| Unchanged image thresholds | Whole-image mean at most 1.0; boundary-band mean at most 2.0 |
| Chunk and reference rendering time | 462.6 seconds, excluding source preparation |

The expanded scene failed the same shaded-noise gate at 256 samples: textured
mean 1.821 and geometry mean 1.714. Raising the sample count reduced stochastic
noise; no threshold was relaxed. The exact guide comparison passed at both sample
counts. The first expanded run exposed the uint16 importer issue described above;
the final comparison uses the corrected importer and genuine source IDs.

The expanded road diagnostic has 1,971 terrain intersections among 2,048 sampled
source vertices, with median separation +0.0085 m and 10th/90th percentiles
-0.2327/+0.3405 m. Extrema remain -7.425/+25.871 m and are not automatically treated
as errors or corrected. Water covers a much larger geographic area than the
study: only 148 of 2,048 samples intersect this terrain, with median -6.589 m and
extrema -30.063/+2.721 m. Only 14 core pixels show water in this view. These
measurements do not establish a shared source date or survey accuracy, and no
context height shift has been applied.

The independent [coverage audit](COVERAGE.md) checks the canonical view's planned
building selection, native terrain support and orthophoto support. Its narrow
checks are not a claim of real-world completeness. Source coverage, visual
approval and illustrated-style approval remain false.

## Earlier measured acceptance evidence

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

- Loaded geometry is not the same as real-world completeness. The expanded study
  audits selected metadata volumes for the fixed view and directional sun. This
  does not prove upstream survey completeness or diffuse-light influence bounds.
  The broader `source_coverage_verified` flag therefore remains false.
- The original study's terrain extended beyond its orthophoto footprint. The
  expanded study fetches imagery for the full bounded terrain support. For any
  future input that lacks imagery, neutral gray means unavailable imagery, not a
  claim about the actual surface.
- The corrected native terrain patch reconciles measured quantization at matched
  source joins and verifies no open interior edges. Building-ground and road-ground
  offsets still require source review. Geometric continuity does not prove survey
  accuracy or identical dates across input layers.
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

Reproduce the final bounded comparison with `just source-study` after the expanded
source acquisitions. The committed `configs/shinjuku-source-check.json` retains
the same world camera at 360 px, nine chunks and 1,024 samples. Use a fresh output
directory. The full diagnostic files are generated locally; a sanitized
[measured report](../previews/shinjuku-art/source-verification.json) is published.
