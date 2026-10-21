# Native nine-core Shinjuku pilot

The current pilot increases image detail within the existing bounded Shinjuku
view. It does not enlarge geographic coverage, complete a district or approve
geographic fidelity. The canvas is 3072 × 3072 pixels over the original 1260 m
orthographic camera span, not a claim of a 1260 m square ground footprint.

## What the pixels represent

- One native 3072-pixel source render and one Atlas ink structural reference use
  the same camera and loaded geometry. Neither is an enlarged earlier image.
- Nine independent native 1254 × 1254 generation windows supply exact
  1024 × 1024 cores. Integer crops form a non-overlapping 3 × 3 grid; separately
  recorded bounded repairs can replace exact regions found defective in review.
- The windows include context from neighboring cores already ingested. Their
  origins are clamped inside the source image. Accepted core pixels are not
  resized, blended or feathered to hide joins.
- The source layer retains exact crops of the monolithic source render. Source
  continuity is therefore separate from the visual continuity of generated art.
- Pixel city and Source share framing. Switching keeps position and zoom, but
  does not establish pixel registration of buildings, streets or vegetation.
- Fine facades, roof equipment, planting and park paths are artistic, especially
  where source textures are absent. This is not a navigation map.

`artwork_approved`, `geographic_fidelity_approved` and broad source-completeness
approval remain false. A recorded visual comparison permits only an approximate
comparison, not a surveyed-accuracy claim. See [source coverage](COVERAGE.md) and
[vegetation provenance](VEGETATION.md) for the underlying limitations.

## 1. Render a native source

Use the Python environment created by `just bootstrap` and Blender 5.2. The
expanded source inputs must already be acquired and checked using the coverage,
terrain, context and vegetation workflows. A fresh checkout does not contain
these large files:

```text
data/coverage-buildings/manifest.json
data/ground-study-v2/manifest.json
data/terrain-study-v2/mesh.json
data/context-study-v2/manifest.json
data/vegetation/manifest.json
```

```sh
export PYTHON="$PWD/.venv/bin/python"
just pilot-source renders/shinjuku-pilot-source-v1
just pilot-source-test
```

Choose a fresh destination for another render. The recipe uses 64 Cycles samples,
four CPU threads, no denoising and no adaptive sampling. It writes
`source-reference.png`, `atlas-ink.png` and `pilot-source.json`, then source and
structural cores, clipped 256-pixel context crops, and exact categorical/numeric
guides. The guide registry preserves genuine source feature identities.

The completed source run used 138 building and 101 context meshes, preserving
16,859 building IDs and 1,083 vegetation representations. Its seven guide channels
cover every image pixel, with no background samples and no unresolved building
meshes. All 99 core/context/guide crops matched their monolithic parent exactly.
These are image and source-identity checks, not proof of real-world completeness.
The measured source, structure and guide run took about 14 minutes on four CPU
threads; other machines and input versions may differ.

## 2. Prepare generation inputs

```sh
just art-pilot-grid init --directory renders/pixel-city-pilot-v1 \
  --source renders/shinjuku-pilot-source-v1/source-reference.png \
  --structure renders/shinjuku-pilot-source-v1/atlas-ink.png \
  --style previews/shinjuku-art/ai-pixel-city.png
just art-pilot-grid prepare --directory renders/pixel-city-pilot-v1 --id x1_y1
```

The plan fixes this order:

```text
x1_y1, x1_y0, x0_y1, x2_y1, x1_y2, x0_y0, x2_y0, x0_y2, x2_y2
```

Preparation writes an exact source window, a structural/context window and their
checksums under `inputs/`. Each later window incorporates the overlapping pixels
of earlier accepted cores. The checked prompt template is
`configs/pixel-city-prompt.txt`; retain the exact prompt used for each call.

**Generation is external and non-deterministic.** These scripts do not call an
image model, regenerate the artwork or promise equivalent output from a prompt.
Use an explicitly authorized image-generation tool, retain its full native
output, and inspect that output against the prepared references. The tool may
not expose an exact model version or per-call cost; unknown values stay unknown.

## 3. Inspect and ingest each native result

For each ID in the fixed order, prepare its references, generate externally,
inspect the result, then ingest the exact native output:

```sh
just art-pilot-grid ingest --directory renders/pixel-city-pilot-v1 \
  --id x1_y1 --image renders/generated-x1_y1.png \
  --prompt configs/pixel-city-prompt.txt
```

The image path is the externally generated result, not a file created by the
previous command. If the call used an amended prompt, pass that saved prompt
instead. Ingestion rejects dimensions other than 1254 × 1254 and never resizes
artwork. It retains the full generation, records hashes and prompt text, and
extracts the prescribed 1024-pixel core. Ingestion is not final visual approval.
The ingestion command cannot overwrite an accepted record; use a fresh pilot
for a generation retry. A repair is a separate recorded output, not a replacement
for the retained original generation.

The bounded plan permits nine main calls and at most four repair calls. It does
not authorize district-wide generation. Main cores use exact crops; a repair
uses an explicitly recorded integer-pixel overwrite, not blending. Retain its
full native generation, prompt, bounds and hashes, plus the original main
generations. The release verifier reconstructs final pixels from the main crops
and ordered repair overwrites. Any repair invalidates the old review and needs
another inspection of the resulting final pixels.

## 4. Review the assembled pixels, not only isolated patches

```sh
just art-pilot-grid assemble --directory renders/pixel-city-pilot-v1
just art-pilot-grid review-template --directory renders/pixel-city-pilot-v1 \
  > renders/pixel-city-pilot-v1/review.json
```

Assembly writes the full atlas plus 12 join strips and four four-way junction
crops under `inspection/`. The review template binds the exact current source,
atlas and all 16 inspection regions by RGB hashes. Every decision starts pending.

Inspect each join and junction at native scale, then compare the full composition
and representative regions against Source. Record a specific result and note for
every region, an overall seam decision, and a separate source-comparison decision
in `review.json`. Do not mark entries passed merely because the assembler or
pixel checks succeeded. If artwork or source pixels change, the old review is
invalid and must be repeated. A seam pass does not establish geographic fidelity.

Only after that review can the release handoff be exported:

```sh
just art-pilot-grid export --directory renders/pixel-city-pilot-v1 \
  --config configs/shinjuku-pilot-source.json \
  --source-metadata renders/shinjuku-pilot-source-v1/pilot-source.json \
  --review renders/pixel-city-pilot-v1/review.json \
  --prompt configs/pixel-city-prompt.txt
just art-pilot-test
```

Export requires complete matching source evidence, unchanged native source and
structural pixels, exact source cores, and completed hash-bound reviews. It
writes `render-index.json` and `evidence.json` without setting artwork or
geographic approval. Generation originals and per-patch prompts remain evidence.

## 5. Build and inspect a native map artifact

```sh
just map-tiles renders/pixel-city-pilot-v1/render-index.json \
  configs/shinjuku-pilot-source.json renders/pixel-city-pilot-v1/evidence.json
just map-verify
just map-dev
# After inspecting the app and completing the checks:
just check
just viewer-e2e
just map-package
```

`map-tiles` checks the native crops and review bindings before selecting a new
content-addressed image release. `map-package` builds and packages only that
verified release. Packaging does not generate art or establish publication.
See [release formats](PREVIEW_RELEASE.md) for the historical preview, independent
verification and selection rules.
