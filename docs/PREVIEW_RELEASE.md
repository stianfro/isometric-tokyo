# Versioned interactive preview

Pixel city is the preferred aesthetic direction. The preview makes the existing
bounded image explorable now, while the source-constrained art pipeline continues.
It is not a completed district, a geographic approval or a claim of seamless
AI-generated coverage.

## Build and inspect

After `just bootstrap`, use the project's Python environment:

```sh
export PYTHON="$PWD/.venv/bin/python"
just preview-dev
```

The task prints a local address without opening a browser. It creates lossless
native-resolution WebP tiles and lower-resolution image levels from the committed
PNG studies. No Blender, geographic downloads or image-generation calls are
needed. Pixel city is 1254 by 1254 pixels. The expanded source render is 1024 by
1024. They are independent image coordinates, not registered comparison layers.

The interface is map-first: one compact toolbar, a discreet provenance/credits
button and no full-width bars or place cards. The overview starts off; reset,
share, optional overview and layer controls live in the information panel.

A view switch resets framing. Shared links retain the selected view and its
position, zoom and layer. The earlier source/geometry comparison remains
available through `just viewer-dev`.

```sh
just check
just viewer-e2e
just preview-package
just preview-verify
```

`preview-package` builds browser assets with unrelated public directories disabled,
then packages only the selected verified preview. The task prints the immutable
bundle directory, ZIP path and archive SHA-256 under `renders/web-releases/`.
It includes source notices, original images, source/generation evidence, the
adapted ODbL vegetation database and runtime software licences. The review server,
SQLite ledger, raw geographic cache and local configuration are not app inputs.

To verify a saved bundle without rebuilding:

```sh
just preview-package-verify renders/web-releases/WEB_RELEASE_ID
```

## Identity and selection

The image release ID hashes the explicit public inputs, original PNG checksums,
notices, preferred-direction record, exporter code, tile size and Pillow/WebP
versions. Each version lives at `releases/RELEASE_ID/` below the preview output.
Native tiles are compared directly with the original image pixels. Every lower
level is compared with the expected resampling, so editing a tile and its inventory
cannot pass verification. Derived notices must match their bound source notice. Every asset
has a size and SHA-256 record, and every DZI descriptor must match its own view.

A new version is built and verified before the active `manifest.json` catalog is
atomically replaced. Existing version directories are not overwritten. Repeating
an unchanged build verifies and reuses its version; corruption fails rather than
being silently repaired. Changed attribution produces a new identity.

The complete web bundle has its own content-derived ID covering the built app,
selected image release, notices and software licences. Its ZIP has deterministic
ordering and timestamps. Copying a complete prior bundle selects that prior app
and image version together, rather than mixing old HTML and new manifests.
The packager never includes other cached preview versions.

## Public contract

The root catalog uses `kind: atlas_collection`, `default_view: pixel-city` and
relative child-manifest paths. Each child keeps the existing viewer manifest and
DZI contract with its own dimensions. Catalog paths must stay below the catalog
URL. The viewer checks that a source view is not AI-styled and that illustration
previews explicitly remain unapproved and geographically unverified.

The selected direction is in `configs/art-direction.json`. Original generation
provenance stays unchanged in `ai-studies.json`; a later preference does not
rewrite the historical review status. The image may invent or alter facade,
roof, road and vegetation detail. Persistent labels and the credits explain this.

## Verification limits

Checks cover deterministic builds, provenance, native pixels, metadata leakage,
symlinks, missing and unexpected files, tampering, input changes during builds,
independent view coordinates, shared links, history, mobile navigation and error
recovery. They do not prove geographic accuracy, seam quality outside this single
image or performance on a named physical phone. Those remain separate roadmap
gates. No production-art approval is inferred from a passing app build.
