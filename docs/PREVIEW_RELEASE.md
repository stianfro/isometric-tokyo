# Versioned map artifacts

Two image formats remain available. They are separate studies, not interchangeable
source/AI comparisons. Building either artifact does not claim live publication,
completed district coverage or approved geographic fidelity.

| Workflow | Images | Switching behavior |
| --- | --- | --- |
| Native pilot, `map-*` | 3072 × 3072, nine 1024-pixel cores in each layer | Source/Pixel city preserve shared framing, not pixel registration |
| Historical study, `preview-*` | Pixel city 1254 × 1254; source 1024 × 1024 | Independent image coordinates; switching resets framing |

Both use a full-screen map, one compact toolbar and a discreet provenance/credits
button. The overview starts off. Reset, sharing, optional overview and detailed
controls live in the information panel. Shared links preserve position, zoom and
the selected layer or independent view.

## Native pilot: build and inspect

The native pilot requires generated artwork and completed manual review evidence.
Those large generated assets are not committed, and no development task fabricates
or regenerates them. First follow [the native source, generation and review
workflow](NATIVE_PILOT.md). With the resulting export:

```sh
export PYTHON="$PWD/.venv/bin/python"
just map-tiles renders/pixel-city-pilot-v1/render-index.json \
  configs/shinjuku-pilot-source.json renders/pixel-city-pilot-v1/evidence.json
just map-verify
just map-dev
```

`map-dev` prints a local development address without opening a browser. It verifies
the selected native release instead of rebuilding the old study. The pilot covers
the same original bounded Shinjuku camera view at higher native image detail, not
an enlarged geographic area. Nine separate 1254-pixel generation windows supply
exact non-overlapping 1024-pixel crops. Separately recorded bounded repairs can
replace exact regions identified in review, with no upscale, blending or feathering.

The Source toggle keeps framing while making clear that AI may change building
edges, streets and vegetation. Fine facades, roofs, planting and park paths are
artistic. Seam/source comparison review never sets geographic or artwork approval.

```sh
just check
just viewer-e2e
just map-package
```

`map-package` verifies the native image release, builds browser assets with
unrelated public directories disabled, and packages only the selected image
version. It prints the immutable bundle directory, ZIP path and archive SHA-256
under `renders/web-releases/`.

## Historical committed study: quick start

After `just bootstrap`, the older study remains reproducible without Blender,
new geographic downloads or image-generation calls:

```sh
export PYTHON="$PWD/.venv/bin/python"
just preview-dev
```

This creates lossless native-resolution WebP tiles and lower image levels from
the committed PNG studies. It opens the 1254-pixel Pixel city image and the
1024-pixel source as independent views. Their dimensions are not rescaled to
pretend that they are registered. A view change resets framing; shared links and
browser history retain the chosen view. `just viewer-dev` opens the still earlier
aligned source/geometry atlas.

```sh
just preview-package
just preview-verify
```

**Selection matters:** `preview-*` tasks rebuild or verify the historical study
in the same preview output directory used by the native pilot. To return to the
native map afterward, run `map-tiles` with its reviewed export, then `map-verify`.
Do not substitute `preview-package` for `map-package` when packaging the native
pilot. The native tasks reject an incompatible selection rather than silently
reactivating the older study.

## Image and bundle identities

Each native image release records its source config, exact source/art cores,
full native generations, prompts, review evidence, notices, exporter code and
image-tool versions. The exporter verifies each art core against its generation
crop, each source core against the source evidence, and the final assembled RGB
pixels against the recorded review bindings. These checks prove pixel provenance,
not that a review decision is correct or that geography is accurate.

The historical study's identity instead covers its explicit public inputs,
original PNG checksums, notices, preferred-direction record, exporter code, tile
size and Pillow/WebP versions. Each image version lives at
`releases/RELEASE_ID/` below the preview output.

Native-resolution pyramid tiles preserve their source pixels. Lower levels are
checked against the expected resampling. Assets have size and SHA-256 records;
DZI descriptors must match their layers and rendered bounds. A new version is
built and verified before the active `manifest.json` is atomically replaced.
Existing versions are not overwritten. Repeating an unchanged build verifies and
reuses it; corruption fails rather than being silently repaired.

The complete web bundle has a separate content-derived ID covering the built
app, selected image release, notices and software licences. Its ZIP has
deterministic ordering and timestamps. It includes source/generation evidence,
source notices and the adapted ODbL vegetation database. Unselected cached image
versions, review-tool state and raw geographic caches are not app inputs.

The standalone verifier supports both image formats despite its historical task
name:

```sh
just preview-package-verify renders/web-releases/WEB_RELEASE_ID
```

Selecting a complete prior bundle retains that prior app and image version
together, rather than mixing old browser code with new manifests.

## Viewer contracts

The native pilot uses a direct atlas manifest with `styled` first and `textured`
second. It has shared image bounds and `comparison_mode` set to
`shared-framing-unverified` or `reviewed-approximate`. Neither value permits exact
geographic registration. The selected layer determines whether the visible
credits label says AI preview or Source. The overview remains off by default.

The historical root catalog uses `kind: atlas_collection`,
`default_view: pixel-city` and relative child-manifest paths. Each child retains
its own dimensions and DZI contract. Catalog paths stay below the catalog URL.
The viewer rejects an AI-styled image mislabeled as an unstyled source, and
illustration previews must remain unapproved and geographically unverified.

The preferred aesthetic is recorded in `configs/art-direction.json`. Historical
`ai-studies.json` retains the original study's generation provenance and review
status. Later work does not rewrite that historical record.

## Verification limits

Automated checks cover deterministic packaging, native pixels, review bindings,
metadata leakage, symlinks, missing/unexpected files, tampering, input changes,
relative notice paths, shared links, mobile navigation and error recovery. Manual
review separately assesses all 12 joins, four junctions and source composition
for the native pilot. Neither type of check proves surveyed accuracy, complete
city coverage or performance on a named physical phone. No production-art
approval follows from a passing build or an approximate comparison review.
