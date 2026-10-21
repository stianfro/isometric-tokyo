# Native 5 × 5 Shinjuku expansion

This extends the [nine-core pilot](NATIVE_PILOT.md) with sixteen new 1024-pixel
cores. It is new geography at the same image scale, not a larger copy of the
existing picture. The final canvas is 5120 × 5120 pixels, or about 2.78 times the
pilot's image area. Image area is not a measurement of ground area.

The expansion is bounded. It is not all of Shinjuku, and it does not include the
Kabukicho center. Approximate planning points place Shinjuku Station in the first
new frontier and part of the Gyoen area near the lower edge. These are planning
hints, not verified points of interest or navigation coordinates.

## Fixed map coordinates

The original camera, lighting, 64-sample setting, projection, geographic origin
and pixel density stay unchanged. The old pixel origin remains `[1536,1536]`.
It must not be moved to the center of the expanded image.

| Property | Native pilot | Expansion |
| --- | --- | --- |
| Global pixel bounds | `[0,0,3072,3072]` | `[-1024,-1024,4096,4096]` |
| Core coordinates | x/y 0 through 2 | x/y -1 through 3 |
| Final image size | 3072 × 3072 | 5120 × 5120 |
| Native core size | 1024 × 1024 | 1024 × 1024 |
| New main generations | 9 | 16 |
| New bounded repair allowance | 4 | 8 |

Global map coordinates may be negative. PNG coordinates are nonnegative and
local: subtract the global bounds origin to get an image position. In this
expansion, local position equals global position plus 1024 on both axes.

The nine accepted parent cores are copied from their immutable image release.
Their original generations, exact rooftop repair, reviews, notices and adapted
vegetation database remain evidence. The new process does not regenerate those
cores. A separately recorded repair may change an old border only when needed
to fix a reviewed defect; it must name the exact replacement rectangle.
New repairs touching old artwork must cross the outer pilot perimeter into new
cells and stay outside the protected inner rectangle `[256,256,2816,2816]`.
This keeps parent interiors unchanged and limits any old-art changes to a
256-pixel outer band. The original historical rooftop repair remains part of
the immutable parent, not a new exception to this rule.

## 1. Acquire and check expanded sources

`configs/shinjuku-expansion-source.json` fixes the view. The selection retains
whole building tiles, including the surrounding geometry needed for occlusion
and the recorded directional-sun envelope. Buildings, terrain, orthophotos and
mapped vegetation have independent checksums and bounds.

```sh
just expansion-source-plan
just coverage probe --plan .cache/coverage/expansion-plan.json \
  --reuse data/coverage-buildings/manifest.json \
  --output .cache/coverage/expansion-sizes.json
just coverage acquire --plan .cache/coverage/expansion-plan.json \
  --reuse data/coverage-buildings/manifest.json --max-bytes 500000000 \
  --output data/expansion-buildings-v1
```

Download the selected building plan using the bounded `coverage acquire` task,
with a fresh destination and verified cache reuse. The selected inputs are:

```text
data/expansion-buildings-v1/manifest.json
data/ground-expansion-v1/manifest.json
data/terrain-expansion-v1/mesh.json
data/context-expansion-v1/manifest.json
data/vegetation-expansion-v1/manifest.json
```

The ground-support bounds are
`[139.66793550852506,35.67102788758345,139.71446800867096,35.70898244748989]`.
Use native z17 orthophotos and native z15 terrain, rather than upscaling earlier
rasters. Acquisition is capped separately at 500 MB for building payloads,
100 MB for official context, 75 MB for orthophotos and 12 MB for terrain.
The vegetation adapter splits the same geographic bounds into four bounded
queries, with a separate response-body budget and recorded failed attempts.

```sh
just ground-fetch --bbox 139.66793550852506 35.67102788758345 \
  139.71446800867096 35.70898244748989 --zoom 17 --max-bytes 75000000 \
  --output data/ground-expansion-v1
just terrain-fetch --bbox 139.66793550852506 35.67102788758345 \
  139.71446800867096 35.70898244748989 --zoom 15 --method native \
  --max-tiles 80 --max-source-bytes 12000000 --output data/terrain-expansion-v1
just context-fetch-bounded --config configs/shinjuku-expansion-source.json \
  --output data/context-expansion-v1 --types tran wtr veg \
  --max-tiles 256 --max-bytes 100000000
just expansion-vegetation --public-bundle datasets/shinjuku-expansion-vegetation
just coverage audit --plan .cache/coverage/expansion-plan.json \
  --manifest data/expansion-buildings-v1/manifest.json \
  --terrain data/terrain-expansion-v1/mesh.json \
  --ground data/ground-expansion-v1/manifest.json \
  --output .cache/coverage/expansion-audit.json
```

The public adapted vegetation snapshot belongs in
`datasets/shinjuku-expansion-vegetation/`. Do not overwrite the pilot's older
database. Keep the actual source timestamp and original feature identities;
omit contributor usernames, numeric user IDs and changeset IDs from public data.
Each query records its actual source timestamp. The acquisition records their
snapshot range, not a claim that all four queries describe one instant. Conflicting
versions or geometry for the same original identity fail the merge. An attempted
historical-snapshot query exceeded the upstream memory limit; it was rejected,
not treated as a complete result or retried with unlimited server memory.

Official road, water and vegetation context remains sourced from the Shinjuku
catalog. Loading buildings from neighboring wards does not establish complete
neighboring-ward context. OSM tree positions are mapped data; generated crown
dimensions and woodland fill are artistic. A park boundary is not permission to
fill all its open space with trees. See [coverage limits](COVERAGE.md),
[vegetation rules](VEGETATION.md) and [source notices](../THIRD_PARTY_NOTICES.md).

