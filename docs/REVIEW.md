# Source-output review desk

The review desk is a small offline-production tool for inspecting images already
recorded in the job ledger. It does not render, generate, or automatically approve
artwork. A source-output approval is not approval of the final Tokyo art direction
or acceptance of an entire coverage release.

## Start with verified output

1. Complete and finalize an atlas render so its `render-index.json` records every
   chunk, layer, image dimension and checksum.
2. Run `just review-import PATH/TO/render-index.json` to verify those records and
   import completed source-render jobs into the ledger as **needs review**.
   Reimporting does not erase an earlier rejection.
3. Run `just review`. Open the review address printed by the task. The task does
   not open a browser automatically.
4. Select a job, compare its recorded image artifacts, and write a review note.

The job list supports state filters and area, kind or ID search. Its counts refer
to the full returned ledger; a long filtered list is paginated. The desk does not
list arbitrary workspace files.

## Compare before deciding

The two image selectors can show source textures, geometry, normals, depth or
other raster guides that were actually recorded for a job. There is no fabricated
styled layer. PNG, JPEG and WebP can be previewed. Other artifact names and their
checksums are listed without being executed or embedded.

Image fetches require the review session token, as do every job read and decision.
The token is passed in request headers, never URLs. Preview files are checked
against the ledger's byte count and SHA-256 before they are displayed. Temporary
image URLs are released when a selection changes. Images larger than 64 MiB need
a smaller review artifact.

Use fit width or 150%, 200%, and 400% zoom. Linked pan uses image fractions, not
geographic coordinates; compare only images whose frames are already aligned.
Open every image artifact before approving. The image-open count is a workflow
check, not evidence that any image has passed review.

Review at least:

- Source fidelity, landmark silhouettes and relative building height.
- Ground alignment, missing textures and geometry gaps.
- Cropped or repeated structures and shadows at boundaries.
- Horizontal, vertical and four-way joins with neighboring chunks.
- Relevant guide layers and any recorded seam-check evidence.

The current desk compares artifacts within one selected job. Neighboring seams
still need separate comparison unless a recorded seam test covers them. It does
not yet provide a geographic region picker, annotated defect masks, manual-repair
import, or an assembled multi-job seam view.

## Decisions

- **Approve output:** available only for completed jobs awaiting review, after all
  image artifacts have opened. A note is required. The server verifies recorded
  files and checksums again before recording approval.
- **Reject output:** available for jobs awaiting review or already approved.
  A note is required. Rejection invalidates the dependent job closure, so stale
  output cannot retain approval after an upstream change.
- **Queue retry:** available only for failed jobs with remaining attempts. This
  returns the job to pending; it does not start a worker or generate imagery.

There is no bulk approval and no option to raise an exhausted attempt allowance.
Notes are limited to 2000 characters. Failed requests keep the note available for
correction, and stale or changed artifacts must be fixed before approval.

## Checks

`just review-test` runs dependency-free Node tests for job validation, filtering,
artifact selection, action-state rules and mandatory notes. The Python review
service tests cover authorization, artifact validation and state transitions.
Browser checks exercise the complete interface using a disposable ledger, not
production approval records.
