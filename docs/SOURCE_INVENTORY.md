# Tokyo source inventory

The checked-in `configs/sources-tokyo-2025.snapshot.json` records official
metadata observed on **2026-10-21**. All 23 special wards have confirmed FY2025
CKAN package IDs and building tileset items. The snapshot includes 353 catalog
items with direct URLs, per-item IDs, format, LOD and texture flags where supplied.
It is a source-selection inventory, not a claim that all of Tokyo is ready to
render.

## What the metadata establishes

| Catalog evidence | Wards |
| --- | ---: |
| Confirmed public FY2025 CKAN package | 23 / 23 |
| Building 3D Tiles, including an item marked textured | 23 / 23 |
| Building LOD1 and LOD2 items | 23 / 23 |
| Water 3D Tiles | 23 / 23 |
| Road 3D Tiles | 18 / 23 |
| Bridge 3D Tiles | 18 / 23 |
| Vegetation 3D Tiles | 9 / 23 |
| Road MVT/reference items | 23 / 23 |
| Park, railway, station and boundary reference items | 23 / 23 |
| Terrain (`dem`) listed in CityGML feature types | 23 / 23 |

All 190 listed 3D Tiles items in the selected feature categories report format
version 1.0. This does not verify their tile hierarchy, bounding volumes,
transforms or feature payload compatibility. The remaining catalog items are
reference/vector formats. The selected categories are buildings, roads,
vegetation, water, bridges, terrain, railway references, parks, stations and
boundaries, not every PLATEAU feature category.

The nine wards with listed vegetation 3D Tiles are Chiyoda, Chuo, Minato,
Shinjuku, Taito, Sumida, Koto, Shibuya and Toshima. Bunkyo, Shinagawa, Meguro,
Ota, Setagaya, Nakano, Suginami, Kita, Arakawa, Itabashi, Nerima, Adachi,
Katsushika and Edogawa need further vegetation-source work. Absence from this
filtered catalog is not proof that no compatible vegetation data exists.

Every package reports the PLATEAU site-policy licence identifier. Retain the
actual resource terms and attribution checks when importing a new layer.
The code's MIT licence does not replace geographic-data terms.

## Confirmed package identities

The snapshot also records each package's CKAN UUID, resource UUIDs, exact URLs,
licence URL and metadata-modification timestamp. These names are discovered from
the official portal and cross-checked with CKAN and the CityGML catalog, not
constructed and assumed to exist.

