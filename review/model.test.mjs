import { test } from 'node:test'
import assert from 'node:assert/strict'
import { actionRequest, allowedActions, artifactName, counts, filterJobs, formatBytes, imageArtifacts, parseJobs } from './model.mjs'

const artifact = { path: 'chunks/source.png', bytes: 1024, sha256: 'a'.repeat(64), url: '/api/artifacts/test-job/0' }
const job = { id: 'test-job', kind: 'render', area: 'Shinjuku', state: 'needs_review', attempts: 1, max_attempts: 3, error: null, payload: {}, artifacts: [artifact] }

test('parses only review-visible fields from a job record', () => {
  const result = parseJobs({ jobs: [job] })[0]
  assert.equal(result.state, 'needs_review')
  assert.equal(result.payload, undefined)
  assert.equal(result.artifacts[0].url, artifact.url)
})

test('rejects invalid jobs and artifact URLs instead of loading arbitrary paths', () => {
  for (const change of [{ id: '../outside' }, { state: 'done' }, { attempts: -1 }, { max_attempts: 0 }, { attempts: 4 }, { artifacts: null }]) {
    assert.throws(() => parseJobs({ jobs: [{ ...job, ...change }] }))
  }
  for (const url of ['https://example.com/image.png', '/api/artifacts/other/0', '/api/artifacts/test-job/1', '/api/jobs', '/api/artifacts/test-job/0?token=secret']) {
    assert.throws(() => parseJobs({ jobs: [{ ...job, artifacts: [{ ...artifact, url }] }] }))
  }
  assert.throws(() => parseJobs({ jobs: [job, job] }))
  assert.throws(() => parseJobs({ jobs: [{ ...job, artifacts: [{ ...artifact, sha256: 'wrong' }] }] }))
})

test('filters and prioritizes needs-review jobs without mutating the list', () => {
  const jobs = [{ ...job, id: 'approved', state: 'approved' }, job, { ...job, id: 'failed', state: 'failed', area: 'Other' }]
  assert.deepEqual(filterJobs(jobs).map((item) => item.id), ['test-job', 'failed', 'approved'])
  assert.deepEqual(filterJobs(jobs, 'all', ' SHINJUKU ').map((item) => item.id), ['test-job', 'approved'])
  assert.equal(filterJobs(jobs, 'failed').length, 1)
  assert.equal(jobs[0].id, 'approved')
  assert.deepEqual(counts(jobs), { needs_review: 1, failed: 1, running: 0, pending: 0, approved: 1 })
})

test('review actions require a valid state and a nonempty trimmed note', () => {
  assert.deepEqual(actionRequest(job, 'approve', '  Checked source and geometry.  '), { path: '/api/jobs/test-job/approve', body: { note: 'Checked source and geometry.' } })
  assert.throws(() => actionRequest(job, 'approve', '  '))
  assert.throws(() => actionRequest(job, 'reject', 'a'.repeat(2001)))
  assert.throws(() => actionRequest(job, 'retry', ''))
  assert.throws(() => actionRequest(job, 'delete', ''))
  assert.throws(() => actionRequest({ ...job, state: 'approved' }, 'approve', 'again'))
  assert.equal(allowedActions({ ...job, state: 'approved' }).reject, true)
})

test('retry queues failed jobs only within their remaining attempt allowance', () => {
  assert.deepEqual(actionRequest({ ...job, state: 'failed' }, 'retry', ''), { path: '/api/jobs/test-job/retry', body: {} })
  assert.throws(() => actionRequest({ ...job, state: 'failed', attempts: 3 }, 'retry', ''))
  assert.throws(() => actionRequest({ ...job, state: 'running' }, 'retry', ''))
})

test('image inspection excludes non-image and executable artifacts', () => {
  const files = ['source.PNG', 'normal.webp', 'detail.jpeg', 'mask.jpg', 'index.json', 'vector.svg', 'source.png.html']
  assert.deepEqual(imageArtifacts({ artifacts: files.map((path) => ({ path })) }).map((item) => item.path), files.slice(0, 4))
  assert.equal(artifactName('chunks/1/source.png'), 'source.png')
  assert.equal(formatBytes(100), '100 B')
  assert.equal(formatBytes(2048), '2.0 KiB')
  assert.equal(formatBytes(1048576), '1.0 MiB')
})
