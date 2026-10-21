# Production ledger

`just jobs` lists the local SQLite ledger. This is a production foundation, not
a complete image-generation system. It never calls an image model, executes
arbitrary commands or approves artwork on its own. The bounded render runner
executes only the project's Blender renderer and finalizer.

## Job identity and lifecycle

Create a JSON job specification with `kind`, `area` and `payload`, plus optional
`dependencies` (job IDs) and `max_attempts` (default 3). Include source hashes,
atlas configuration hash and tool versions in the payload. Changing any of those
inputs creates a different job identity. Enqueue is idempotent.

```json
{
  "kind": "render",
  "area": "shinjuku",
  "payload": {
    "config_sha256": "replace-with-verified-config-hash",
    "source_sha256": "replace-with-verified-source-hash",
    "renderer_version": "replace-with-actual-version"
  },
  "max_attempts": 3
}
```

```sh
just jobs enqueue job.json
just jobs claim --worker renderer-1 --lease-seconds 300
just jobs heartbeat JOB_ID --token LEASE_TOKEN --lease-seconds 300
just jobs finish JOB_ID --token LEASE_TOKEN renders/JOB_ID/textured.png
just jobs approve JOB_ID --note 'Compared with source and checked all joins'
```

The state sequence is `pending`, `running`, `needs_review`, `approved`. Errors
enter `failed`; `just jobs retry JOB_ID` allows another attempt only below the
limit. An expired worker lease returns work to the queue or fails it if its
attempt budget is exhausted. A stale worker cannot complete a reassigned job.
Dependent jobs are claimable only after every parent is approved.

Store each job's outputs in a unique, immutable directory based on its ID. The
ledger hashes outputs at completion and rechecks them at approval. It does not
continuously watch approved files for mutation, so do not overwrite them. Use a
new job with changed inputs for new output. Output paths are workspace-relative;
paths escaping that workspace are rejected.

`just jobs invalidate JOB_ID --note 'Source correction'` invalidates that job and
all downstream jobs, revoking active leases. It preserves artifacts and event
history and leaves unrelated work alone. Retry budgets do not reset on
invalidation. Create a revised input identity when the budget is exhausted.

## Verification boundaries

Unit tests cover process interruption, concurrent claims through separate
connections, expired leases, stale completion, checksums, dependency ordering,
selective invalidation and retry limits. The bounded batch runner and [review workbench](REVIEW.md) connect actual renders
to this ledger. Automatic art-generation dependencies, image-model budgets and
manual repair import remain separate implementation work.

The sample-image exporter behind `just tiles` has a 16-megapixel input cap and
must not be used to assemble a city-sized master image. It writes matching DZI
pyramids, strips local renderer metadata, preserves source credits and verifies
asset dimensions, hashes and release identity before replacing its prior output.
Native-resolution WebP tiles are lossless. Originals remain available separately.

## Resumable render batches

`just atlas-batch CONFIG OUTPUT` plans chunks from a validated configuration,
records source-file and renderer-code hashes, and runs each chunk as a separate
Blender job. The input assets, renderer version, settings and integer pixel bounds
form the job identity. Running the same command again skips completed and
approved jobs. Only this batch's jobs can be claimed; unrelated queued work is
left alone. The first implementation uses one worker to bound resource use.

Each attempt receives a new directory. The runner renews its lease during long
renders, enforces a per-job time limit, retains failure logs, finalizes output and
checks hashes before marking the job `needs_review`. It does not approve images.
A killed worker's lease can expire safely. Failed jobs require an explicit
`--retry-failed` CLI flag and remain subject to the attempt limit.

When every chunk is complete, the runner writes a combined `render-index.json`
for `just chunk-tiles`. This indicates completed rendering, not approved artwork
or complete geographic source coverage. Artifact hashes are checked again when
resuming or assembling the index. Use unique immutable output directories for
completed versions. The review API previews image artifacts only; the ledger
also retains index and numeric metadata artifacts for integrity checks.

Batch tests simulate interruption, failure, retry, source changes, stale output
and unrelated queued work. Blender seam fixtures test the actual renderer.
