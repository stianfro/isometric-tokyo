import { actionRequest, allowedActions, artifactName, counts, filterJobs, formatBytes, imageArtifacts, parseJobs, STATES, STATE_LABELS } from './model.mjs'

const element = (id) => document.getElementById(id)
const token = document.querySelector('meta[name="review-token"]')?.content
const PAGE_SIZE = 40
const MAX_IMAGE_BYTES = 64 * 1024 * 1024
let jobs = []
let selectedId = null
let queuePage = 0
let busy = false
let viewed = new Set()
const panels = {
  left: { controller: null, objectUrl: null },
  right: { controller: null, objectUrl: null },
}

function showMessage(id, message) {
  element(id).textContent = message
  element(id).hidden = !message
}

async function api(path, options = {}) {
  if (!token || token.includes('{{')) throw new Error('The review session token is missing. Open the desk using the review task.')
  if (!path.startsWith('/api/') || path.includes('..') || path.includes('?') || path.includes('#')) throw new Error('Invalid review request.')
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Authorization: `Bearer ${token}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
  })
  if (!response.ok) {
    let reason = response.status === 401 ? 'The review session expired. Reopen the desk.' : `Review request failed (${response.status}).`
    try { const result = await response.json(); if (typeof result.error === 'string') reason = result.error } catch { /* Keep the status message when the response is not JSON. */ }
    throw new Error(reason)
  }
  return response
}

function selectedJob() { return jobs.find((job) => job.id === selectedId) }

function renderQueue() {
  const totals = counts(jobs)
  const oldFilter = element('filter').value
  element('filter').replaceChildren(new Option(`All states (${jobs.length})`, 'all'), ...STATES.map((state) => new Option(`${STATE_LABELS[state]} (${totals[state]})`, state)))
  element('filter').value = oldFilter
  element('total-count').textContent = `${jobs.length} ${jobs.length === 1 ? 'job' : 'jobs'}`
  const filtered = filterJobs(jobs, oldFilter, element('search').value)
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  queuePage = Math.min(queuePage, pageCount - 1)
  element('queue-summary').textContent = filtered.length ? `${filtered.length} matching ${filtered.length === 1 ? 'job' : 'jobs'}` : 'No jobs match these filters.'
  element('job-list').replaceChildren(...filtered.slice(queuePage * PAGE_SIZE, (queuePage + 1) * PAGE_SIZE).map((job) => {
    const li = document.createElement('li')
    const button = document.createElement('button')
    button.className = 'job-choice'
    button.type = 'button'
    button.disabled = busy
    button.setAttribute('aria-current', String(job.id === selectedId))
    const area = document.createElement('strong'); area.textContent = job.area
    const kind = document.createElement('span'); kind.textContent = `${job.kind} · ${job.id.slice(0, 10)}`; kind.className = 'job-choice-kind'
    const state = document.createElement('span'); state.textContent = STATE_LABELS[job.state]; state.className = `state-badge state-${job.state}`
    button.append(area, kind, state)
    button.addEventListener('click', () => selectJob(job.id, true))
    li.append(button)
    return li
  }))
  element('page-label').textContent = `${queuePage + 1} / ${pageCount}`
  element('previous').disabled = queuePage === 0
  element('next').disabled = queuePage + 1 >= pageCount
  document.querySelector('.queue-pages').hidden = pageCount === 1
}

function clearPanel(side) {
  const panel = panels[side]
  panel.controller?.abort()
  if (panel.objectUrl) URL.revokeObjectURL(panel.objectUrl)
  panel.objectUrl = null
  panel.controller = null
}

async function loadPanel(side) {
  clearPanel(side)
  const job = selectedJob()
  const stage = element(`${side}-stage`)
  const caption = element(`${side}-caption`)
  const artifact = job && imageArtifacts(job).find((item) => item.url === element(`${side}-image`).value)
  if (!artifact) { stage.replaceChildren(); caption.textContent = ''; return }
  const controller = new AbortController()
  panels[side].controller = controller
  const pending = document.createElement('p'); pending.textContent = 'Loading and checking image…'; pending.setAttribute('role', 'status')
  stage.replaceChildren(pending)
  stage.dataset.loaded = 'false'
  caption.textContent = `${artifactName(artifact.path)} · ${formatBytes(artifact.bytes)}`
  try {
    if (artifact.bytes > MAX_IMAGE_BYTES) throw new Error('This image exceeds the 64 MiB preview limit. Use a smaller review artifact.')
    const response = await api(artifact.url, { signal: controller.signal })
    const type = response.headers.get('Content-Type')?.split(';')[0]
    if (!['image/png', 'image/webp', 'image/jpeg'].includes(type)) throw new Error('This artifact is not a supported raster image.')
    const blob = await response.blob()
    if (blob.size !== artifact.bytes || blob.size > MAX_IMAGE_BYTES) throw new Error('The image size changed after the job finished.')
    const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
    if (hash !== artifact.sha256) throw new Error('The image checksum changed after the job finished.')
    if (controller.signal.aborted) return
    const url = URL.createObjectURL(blob)
    panels[side].objectUrl = url
    const img = new Image()
    img.alt = `${job.area}, ${artifactName(artifact.path)}`
    img.draggable = false
    img.src = url
    await img.decode()
    if (controller.signal.aborted) return
    stage.replaceChildren(img)
    stage.dataset.loaded = 'true'
    caption.textContent += ` · ${img.naturalWidth} × ${img.naturalHeight} px · checksum verified`
    viewed.add(artifact.url)
    renderActions()
  } catch (error) {
    if (controller.signal.aborted) return
    const message = document.createElement('p')
    message.className = 'image-error'
    message.setAttribute('role', 'alert')
    message.textContent = error instanceof Error ? error.message : 'The image could not load.'
    stage.replaceChildren(message)
    viewed.delete(artifact.url)
    renderActions()
  }
}

function selectJob(id, focus = false, keepNote = false) {
  selectedId = id
  const job = selectedJob()
  viewed = new Set()
  clearPanel('left'); clearPanel('right')
  if (!keepNote) element('note').value = ''
  showMessage('action-error', '')
  element('empty').hidden = Boolean(job)
  element('job-detail').hidden = !job
  renderQueue()
  if (!job) return
  element('job-kind').textContent = `${job.kind} / attempt ${job.attempts} of ${job.max_attempts}`
  element('job-area').textContent = job.area
  element('job-id').textContent = job.id
  element('job-state').textContent = STATE_LABELS[job.state]
  element('job-state').className = `state-badge state-${job.state}`
  showMessage('job-error', job.error ?? '')
  const images = imageArtifacts(job)
  element('comparison').hidden = !images.length
  element('no-images').hidden = Boolean(images.length)
  for (const [index, side] of ['left', 'right'].entries()) {
    const select = element(`${side}-image`)
    select.replaceChildren(...images.map((artifact) => new Option(artifactName(artifact.path), artifact.url)))
    select.selectedIndex = Math.min(index, images.length - 1)
    select.disabled = images.length < 2
    loadPanel(side)
  }
  element('artifact-list').replaceChildren(...job.artifacts.map((artifact) => {
    const li = document.createElement('li')
    const name = document.createElement('strong'); name.textContent = `${artifactName(artifact.path)} · ${formatBytes(artifact.bytes)}`
    const hash = document.createElement('code'); hash.textContent = `SHA-256 ${artifact.sha256}`
    li.append(name, hash)
    return li
  }))
  renderActions()
  if (focus) { element('job-area').tabIndex = -1; element('job-area').focus({ preventScroll: true }) }
}

function renderActions() {
  const job = selectedJob()
  if (!job) return
  const images = imageArtifacts(job)
  const allViewed = images.length > 0 && images.every((artifact) => viewed.has(artifact.url))
  const actions = allowedActions(job)
  element('image-progress').textContent = `${viewed.size} / ${images.length} image artifacts opened`
  element('approve').disabled = busy || !actions.approve || !allViewed
  element('reject').disabled = busy || !actions.reject
  element('retry').disabled = busy || !actions.retry
  element('note').disabled = busy
  element('refresh').disabled = busy
  element('filter').disabled = busy
  element('search').disabled = busy
  let help = 'A note is required. Approval verifies recorded checksums again. Rejection invalidates this output and its dependent jobs.'
  if (job.state === 'needs_review' && !allViewed) help = 'Open every image artifact before approving. Viewing an image does not mean it has passed review.'
  if (job.state === 'failed') help = actions.retry ? 'Queue retry returns this job to pending. It does not start a worker or generate images.' : 'The attempt allowance is exhausted. This desk cannot increase the retry budget.'
  if (job.state === 'running') help = 'This job is still running. Wait for its recorded artifacts before reviewing.'
  if (job.state === 'pending') help = 'This job is queued. This desk does not start workers or generate images.'
  if (job.state === 'approved') help = 'This output is approved. Rejecting it invalidates dependent jobs as well; leave a specific reason.'
  element('action-help').textContent = help
}

async function refreshJobs() {
  element('refresh').disabled = true
  showMessage('global-error', '')
  try {
    jobs = parseJobs(await (await api('/api/jobs')).json())
    renderQueue()
    if (selectedId) selectJob(selectedId, false, true)
    else if (jobs.length) selectJob(filterJobs(jobs)[0].id)
  } catch (error) {
    showMessage('global-error', error instanceof Error ? error.message : 'The job list could not load.')
    element('queue-summary').textContent = 'Could not refresh the job list.'
  } finally { element('refresh').disabled = busy }
}

async function decide(action) {
  const job = selectedJob()
  if (!job || busy) return
  showMessage('action-error', '')
  showMessage('global-message', '')
  try {
    const request = actionRequest(job, action, element('note').value)
    busy = true
    renderActions()
    renderQueue()
    const response = await (await api(request.path, { method: 'POST', body: JSON.stringify(request.body) })).json()
    const [updated] = parseJobs({ jobs: [response.job] })
    const expected = { approve: 'approved', reject: 'failed', retry: 'pending' }
    if (updated.id !== job.id || updated.state !== expected[action]) throw new Error('The decision response was unexpected. Refresh the job list before trying again.')
    element('note').value = ''
    const messages = { approve: 'Output approved. The server verified its recorded checksums.', reject: 'Output rejected. Dependent jobs were invalidated and must be reviewed again.', retry: 'Retry queued. No worker or image generation was started.' }
    showMessage('global-message', messages[action])
    await refreshJobs()
  } catch (error) { showMessage('action-error', error instanceof Error ? error.message : 'The decision could not be saved.') }
  finally { busy = false; renderActions(); renderQueue() }
}

element('refresh').addEventListener('click', refreshJobs)
for (const id of ['filter', 'search']) element(id).addEventListener(id === 'search' ? 'input' : 'change', () => { queuePage = 0; renderQueue() })
element('previous').addEventListener('click', () => { queuePage -= 1; renderQueue() })
element('next').addEventListener('click', () => { queuePage += 1; renderQueue() })
for (const side of ['left', 'right']) element(`${side}-image`).addEventListener('change', () => loadPanel(side))
element('zoom').addEventListener('change', () => { for (const side of ['left', 'right']) element(`${side}-stage`).dataset.zoom = element('zoom').value })
for (const action of ['approve', 'reject', 'retry']) element(action).addEventListener('click', () => decide(action))
let syncingPan = false
for (const side of ['left', 'right']) {
  element(`${side}-stage`).addEventListener('scroll', () => {
    if (!element('linked-pan').checked || syncingPan) return
    syncingPan = true
    const source = element(`${side}-stage`)
    const target = element(`${side === 'left' ? 'right' : 'left'}-stage`)
    const fraction = (value, maximum) => maximum > 0 ? value / maximum : 0
    target.scrollLeft = fraction(source.scrollLeft, source.scrollWidth - source.clientWidth) * (target.scrollWidth - target.clientWidth)
    target.scrollTop = fraction(source.scrollTop, source.scrollHeight - source.clientHeight) * (target.scrollHeight - target.clientHeight)
    requestAnimationFrame(() => { syncingPan = false })
  }, { passive: true })
}
window.addEventListener('beforeunload', () => { clearPanel('left'); clearPanel('right') })
refreshJobs()
