# Native Shinjuku pilot results

Checked 2026-10-21. This is a higher-detail illustration of the existing bounded
view, not more geographic coverage or a completed Shinjuku district.

## Artwork and review

- Native canvas: 3072 × 3072 pixels, nine 1024 × 1024 cores.
- Nine native 1254 × 1254 model outputs, with exact integer crops.
- One additional native generation repaired a clipped rooftop detail through an
  exact 260 × 120 pixel overwrite. No pixels outside that rectangle changed.
- No artwork upscaling, blending or feathering. Lower-resolution viewing levels
  are derived from the completed native image.
- All 12 internal joins and four junctions passed visual inspection. The repair
  perimeter was inspected separately. Review records bind the actual final RGB
  pixels, not just filenames or a generic approval note.
- The source frame remained unchanged. The same framing enables approximate
  comparison, not pixel registration or a surveyed-accuracy claim.
- Facades, roof details, some planting and park paths are artistic. Missing source
  textures are not proof of the illustrated materials. Neither geographic fidelity
  nor final owner artwork approval is inferred from these checks.
- The image tool did not expose a precise model version or per-call cost. Those
  values remain unknown rather than being estimated as actual expenditure.

## Downloadable artifact

The [native pilot release](https://github.com/stianfro/isometric-tokyo/releases/tag/shinjuku-pilot-v1)
contains the complete web bundle, native illustration PNG, visual review,
source notices and checksums. The bundle retains original generations, final
source/art cores, the ordered repair, prompts and bound review evidence.

| Item | Value |
| --- | --- |
| Image release | `shinjuku-map-ad3879b1fc5e3769d49c9c08` |
| Web bundle | `web-ff90eabf58d2ed8b35719925` |
| Image tiles | 404 across artwork and Source |
| Bundle files | 456 |
| Uncompressed bundle | 108,062,718 bytes |
| ZIP | 108,175,714 bytes |
| ZIP SHA-256 | `98a6e8ceaaa0dcf47fd9d7b815f78a716e31e4222495b30114caf639b091a883` |
| Native illustration PNG SHA-256 | `0cd5f6c2012da240467e5f899da8651669d02346df759891b2b315b5be322059` |

After extracting the ZIP, use `just preview-package-verify` with the extracted
bundle directory. The verifier checks native reconstruction, final review
bindings, tiles, notices and the complete file inventory. It does not replace
visual review. See [the workflow](NATIVE_PILOT.md) for source rendering and new
generation, and [artifact formats](PREVIEW_RELEASE.md) for packaging.

## Browser measurements

Cold first-view encoded image bytes from the exact bundle, with no original PNG
or hidden Source preload:

| Viewport | Image requests | Image bytes | 3 MiB imagery target |
| --- | --- | --- | --- |
| Desktop, 1440 × 1000 CSS pixels, DPR 1 | 36 | 4,312,082 | Not met |
| Pixel 7 emulation, DPR 2.75 | 24 | 2,872,296 | Met |

The native pilot defers one extra resolution level on displays with DPR at least
2, then loads native detail on zoom. This reduces the tested mobile image payload
from 7,538,886 bytes while preserving standard-density desktop sharpness. A
blanket lower-detail setting was rejected because it softened the desktop view.
These are image-byte measurements, not total-page payloads, physical-phone tests
or throttled-network latency benchmarks. Desktop payload reduction remains open.

The actual artifact passed desktop/mobile checks for the minimal interface,
native-detail zoom, Source switching with preserved framing, shared URL reload,
notices, labels and a strict content-security policy, with no browser errors or
failed asset requests.

## Verification

All lints passed. The final checks passed 325 Python tests, 89 viewer tests,
6 review-model tests and 48 automated desktop/mobile browser tests, 468 total.
Standalone package and ZIP verification passed. Every image/provenance asset
remained byte-identical across the final viewer loading adjustment. No remote
CI run is claimed.

Original code is MIT. Geographic data and derived artwork keep their separate
[attribution and source terms](../THIRD_PARTY_NOTICES.md).
