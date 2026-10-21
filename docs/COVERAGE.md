# Bounded source coverage

The initial 800 m geographic sample was smaller than the ground visible through
its 2400 px isometric camera. Downloading every tile inside that old sample did
not make the image complete. Elevated buildings and their shadows also need
inputs beyond the visible ground.

`source_coverage.py` makes a reproducible, local-study selection plan for the
unchanged camera in `configs/shinjuku.json`. It preserves every selected source
tile, rather than clipping geometry at image chunk edges.

## What the audit means

The report keeps these statements separate:

- **Tile selection complete:** every tile required by the recorded metadata,
  camera, heights and directional-sun envelope is present, with matching raw
  checksums, transforms and bounding volumes.
- **Terrain surface support complete:** the supplied terrain triangles cover a
  rectangle containing the actual visible ground. The audit checks triangle
  orientation, edge incidence, interior holes, total projected area and the
  actual ENU height range. This does not establish survey accuracy.
- **Orthophoto support complete:** the checked XYZ grid and checksummed mosaic
  cover that ground footprint. This does not make image dates match buildings.
- **Real-world source coverage:** remains unverified. Missing upstream features,
  inaccurate bounds, differing acquisition dates, unmapped vegetation and model
  omissions do not disappear because a download selection is complete.

No broad `source_coverage_verified` flag is set to true by this tool. Image art
approval is a separate review decision.

## Selection method

1. Read the verified 23-ward catalog snapshot. Require one distinct textured LOD2
   building tileset per ward, confirmed package identity and source terms. The
   selected files can contain mixed feature LODs; the selector does not add an
   overlapping LOD1 layer.
2. Read and cache all 23 explicit region-based tilesets, retaining raw metadata
   hashes. External, implicit or unsupported bounding-volume adapters fail.
3. Expand the canonical image bounds by its 32 px render overscan. Sweep the
   configured ENU height interval, currently -100 to 650 m, through the exact
   orthographic camera. Add the configured 600 m broad-phase source margin.
4. Bound each curved WGS84 tile region in ENU using corner transforms plus a
   conservative angular interpolation-error allowance. Project its complete
   volume into the image, including offscreen roofs.
5. Include tiles that can cast a directional-sun shadow onto any point in the
   full configured height interval, including building receivers. The recorded
   XYZ sun rotation is `(20, -25, -35)` degrees. Its 12 degree angular diameter
   gives a 6 degree direction-cone half-angle. Exact cone component-slope bounds
   and independent coordinate intervals deliberately overselect blockers. The
   maximum horizontal reach is 577.76 m, inside the 600 m broad-phase margin.
6. Retain entire selected b3dm files, extracted unchanged GLBs, original feature
   tables, accumulated transforms, RTC centers and per-ward terms. Reject
   duplicate `gml_id` values rather than drawing overlapping ward copies.

Blender defines the SUN angle as an angular diameter, not a cone half-angle.
See the [official light documentation](https://docs.blender.org/manual/en/4.0/render/lights/light_object.html).

This is a small-area adapter, not a planet-scale proof. Geographic envelope
conversion samples the local inverse frame densely with a 2 m guard and rejects
extents over 20 km or origins beyond 60 degrees latitude. Region curvature bounds reject wide, wrapped or nonfinite
regions. Official metadata bounds are assumed to contain their payload geometry;
this audit does not independently parse every GLB vertex. The finite directional
sun selection does not prove bounds for diffuse/global illumination. Changing
camera extent, height interval, sun direction or increasing its angle requires a
new plan. Lower angular diameters with the same direction are contained by the
recorded cone.

## Reproduction

Use the `coverage` task for each stage:

```sh
just coverage plan
just coverage probe
just coverage acquire --output data/coverage-buildings --max-bytes 250000000
just coverage audit --manifest data/coverage-buildings/manifest.json \
  --terrain data/terrain-study-v2/mesh.json \
  --ground data/ground-study-v2/manifest.json \
  --output .cache/coverage/audit.json
```

The default plan is `.cache/coverage/plan.json`; source and HEAD metadata caches
are content-checked in `.cache/coverage-metadata`. Plan identities include the
canonical config, inventory checksum, every source metadata checksum and all
selection parameters. HEAD lengths are estimates only. Actual source and network
response bodies have separate hard budgets during acquisition; partial failed
requests still consume network budget. Requests and tile counts are bounded.
Cached original sample files are reused only after checksum verification.
Output replacement is staged, with the previous marked directory retained if a
new acquisition fails. No whole-ward archives are downloaded.

The expanded orthophoto command is:

```sh
just ground-study
```

It selects 132 native z17 tiles for west/south/east/north bounds
`[139.67688, 35.67835, 139.70549, 35.70169]`, under a 50 MB hard response-body cap.
The result is `data/ground-study-v2/manifest.json`, a 3072 by 2816 px mosaic with
source tile and mosaic checksums. The completed fetch used 20,670,452 response-body
bytes, and the final audit compared every mosaic tile against the checked source
pixels. This is native z17 imagery, not invented z18
resolution. Individual images are limited to 1 MB, decoded as 256 by 256 pixels,
and cached images require matching recorded checksums. HTTP redirects are not
followed. Budgets count response bodies, not HTTP headers or transport overhead.

## Current study evidence

The final plan selects **138 building tiles**: 122 direct-view region volumes and
16 additional shadow-only volumes. Whole tiles retain 16,859 unique source
`gml_id` values, including features outside the visible image.

| Catalog ward | Required tiles | Measured payload bytes |
| --- | ---: | ---: |
| Shinjuku | 62 | 107,729,452 |
| Shibuya | 61 | 36,465,908 |
| Nakano | 15 | 53,329,216 |
| Total | 138 | 197,524,576 |

Of that total, 45,242,192 bytes are reusable original sample payloads. The HEAD
estimate for new geometry is 152,282,384 bytes. Completed acquisition consumed exactly 152,282,384 new network response-body
bytes, with 204,328,626 source bytes including all 23 tileset metadata files.
The final manifest records both figures and a 250 MB hard cap.
The other 20 ward tilesets were examined and have no required tiles under this
specific view and sun plan, not under all possible Tokyo views.

The separate terrain patch uses 30 official native z15 source tiles. Its final
native-TIN mesh has 47,914 vertices and 94,188 triangles. It retains native interior
vertices rather than resampling heights onto a regular grid. Its checked ENU-up
range is approximately 53.334 to 82.598 m, inside the planning interval 0 to 150 m
for visible ground. The required ground bounds, including overscan and numerical
guard, are approximately
`[139.677134, 35.678657, 139.705114, 35.701481]`.
The supplied terrain rectangle contains those bounds with no open interior edges.
This ground-height interval is not used to omit shadows on elevated buildings.

Expanded road, water and vegetation context have separate manifests, editions
and limitations. OSM-derived vegetation is a labeled fallback, not PLATEAU survey
data. Official catalog vegetation availability alone is not evidence that its
geometry was loaded. Render provenance must name the actual loaded sources and
retain any remaining upstream or context gaps.