| Code | Ward | Confirmed FY2025 package |
| --- | --- | --- |
| 13101 | Chiyoda | [plateau-13101-chiyoda-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13101-chiyoda-ku-2025) |
| 13102 | Chuo | [plateau-13102-chuo-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13102-chuo-ku-2025) |
| 13103 | Minato | [plateau-13103-minato-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13103-minato-ku-2025) |
| 13104 | Shinjuku | [plateau-13104-shinjuku-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025) |
| 13105 | Bunkyo | [plateau-13105-bunkyo-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13105-bunkyo-ku-2025) |
| 13106 | Taito | [plateau-13106-taito-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13106-taito-ku-2025) |
| 13107 | Sumida | [plateau-13107-sumida-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13107-sumida-ku-2025) |
| 13108 | Koto | [plateau-13108-koto-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13108-koto-ku-2025) |
| 13109 | Shinagawa | [plateau-13109-shinagawa-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13109-shinagawa-ku-2025) |
| 13110 | Meguro | [plateau-13110-meguro-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13110-meguro-ku-2025) |
| 13111 | Ota | [plateau-13111-ota-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13111-ota-ku-2025) |
| 13112 | Setagaya | [plateau-13112-setagaya-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13112-setagaya-ku-2025) |
| 13113 | Shibuya | [plateau-13113-shibuya-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13113-shibuya-ku-2025) |
| 13114 | Nakano | [plateau-13114-nakano-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13114-nakano-ku-2025) |
| 13115 | Suginami | [plateau-13115-suginami-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13115-suginami-ku-2025) |
| 13116 | Toshima | [plateau-13116-toshima-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13116-toshima-ku-2025) |
| 13117 | Kita | [plateau-13117-kita-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13117-kita-ku-2025) |
| 13118 | Arakawa | [plateau-13118-arakawa-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13118-arakawa-ku-2025) |
| 13119 | Itabashi | [plateau-13119-itabashi-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13119-itabashi-ku-2025) |
| 13120 | Nerima | [plateau-13120-nerima-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13120-nerima-ku-2025) |
| 13121 | Adachi | [plateau-13121-adachi-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13121-adachi-ku-2025) |
| 13122 | Katsushika | [plateau-13122-katsushika-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13122-katsushika-ku-2025) |
| 13123 | Edogawa | [plateau-13123-edogawa-ku-2025](https://www.geospatial.jp/ckan/dataset/plateau-13123-edogawa-ku-2025) |

## Immediate rendering implications

Shinjuku has separate PlantCover and SolitaryVegetationObject items at LOD1 and
LOD3, roads at LOD3, water at LOD1, and bridges at LOD2. Catalog availability does
not establish coverage of the source-study bounds. The bounded probe in
[CONTEXT.md](CONTEXT.md) finds no intersecting vegetation tiles there at LOD1 or
LOD3. Nearby areas need separate coverage tests.

The snapshot pins direct item URLs and their associated dataset IDs. It also
retains the API's composite URLs. Composite endpoints introduce external tileset
references, so do not feed them into a sampler that only accepts terminal local
hierarchies without adding and testing that adapter. Do not replace pinned
editions with `latest` during reproducible builds.

Higher building LOD entries in Minato, Taito and Sumida are local detail
opportunities, not evidence of ward-wide LOD3/LOD4. Preserve mixed-LOD reporting.
Reference railway GeoJSON and station CZML are useful for alignment and place
navigation, but are not complete railway meshes.

## What remains unknown

Every ward deliberately has `ready_for_district_production: false` until spatial
checks pass. Acquisition dates, per-building texture completeness and source
boundary quality remain unknown. Source-format adapters are not marked verified
from metadata alone. Terrain being listed in CityGML does not establish that the
separate terrain service covers every intended render footprint.

An FY2025 catalog item can point to an older source asset. For example, Shinjuku's
park reference URL contains `2023`. Metadata-modification dates and edition years
must not be presented as photography-capture dates. Original capture-date records
require inspecting the linked source metadata, not guessing from a filename.

Next evidence needed before expansion:

1. Bounded tileset-tree probes for each selected building and supplementary item.
2. Spatial coverage and mixed-LOD/texture checks across representative city blocks.
3. Source-metadata acquisition dates and coordinate/height conventions.
4. Orthophoto and terrain availability over the required tower/shadow footprint.
5. Cross-ward duplicate-building, elevation and source-date checks.
6. Compatible vegetation treatment where the selected catalog has no 3D trees.

## Repeating the inventory

`just source-inventory` refreshes only metadata. `just source-inventory-offline`
rebuilds the snapshot from checksummed cached responses with no network access.
The script is `scripts/inventory_sources.py`; configuration is
`configs/sources-tokyo-wards.json`. Changing the edition requires another confirmed
inventory, not editing dates in the generated snapshot.

The initial run used 25 requests and downloaded **4,396,193 bytes of metadata**:
one portal page, one filtered GraphQL query and 23 CKAN package responses. It did
not download model archives, building payloads, imagery or terrain. Fetching is
limited to official metadata endpoints, two attempts per request, 52 attempts in
total, two million bytes per response and twenty million bytes per run. Failed
refreshes leave the previous snapshot intact. Cache response hashes and observed
UTC times are retained as evidence; rebuilding offline does not refresh those
observation times.

The snapshot omits raw descriptions, embedded Markdown and contact fields.
The offline tests cover exact ward membership, discovered-package identity,
GraphQL errors, unexpected editions, cache corruption, retry/byte limits, safe
metadata fields and the distinction between catalog presence and usable coverage.

## Primary references

- [Official PLATEAU data portal](https://front.geospatial.jp/plateau_portal_site/).
- [PLATEAU 3D Tiles and catalog documentation](https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/).
- [GraphQL schema](https://docs.plateauview.mlit.go.jp/api/graphql/schema/) and
  [API documentation](https://docs.plateauview.mlit.go.jp/api/graphql/).
- [Composite tileset endpoint](https://docs.plateauview.mlit.go.jp/api/rest/operations/datacatalog3dtilesspectilesetjson/).
- [PLATEAU source terms](https://www.mlit.go.jp/plateau/site-policy/).
