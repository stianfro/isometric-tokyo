# Seam review and manual repair

`review_regions.py` assembles a bounded crop across finalized chunk boundaries.
It also imports an externally edited image as a new review candidate. It never
changes the source render, guide buffers, render index, or published tile
pyramid. A repair is not accepted artwork until a reviewer approves it.

## Assemble a seam region

```sh
just review-region assemble \
  --index renders/shinjuku-guides-v2/render-index.json \
  --bounds 40 40 320 320 \
  --layers textured geometry
```

The command returns a content-addressed directory under
`.cache/review-regions`. It contains:

- A pixel-exact, metadata-free PNG for each selected layer, at native scale.
- A separate annotated image per layer with red original chunk boundaries.
  Annotation does not alter the image that an editor should work on.
- `coverage.png`: white means a source chunk exists; black means a missing
  chunk. Coverage does not infer geometry completeness from image alpha.
- A manifest with global integer pixel bounds, config and source-index hashes,
  contributing chunk IDs, original chunk rectangles, selected artifact paths
  and checksums, coverage counts, and every output checksum.

Bounds are half-open `[left, top, right, bottom]` in the atlas pixel frame, not
latitude/longitude. Negative coordinates are valid. The crop must lie inside
the atlas extent and intersect at least one chunk. Choose one or two declared
image layers. Empty sparse cells remain transparent in original images and
dark gray in annotated images; they are not fabricated as source content.

Each region is limited to 4,000,000 pixels, 4096 pixels per side and 64
contributing chunks. A source chunk is subject to the pyramid reader's
16,000,000-pixel decode limit. Sources are decoded individually, not assembled
into a full-atlas canvas. The finalized index is capped at 20 MB and its source
checksums are verified before assembly. Selected source hashes are checked
again after assembly. Output encoding is RGBA8 PNG with no EXIF, text, embedded
profile or filename metadata.

```sh
just review-region verify .cache/review-regions/REGION_ID
```

Verification recomputes the content identity and checks all files, dimensions
and checksums. Unexpected files, symlinks, path escapes and metadata are
rejected. Repeating the same assembly verifies and reuses the identical bundle;
it does not overwrite it.

## Import an edited image

First register the finalized render index with the review workbench. Keep the
returned source job IDs for every chunk contributing to the region. The repair
import checks exact chunk, config, index and artifact identity against those
jobs, including hashes for their other artifacts. Batch-worker jobs alone are
not the source-review records used by this importer.

Edit `original-textured.png`, not the boundary annotation. Save a single-frame
PNG or WebP at exactly the original dimensions. Then submit it explicitly:

```sh
just review-region import-repair \
  --region .cache/review-regions/REGION_ID \
  --replacement .cache/edited-region.png \
  --bounds 40 40 320 320 \
  --layer textured \
  --edit-method manual \
  --source-jobs SOURCE_JOB_1 SOURCE_JOB_2 SOURCE_JOB_3 SOURCE_JOB_4 SOURCE_JOB_5 SOURCE_JOB_6 SOURCE_JOB_7 SOURCE_JOB_8 SOURCE_JOB_9 \
  --note "Corrected a facade seam while preserving the guide silhouette"
```

The repeated bounds are mandatory and must exactly equal the review region.
`--edit-method` is also mandatory: choose `manual` or `ai-assisted` to describe
how the external replacement was produced. The method is recorded in both the
immutable manifest and the ledger payload. The Python `import_repair` API defaults
to `edit_method="manual"` for existing callers; integrations using AI must pass
`edit_method="ai-assisted"` explicitly. This is provenance reporting, not a claim
that image inspection can reliably determine the method.
The input image is limited to 32 MB encoded and the region pixel limits still
apply. Only explicit artwork layers (`textured`, `styled`, `artwork`) may be
repaired. Geometry and guide layers cannot be repaired through this command.
Regions containing missing cells are rejected, even if the editor filled them.

The importer creates a separate immutable bundle under `.cache/repairs` with:

- `source.png`: the preserved original assembled source pixels.
- `repaired.png`: the submitted pixels converted to metadata-free RGBA8 PNG.
- `boundaries.png`: the original seam annotation for comparison.
- A manifest recording the source region, source jobs, chunk provenance,
  original input-file checksum, edit note and output checksums.

The editor's original filename, absolute path, EXIF, text fields and profile are
not copied into the bundle. Color-managed edits should be exported to the
intended RGBA8 appearance first; the importer does not preserve an embedded
profile or perform a separate color-space conversion.

