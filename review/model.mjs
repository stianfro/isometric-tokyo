export const STATES = ['needs_review', 'failed', 'running', 'pending', 'approved']
export const STATE_LABELS = { needs_review: 'Needs review', failed: 'Failed', running: 'Running', pending: 'Pending', approved: 'Approved' }

const identifier = (value) => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value)
const text = (value) => typeof value === 'string' && value.length <= 10_000

export function parseJobs(value) {
  if (!value || !Array.isArray(value.jobs)) throw new Error('The job list is invalid.')
  const seen = new Set()
  return value.jobs.map((job) => {
    if (!job || !identifier(job.id) || seen.has(job.id) || !text(job.kind) || !text(job.area) || !STATES.includes(job.state)) throw new Error('A job record is invalid.')
    seen.add(job.id)
    if (!Number.isSafeInteger(job.attempts) || job.attempts < 0 || !Number.isSafeInteger(job.max_attempts) || job.max_attempts < 1 || job.attempts > job.max_attempts) throw new Error('A job attempt count is invalid.')
    if (!Array.isArray(job.artifacts)) throw new Error('A job artifact list is invalid.')
    const artifacts = job.artifacts.map((artifact, index) => {
      if (!artifact || !text(artifact.path) || !artifact.path || !Number.isSafeInteger(artifact.bytes) || artifact.bytes < 0 || typeof artifact.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(artifact.sha256)) throw new Error('An artifact record is invalid.')
      const expected = `/api/artifacts/${job.id}/${index}`
      if (artifact.url !== expected) throw new Error('An artifact URL is not in the job ledger.')
      return { path: artifact.path, bytes: artifact.bytes, sha256: artifact.sha256, url: expected }
    })
    return { id: job.id, kind: job.kind, area: job.area, state: job.state, attempts: job.attempts, max_attempts: job.max_attempts, error: text(job.error) ? job.error : null, artifacts }
  })
}

export function filterJobs(jobs, state = 'all', query = '') {
  const needle = query.trim().toLowerCase()
  return jobs.filter((job) => (state === 'all' || job.state === state) && `${job.area} ${job.kind} ${job.id}`.toLowerCase().includes(needle))
    .sort((a, b) => STATES.indexOf(a.state) - STATES.indexOf(b.state) || a.area.localeCompare(b.area) || a.id.localeCompare(b.id))
}

export function counts(jobs) {
  return Object.fromEntries(STATES.map((state) => [state, jobs.filter((job) => job.state === state).length]))
}

export function imageArtifacts(job) {
  return job.artifacts.filter((artifact) => /\.(png|webp|jpe?g)$/i.test(artifact.path))
}

export function artifactName(path) {
  return path.replaceAll('\\', '/').split('/').at(-1) || 'Unnamed artifact'
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`
}

export function allowedActions(job) {
  return {
    approve: job.state === 'needs_review' && job.artifacts.length > 0,
    reject: job.state === 'needs_review' || job.state === 'approved',
    retry: job.state === 'failed' && job.attempts < job.max_attempts,
  }
}

export function actionRequest(job, action, note) {
  if (!['approve', 'reject', 'retry'].includes(action) || !allowedActions(job)[action]) throw new Error('That action is not available for this job.')
  if (!identifier(job.id)) throw new Error('The job identifier is invalid.')
  const trimmed = String(note ?? '').trim()
  if (action !== 'retry' && (!trimmed || trimmed.length > 2000)) throw new Error('Write a review note between 1 and 2000 characters.')
  return { path: `/api/jobs/${job.id}/${action}`, body: action === 'retry' ? {} : { note: trimmed } }
}
