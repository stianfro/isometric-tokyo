# Native Shinjuku expansion results

Checked 2026-10-22. The 5 × 5 map adds sixteen neighboring cores to the
nine-core pilot at the same native detail scale. It covers about 2.78 times the
pilot's image area, not all of Shinjuku or Tokyo. Image area is not ground area.

## Artwork and review

- Native canvas: **5120 × 5120**, with 25 exact 1024 × 1024 cores.
- Sixteen new native 1254 × 1254 generations, retaining nine original cores.
- Four new repair generations corrected the park road and clipped crowns,
  northern facade patterns, southern facade patterns and western road surface.
- All 40 joins, 16 junctions and the four sides of every repair passed visual
  inspection. Final reviews bind the actual RGB pixels.
- No artwork upscaling, blending or feathering. An independent reconstruction
  from the original generations and exact repair crops matches every final core.
- All 6,553,600 pixels in the protected parent interior remain unchanged.
  The 152,853 changed parent pixels lie only inside the four recorded repair
  rectangles, confined to the outer band. The original release is untouched.
- All 39 retained parent-proof files are byte-identical, including the historical
  rooftop repair, prompts, notices and older adapted vegetation database.
- Total history: 25 main generations and five repairs, of which 16 main
  generations and four repairs belong to this expansion.
- Exact model version and per-call cost were not exposed, so remain unknown.

The two facade repairs preserve silhouettes and remove image-horizontal
pattern changes. One northern tower retains different upper and lower materials,
with the boundary following its projected floor plane rather than a tile cut.
Local canopy styles vary. Passing seam review does not establish final owner
artwork approval or geographic accuracy.

## Source support

The native source uses the original camera, pixel density and geographic origin.
Global bounds are `[-1024,-1024,4096,4096]`; the original pixel origin remains
`[1536,1536]`. The source and structural images are natively 5120 pixels square.

- 259 building meshes across Shinjuku, Shibuya, Nakano and Suginami, with
  38,973 genuine building identities and no unresolved building meshes.
- 113 official context meshes, sourced from Shinjuku only. This does not
  establish complete road, water or vegetation context in neighboring wards.
- 324 native z17 orthophoto tiles and 80 native z15 terrain tiles.
- All 275 derived source, structural, context and guide assets independently
  match the monolithic renders. There are zero background guide pixels.
- The 66,217-feature adapted OSM database is separately attributed and retained.
  The render uses 227 mapped-point crowns and 1,349 woodland-fill crowns.
  Their dimensions and fill are artistic, not a tree survey.
- Native render and guide extraction took about 36.1 minutes in the recorded run.

Expanded source geometry changes some lighting and occlusion in the old overlap.
Of 9,437,184 overlap pixels, 9,436,904 retain the same stable feature identity.
Source RGB is not identical to the earlier render, and this is not hidden by
altering the historical source or artwork.

The source-support receipt checks the bounded inputs actually used. It does not
set source completeness, geographic-fidelity or production approval flags.
Peripheral buildings often lack source textures. Facades, roofs, road markings,
individual tree crowns and park paths remain artistic interpretations. The
source comparison is explicitly approximate.

## Downloadable artifact

The [expansion release](https://github.com/stianfro/isometric-tokyo/releases/tag/shinjuku-expansion-v1)
contains the complete web bundle, native illustration, reduced overview, final
visual review, source notices and checksums. The web bundle retains both Source
and Pixel city layers, native source-support proofs, original generations,
ordered repairs, prompts and both vegetation snapshots.

| Item | Value |
| --- | --- |
| Image release | `shinjuku-map-7941a863996160a36b4bd53d` |
| Web bundle | `web-1b127a1c4c8240fc40af2b9b` |
| Image tiles | 1,094 across artwork and Source |
| Bundle files | 1,229 |
| Uncompressed bundle | 349,969,566 bytes |
| ZIP | 350,277,712 bytes |
| ZIP SHA-256 | `72b5f61cbb56da2474f377dcdd1c974d4e5c8c07d9a09c494544a3f0912a6920` |
| Native illustration PNG SHA-256 | `36378d161c9bc375d298f08dc192818fcdf428ffa2849db2a137c7f420e794b5` |

After extracting the ZIP, run `just preview-package-verify` with the extracted
bundle directory. The verifier checks reconstruction, reviews, source proofs,
tiles, notices and inventory. It does not replace visual inspection.

The [generation workflow](NATIVE_EXPANSION.md) explains the bounded acquisition,
native rendering, original-art protection and review gates. The
[earlier pilot](NATIVE_PILOT_RESULTS.md) remains available separately.

## Viewer and measured loading

The interface stays minimal. Old native-map links retain their framing.
New links use the stable `shinjuku-native-v1` global frame, including negative
coordinates. Home keeps the original center and scale instead of zooming out to
fit the entire expanded canvas. Source switching preserves shared framing,
not geographic pixel registration.

Cold first-view encoded imagery from the exact bundle:

| Viewport | Tile requests | Image bytes | 3 MiB imagery target |
| --- | --- | --- | --- |
| Desktop, 1440 × 1000 CSS pixels, DPR 1 | 42 | 5,104,556 | Not met |
| Pixel 7 emulation, DPR 2.75 | 28 | 3,425,226 | Not met |

The larger extent loads additional edge tiles at the original home view.
Home uses level 12; exploration requests native level 13 detail. No original PNG
or hidden Source preload occurs during normal navigation. These are local
encoded-image measurements, not physical-phone or network-latency benchmarks.
Reducing first-view payload without blurring the map remains open.

Both devices passed 27 actual-bundle checks, including stable links, history,
negative-coordinate navigation, station-frontier detail, Source framing,
minimal controls, credits and content-security policy. Screenshots were inspected,
with no blank tiles, failed requests, external requests or browser errors.

## Verification

All lints passed, alongside 357 Python tests, 126 viewer unit tests, six
review-model tests and 60 headless desktop/mobile fixture tests. Actual-bundle
checks were separate from those fixtures. Native-map integrity, standalone
package verification and YAML validation also passed. No remote CI run is claimed.

Original code is MIT. Geographic data and derived artwork retain their separate
[attribution and source terms](../THIRD_PARTY_NOTICES.md).