## 2. Render the native source and overlap audit

```sh
just expansion-source renders/shinjuku-expansion-source-v1
just expansion-source-test
```

This renders a native 5120-pixel source reference, an Atlas ink structural image
and exact guide channels. Finalization partitions the monolithic outputs into
25 cores and context windows, verifies exact reassembly and writes
`expansion-source.json`. Global bounds and local crop bounds are separate fields.
Finalization also requires the completed building-selection, terrain-support
and orthophoto audit to match the exact rendered input hashes. Its retained
`source-support.json` is evidence for those bounded checks, not city-wide source
completeness or survey accuracy.

The receipt also compares the central 3072-pixel source overlap with the retained
pilot source. Expanded geometry, terrain and vegetation can change illumination
or occlusion even with an identical projection. Report those differences instead
of claiming the old source pixels are unchanged. Old artwork remains intact
unless a separately recorded repair changes it.

## 3. Extend the accepted artwork frontier

Use the complete, verified parent image release, not an unverified screenshot or
the earlier 1254-pixel direction study:

```sh
just art-pilot-grid expansion-init --directory renders/pixel-city-expansion-v1 \
  --parent-release viewer/public/preview/releases/shinjuku-map-ad3879b1fc5e3769d49c9c08 \
  --source renders/shinjuku-expansion-source-v1/source-reference.png \
  --structure renders/shinjuku-expansion-source-v1/atlas-ink.png \
  --style previews/shinjuku-art/ai-pixel-city.png \
  --order configs/shinjuku-expansion-order.json
just art-pilot-grid prepare --directory renders/pixel-city-expansion-v1 --id x3_y2
```

The first three new cells are `x3_y2`, `x3_y1` and `x3_y3`. Inspect their actual
source, artwork and joins before continuing around the remaining frontier.
Every new cell shares an edge with accepted artwork. The checked order is in
`configs/shinjuku-expansion-order.json`.

Preparation makes two exact 1254-pixel windows: source and structure with
accepted neighboring artwork. Their recorded anchor rectangles tell the image
model which illustrated edges to continue. The third reference is the preferred
Pixel city style, not geographic placement guidance. Use
`configs/pixel-city-prompt.txt` as the base, saving the exact amended prompt for
each external generation.

```sh
just art-pilot-grid ingest --directory renders/pixel-city-expansion-v1 \
  --id x3_y2 --image renders/generated-x3_y2.png \
  --prompt renders/pixel-city-expansion-v1/inputs/x3_y2-prompt.txt
just art-pilot-grid assemble --directory renders/pixel-city-expansion-v1 \
  --allow-partial
```

The generated image and prompt are external inputs, not outputs of `prepare`.
Ingestion requires a native 1254 × 1254 image. An exact integer crop supplies its
1024 × 1024 core. No scaling, blending or feathering hides a transition. Keep all
original generations and repair outputs. Unknown model versions and costs remain
unknown rather than estimated as observed facts.

## 4. Review every final join and junction

After all sixteen new cores are present:

```sh
just art-pilot-grid assemble --directory renders/pixel-city-expansion-v1
just art-pilot-grid review-template --directory renders/pixel-city-expansion-v1 \
  > renders/pixel-city-expansion-v1/review.json
```

The final atlas has **40 joins and 16 four-way junctions**. Parent review entries
carry forward only when the actual RGB region is identical. New or changed
regions begin pending. Inspect every remaining region at native scale, along
with the first-three frontier and representative source comparisons. A matching
checksum is not a visual seam review.

At most eight new repair calls can make recorded exact overwrites inside native
1254-pixel generation windows. They retain the earlier originals and historical
repair. Repairs invalidate affected review bindings and require inspection of
the final patch edges and composition. Do not turn a pending or failed seam into
a passing note merely to produce a release.

The expanded source comparison is always new, even when old art is retained.
Pixel city remains approximate, with artistic roof, facade, planting and path
details. Seam approval does not set geographic fidelity or final artwork approval.

## 5. Export, test and package

```sh
just art-pilot-grid export --directory renders/pixel-city-expansion-v1 \
  --config configs/shinjuku-expansion-source.json \
  --source-metadata renders/shinjuku-expansion-source-v1/expansion-source.json \
  --review renders/pixel-city-expansion-v1/review.json \
  --prompt configs/pixel-city-prompt.txt
just art-expansion-test
just map-tiles renders/pixel-city-expansion-v1/render-index.json \
  configs/shinjuku-expansion-source.json renders/pixel-city-expansion-v1/evidence.json
just map-verify
just check
just viewer-e2e
just map-package
```

Review the packaged desktop and mobile views as well as unit-test fixtures.
The immutable artifact retains the exact native artwork, new source reference,
parent proof, per-call records, final reviews and both attributed vegetation
snapshots. Keep the previous image and app releases available. An artifact build
alone does not establish publication.

## Stable shared views

The expanded direct atlas declares the `shinjuku-native-v1` global pixel frame.
New links record a global center and viewport span, not a fraction of the growing
canvas. Old native pilot links resolve through their original 3072-pixel bounds.
The default view keeps the old center and scale instead of zooming out to fit the
larger extent. Visitors can pan into the new area without losing the familiar
opening view or adding interface panels.

Historical independently redrawn art-study coordinates are not registered to
the native atlas. When such a link opens the direct native map, use its default
artwork frame rather than implying an exact geographic migration. Historical
collections retain their own independent link behavior.

## After this batch

Measure accepted-output generation count, repair effort, review time, artifact
size and first-view traffic before planning another ring. The current pyramid
builder is bounded but rebuilds when global bounds change; it is not yet an
incremental city-wide publishing system. Kabukicho and other connected areas
need the next source plan and their own bounded acceptance batch.
