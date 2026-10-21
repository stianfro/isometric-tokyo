# Bounded context sampling

The context sampler selects vegetation (`veg`), transportation (`tran`) and
water (`wtr`) from the verified source inventory. It downloads individual 3D
Tiles contents, not ward archives. This is a source-quality probe and a generic
import foundation, not a complete context layer or an approved atlas release.

## Current Shinjuku result

The **2026-10-21** probe uses `configs/shinjuku.json` source bounds:
139.6873 west, 35.6860 south, 139.6961 east, 35.6932 north.
The recorded source is the official
[Shinjuku FY2025 package](https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025).

| Selected item | LOD | Intersecting tiles | Retained batch features |
| --- | ---: | ---: | ---: |
| `di_13104_tran_lod3` | 3 | 28 | 500 |
| `di_13104_wtr_lod1` | 1 | 2 | 107 |
| `di_13104_veg_PlantCover_lod3` | 3 | 0 | 0 |
| `di_13104_veg_SolitaryVegetationObject_lod3` | 3 | 0 | 0 |

Both vegetation sources also have **zero intersecting tiles at LOD1**. Listed
ward-level vegetation availability does not establish coverage of this study
area. The sampler does not synthesize trees, substitute buildings, or silently
move the requested bounds to obtain a result.

The successful combined sample uses **6,268,178 distinct source bytes**,
including tileset metadata, original payloads and external dependencies.
The successful run transferred 6,158,056 bytes because metadata was already
cached. Separate vegetation metadata probes were small and fetched no models.
All 607 batch entries have retained `gml_id` values, unique within each sampled
feature category. An intersecting source tile may contain features outside the
requested bounds. These counts are not an in-bounds census.

The ignored `data/context/manifest.json` records the selected items and exact
hashes. Its `status: complete` means the selected downloads completed, not that
all roads, water or vegetation are present. `source_coverage_verified` and
`acquisition_dates_verified` remain false. Catalog edition is not a claim about
capture date or temporal alignment with the building source.

## Run

```sh
# Inspect vegetation coverage without downloading geometry.
just context-preflight

# Fetch the bounded combined sample.
just context-fetch

# Optional CLI controls for another preflight.
python3 scripts/fetch_context.py --types veg --max-lod 1 --preflight-only
```

Defaults use the committed inventory snapshot, Shinjuku config and a
checksummed `.cache/context-downloads` cache. The CLI's default type is
vegetation, so a full default download deliberately fails when there is no
intersection. `--types veg tran wtr` requests the combined sample. `--config`,
`--inventory`, `--output`, `--cache`, `--max-lod` and `--max-bytes` are explicit
controls. Outputs remain under ignored `data/context` by default.

The selector chooses the highest allowed LOD separately for each catalog
dataset, preferring a textured item when LOD ties. This keeps PlantCover and
SolitaryVegetationObject distinct. It never adds lower-LOD copies of the same
dataset. Requested categories missing from the inventory fail explicitly.

## Limits and failure behavior

- A hard maximum of **25,000,000 bytes** applies separately to distinct source
  inputs and actual response-body bytes read in a run. A smaller `--max-bytes`
  value is allowed. Cached inputs still count toward the source limit.
- Requests are limited to 128, with at most one retry per request. Partial
  transfers count toward the actual network budget. Content-Length is checked
  before payload reads when supplied. Unknown-length streams stop at the limit
  without reading an extra byte to guess EOF.
- Only the confirmed HTTPS PLATEAU asset origin is accepted. Redirects and
  whole-model ZIP/7z archives are rejected. Cache hashes are rechecked on use.
- All selected tileset metadata is inspected before any model payload is
  downloaded. Metadata is limited to 2 MB per item and 100,000 explicit nodes;
  no more than 64 intersecting tiles are accepted per source item.
- The current adapter requires explicit finite geographic region bounds and
  b3dm or GLB contents. Box/sphere bounds, implicit tiling, external tilesets,
  multiple contents and unsupported dependency paths fail rather than being
  treated as empty coverage.
- A failed fetch does not publish a partial manifest or replace a previous
  completed sample. Replacement is allowed only for sampler-marked output
  directories. Cached complete responses may be reused on retry.

Network limits count application response bodies, not HTTP headers, TLS
traffic or bytes buffered inside the operating system. This is a bounded
public-data sampler, not a general-purpose downloader.

## Provenance and importer contract

Each source records its catalog item and dataset IDs, exact source URL, edition,
raw tileset metadata and checksum. Package identity and the supplied
[PLATEAU terms](https://www.mlit.go.jp/plateau/site-policy/) remain separate from
the code's MIT licence.

Each tile preserves:

- The original b3dm or GLB bytes and checksum.
- The extracted original GLB, without renaming objects or modifying geometry.
- The feature and batch JSON tables, including available `gml_id`, `gml:id` or
  `id` arrays. Binary tables remain in the original b3dm.
- Accumulated column-major tile transforms, RTC center and original embedded
  GLB node transforms. Simultaneous feature-table and GLB RTC centers are
  rejected until explicitly resolved.
- External image/buffer dependencies at safe relative paths, with their source
  URLs, hashes and byte counts.
- Generic `context_type`, `source_item_id`, `feature_count` and LOD counts.
  Missing batch counts remain unknown instead of being reported as zero.

Manifest paths are relative to the sample directory. Render import must
validate these hashes, apply the same atlas geographic frame, tag context
semantics separately from buildings, and preserve source identity through guide
exports. Do not feed context meshes into a building-only ID registry. Spatial
alignment, road/terrain overlap, water appearance, source-footprint coverage
and seam checks are still needed before accepting a combined render.