## Review and invalidation

A new repair is atomically registered as `needs_review`, with dependencies on
all contributing source-review jobs. Existing descendants of those source jobs
are invalidated, including running leases and previous repair candidates.
Original source jobs and artifacts are retained. Unrelated work is unchanged.
Approval requires the source dependencies to have been approved first, followed
by an explicit repair review. The importer never approves either automatically.

The repair note, content and dependencies determine its identity. An identical
retry returns the existing job and does not invalidate work again or undo a
reviewer's decision. A failure after file publication but before ledger
registration can be retried: the immutable files are verified and registered
without replacement. A corrupted existing bundle fails instead of being
silently repaired in place.

A later build must depend on the **new repair job**, not retry old derived work
with its obsolete inputs. Invalidation is conservative at the contributing
source-job level, not a geometric search over every nearby region. Dependencies
must therefore describe the real inputs of derived jobs. Import does not
automatically splice the candidate into chunk outputs, retile
it, select it as the current version, or publish it. The explicit exporter below
can build a separate repaired study release after approval. The original atlas
remains unchanged.

## Build an explicitly repaired study release

After reviewers have approved the candidate and its source dependencies, select
its exact job ID. There is no implicit "latest repair" selection:

```sh
just repair-release --index renders/shinjuku-guides-v2/render-index.json \
  --jobs APPROVED_REPAIR_JOB_ID
```

The source index must have explicit boolean `ai_styling` provenance. Export
fails if it is missing or unknown. The new release marks `ai_styling` true when
the source already used AI or any selected repair declares `ai-assisted`.
A manual repair cannot erase an existing AI provenance flag. Older repair
bundles without an edit method must be reimported with an explicit method and
reviewed again before export; no method is inferred from their pixels.

The exporter checks every selected repair job and transitive dependency for
approved state and current artifact hashes. It matches the original index
checksum, config, contributing chunk IDs, bounds and selected layer bytes.
The preserved `source.png` pixels must equal the original chunk crops. A job
with valid image hashes but a different preserved source image is rejected.

Any overlapping repair rectangles are rejected, even across different layers.
Adjacent nonoverlapping rectangles are accepted and their selection order does
not affect output. No candidate is approved by running this command.

A new immutable directory appears under `.cache/repaired-releases/RELEASE_ID`.
Its `render-index.json` contains only finalized chunk cores. The exporter copies
unaffected layers, guide PNGs, numeric guides and the feature registry
byte-for-byte. Within the selected artwork layer it replaces exact repair
pixels, including alpha, clipped to each contributing chunk. It does not blend
against the old pixels or alter another layer. Original overscan and full-view
reference images are omitted because they no longer describe the repaired
artwork. Original files are not overwritten.

Repair proof bundles are retained alongside the new chunks. The index records
repair job IDs, repair identities, source-index hash and a complete output
checksum inventory. `repair_release_sha256` is the new content identity; the
underlying atlas config hash remains unchanged so the normal pyramid exporter
can reuse unaffected tiles. `artwork_approved` and `source_coverage_verified`
remain false, and `release_review_required` remains true. Approval of a local
repair is not approval of the assembled atlas or its geographic completeness.

```sh
just repair-release --verify .cache/repaired-releases/RELEASE_ID
just chunk-tiles .cache/repaired-releases/RELEASE_ID/render-index.json \
  .cache/repaired-pyramid
```

The pyramid command accepts this index normally. Reusing an existing
sampler-marked pyramid output allows its incremental exporter to regenerate
changed native tiles and their ancestors while retaining unchanged layers.
An automated regression test compares this result byte-for-byte with a fresh
full pyramid build. This operation still does not publish or activate a release.

This exporter is intentionally study-sized: at most 256 source chunks, 32
selected repairs, 4096 dependency jobs, and 512 MB of copied source plus repair
proof inputs. Dependency artifact validation has a separate 512 MB limit.
Encoded output above 512 MB is rejected. It processes one chunk and one repair
image at a time; the existing 16-million-pixel chunk and 4-million-pixel repair
limits apply. Disk copies are real copies rather than mutable hard links.
Repeated identical runs verify and reuse the content-addressed release. Source
or review-state changes detected during construction abort before publication.
The final review-state check and directory publication are serialized with
ledger writes. Files themselves must still obey the immutable-artifact contract.
