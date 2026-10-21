# Vegetation sources and artistic representation

The vegetation input is a separate geographic-data layer. It does not turn the
Shinjuku source study into a surveyed tree inventory, and it does not alter the
meaning of the source building geometry.

## What the source check found

The four published Shinjuku FY2025 PLATEAU vegetation tilesets were checked at
both LOD1 and LOD3. Their metadata totals 10,152 bytes. Both PlantCover levels
have geographic bounds approximately `[139.70010,35.69189,139.70331,35.69375]`;
both SolitaryVegetationObject levels have bounds approximately
`[139.70023,35.69202,139.70365,35.69387]`. None intersects the original Central
Park study bounds `[139.6873,35.6860,139.6961,35.6932]`. The eastern patches do
intersect the larger visible-ground extent. A catalog entry for vegetation is
therefore not proof of Central Park coverage, or proof that a particular render
loaded those eastern trees. URLs and checksums are preserved by the existing
source inventory and context sampler.

The ward's [tenth green-space survey description](https://www.city.shinjuku.lg.jp/content/000439281.pdf)
describes aerial-photo land-cover interpretation and field surveys of larger
trees. It is evidence that richer survey information exists, not a downloadable
licensed tree-coordinate dataset. The reviewed ward publications and
[official park page](https://www.city.shinjuku.lg.jp/seikatsu/file15_02_00001.html)
did not establish a compatible open machine-readable tree inventory for this
sample. That is a bounded research result, not a claim that no such data exists.

A bounded OpenStreetMap query provides usable geolocated detail instead:
individual mapped tree points, woodland patches, grass, paths, buildings and
water. The [tree tag](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dtree)
represents an individual mapped tree; [woodland tags](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dwood)
represent tree-covered areas. A [park boundary](https://wiki.openstreetmap.org/wiki/Tag:leisure%3Dpark)
is not a canopy footprint. The adapter keeps these categories separate.

The expanded collection used bounds
`[139.67688,35.67835,139.70549,35.70169]`. Its response is 22,570,051 bytes,
under a 25 MB hard cap. The source database timestamp is
`2026-10-21T04:10:31Z`. It contains 756 mapped tree points, 81 woodland polygons,
63 grass polygons, 89 shrub/hedge features and 68 park/garden boundaries, plus
building, road, water and play-area exclusions. Missing records do not prove
that vegetation is absent. The full source bbox is not a completeness guarantee.

## Source versus representation

| Category | Meaning | Permitted treatment |
| --- | --- | --- |
| Surveyed source tree | An independently verified tree inventory or original authoritative 3D feature | Use its documented geometry and dimensions; none is inferred from OSM |
| `mapped_tree` | An OSM tree point at a contributor-mapped location | Keep its coordinate; mark the generated crown/trunk dimensions as artistic |
| `mapped_woodland` | A mapped wooded-area polygon | Optional deterministic artistic crowns contained within the polygon |
| `mapped_grass` | Mapped grass or grassland | Ground cover, never a reason to add trees |
| `mapped_scrub` | Shrub or hedge geometry | Distinct low vegetation, not forest trees |
| `park_boundary` | A recreational-area boundary | Reference only; do not fill lawns, plazas or paths with trees |

OSM `source=survey` tags are retained as contributor provenance. They are not
promoted to `survey_verified:true`. Crown radius and height are not measured by
this adapter, even for a mapped tree point. Woodland fill positions are artistic
instances, not inferred observations or new geographic facts.

The canopy sampler uses a stable hashed metric grid, with configurable spacing.
A complete crown disk must fit inside its woodland polygon and avoid its holes.
It rejects overlap with mapped grass, buildings, water, pitches, playgrounds and
conservative path/road buffers. Building relations with complex parts use an
explicitly labelled conservative bounding envelope for exclusion, not an invented
building footprint. Original mapped tree positions are not moved to make them
fit. Rejected points remain in the source feature list.

An explicit primary-source exclusion bbox can prevent duplicate interpretation
where authoritative vegetation is actually loaded. Such an exclusion is not
proof of complete source coverage inside that rectangle. Do not exclude an area
merely because a vegetation catalog item is listed there. The exact exclusions,
spacing, rejected candidates and any 1,500-crown budget omissions are recorded in
`representation`. Surface polygons with holes are not blindly triangulated or
filled by this adapter.

The renderer drapes accepted samples to the same terrain as the buildings and
marks their `vegetation_part` as `canopy` or `trunk`. Grass/plaza imagery remains
visible. Additional procedural art is separate from AI styling: this source
adapter records `ai_styling:false` and `artistic_vegetation:true`.

## Data contract

`data/vegetation/manifest.json` contains:

- `schema_version:1`, `kind:"source-vegetation"`, `status:"complete"` and
  `crs:"EPSG:4326"`. Geographic geometry coordinates are longitude, latitude.
- `source`: endpoint, query and raw-response hashes, source timestamp, attribution,
  ODbL licence and relative paths to local acquisition records.
- `features`: normalized GeoJSON features, preserving original OSM IDs, versions,
  timestamps, tags and mapped geometry. Conservative building-relation envelopes
  have their own `geometry_interpretation` value.
- `canopy_samples`: `id`, `source_id`, `lon`, `lat`, `radius_m`, `height_m`,
  `placement` (`mapped_tree` or `artistic_woodland_fill`),
  `dimensions_measured:false`, `canopy_representation:"artistic"`.
- `representation`, `feature_counts`, `skipped_features`, notices and asset hashes.
- `source_coverage_verified:false`, `survey_verified:false` and an optional
  repository-relative `public_data_reference`.

The raw acquisition response stays in ignored local inputs. Public bundles omit
OSM contributor usernames, numeric user IDs and changeset IDs while retaining
feature identities and the exact artistic placement database.

## Reproduction and checks

The source config `configs/shinjuku-context.json` retains the original camera and
pixel frame while selecting the larger visible-ground extent. Fetch the official
context at its listed LODs, without downgrading roads to solve a vegetation budget:

```sh
just context-fetch-bounded --config configs/shinjuku-context.json \
  --output data/context-study-v2 --types tran wtr veg \
  --max-tiles 128 --max-bytes 64000000
```

The preflight selects 92 road tiles at LOD3, 3 PlantCover tiles at LOD3,
4 solitary-tree tiles at LOD3 and 2 water tiles at LOD1. The completed sample has
2,554 source features, 48,638,028 payload bytes and 110,122 metadata bytes, within
the explicit 64 MB budget. The self-contained GLBs and source hashes are checked.
This remains Shinjuku catalog context, not complete neighboring-ward
coverage. The default sampler budget remains smaller.

After that actual official vegetation is available for the renderer, reproduce
the OSM interpretation and a fresh public-data bundle with:

```sh
just vegetation-fetch --bbox 139.67688 35.67835 139.70549 35.70169 \
  --max-bytes 25000000 --spacing 10 \
  --exclude-bbox 139.7001004671548 35.69189017768365 139.7036549027925 35.69386991149829 \
  --public-reference datasets/shinjuku-vegetation/ \
  --public-bundle .cache/vegetation-public-rebuild
```

This produces 1,083 crowns: 182 retained mapped-tree positions and 901 explicitly
artistic woodland placements, with no canopy-budget omissions. The exclusion is
the union envelope of the two actual official vegetation source selections. The
public reference points to the committed database used by the preview, not to a
promise that a future rebuild has been approved. Use a new bundle output directory
for another run, and add `--offline` to reuse only the checksummed OSM response.

Use the repository's `just` wrapper for `scripts/fetch_vegetation.py`. The adapter
accepts `--bbox WEST SOUTH EAST NORTH`, `--origin LON LAT`, `--max-bytes`,
`--spacing`, `--exclude-bbox`, `--offline`, `--output`, `--public-bundle` and
`--public-reference`. A source query is sent once, without automatic retries or
fallback to unbounded geographic downloads. The default transfer cap is 5 MB;
the expanded sample requires the explicit 25 MB cap. The query must remain within
a bounded non-wrapping study area. Redirects are rejected.

Cached response size, endpoint, query hash and content hash are verified before
reuse. An Overpass error/partial-result remark is a failure, not empty coverage.
Malformed exclusions fail closed. Unsupported green geometries are reported,
not silently filled. Packages use a marker and staged replacement; unknown output
directories are not overwritten. Public bundle output directories must be new.

Focused unit tests cover source classification, incomplete responses, budget and
cache failures, ring assembly, holes, road/building exclusions, reproducibility,
artistic provenance, data publication and overwrite protection. The normal
Python lint/test tasks include these files.

## Public data and licensing

The compact public bundle belongs under `datasets/shinjuku-vegetation/`. Its
`vegetation.json.gz` contains the actual source-feature and artistic-placement
database used by the renderer, not just a future download instruction. The bundle
also includes the query, notice, reading instructions and `SHA256SUMS`. It is
about 1.7 MB compressed. The source response hash remains recorded without
publishing raw contributor account metadata.

OSM and adapted geographic/placement data retain
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) terms, not the code's MIT
licence. Public previews and the map need visible **© OpenStreetMap contributors**
credit and access to the licence notice. The bundled database makes the data
behind the produced imagery available. These obligations are documented by
[OpenStreetMap's copyright page](https://www.openstreetmap.org/copyright).
PLATEAU and other source notices remain separate. No source provider's endorsement
or geographic completeness is implied.
