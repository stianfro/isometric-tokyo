import { test, expect, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const token = 'automated-test-token'
const root = new URL('../../review/', import.meta.url)
const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=', 'base64')
const sha = createHash('sha256').update(image).digest('hex')
const id = 'a'.repeat(64)
function fixture() {
  return {
    id, kind: 'source-render', area: 'Shinjuku test chunk', state: 'needs_review', attempts: 1, max_attempts: 3, error: null,
    artifacts: ['source.png', 'geometry.png'].map((path, index) => ({ path, bytes: image.length, sha256: sha, url: `/api/artifacts/${id}/${index}` })),
  }
}

type Options = { corruptImage?: boolean; expired?: boolean; failApproval?: boolean }
async function openDesk(page: Page, options: Options = {}) {
  const job = fixture()
  const calls: { action: string; note?: string }[] = []
  const errors: Error[] = []
  page.on('pageerror', (error) => errors.push(error))
  await page.route('**/desk', async (route) => {
    const html = (await readFile(fileURLToPath(new URL('index.html', root)), 'utf8')).replace('{{REVIEW_TOKEN}}', token)
    await route.fulfill({ status: 200, contentType: 'text/html', body: html, headers: { 'Content-Security-Policy': "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; connect-src 'self'" } })
  })
  for (const name of ['review.js', 'review.css', 'model.mjs']) {
    await page.route(`**/${name}`, async (route) => route.fulfill({ status: 200, contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript', body: await readFile(fileURLToPath(new URL(name, root))) }))
  }
  await page.route('**/api/**', async (route) => {
    expect(route.request().headers().authorization).toBe(`Bearer ${token}`)
    const path = new URL(route.request().url()).pathname
    if (options.expired) { await route.fulfill({ status: 403, json: { error: 'Review authorization required' } }); return }
    if (path === '/api/jobs') { await route.fulfill({ json: { jobs: [job] } }); return }
    if (path.startsWith('/api/artifacts/')) {
      await route.fulfill({ contentType: 'image/png', body: options.corruptImage ? Buffer.from('invalid image') : image }); return
    }
    const action = path.split('/').at(-1)!
    const body = route.request().postDataJSON() as { note?: string }
    calls.push({ action, ...body })
    if (options.failApproval && action === 'approve') { await route.fulfill({ status: 400, json: { error: 'Artifact changed after completion' } }); return }
    job.state = { approve: 'approved', reject: 'failed', retry: 'pending' }[action] ?? job.state
    await route.fulfill({ json: { job } })
  })
  await page.goto('/desk')
  return { calls, errors }
}

test('review desk previews authorized images, requires notes and records per-job decisions', async ({ page }) => {
  const { calls, errors } = await openDesk(page)
  await expect(page.locator('#image-progress')).toHaveText('2 / 2 image artifacts opened')
  await expect(page.locator('#left-stage')).toHaveAttribute('data-loaded', 'true')
  await expect(page.locator('#right-caption')).toContainText('checksum verified')
  await page.getByRole('button', { name: 'Approve output' }).click()
  await expect(page.locator('#action-error')).toContainText('Write a review note')
  expect(calls).toHaveLength(0)
  await page.getByLabel('Review note', { exact: false }).fill('Geometry and source match. Boundary checked against neighbor.')
  await page.getByRole('button', { name: 'Approve output' }).click()
  await expect(page.locator('#job-state')).toHaveText('Approved')
  await expect(page.getByRole('button', { name: 'Approve output' })).toBeDisabled()
  await page.getByLabel('Review note', { exact: false }).fill('Neighbor join needs a repair.')
  await page.getByRole('button', { name: 'Reject output' }).click()
  await expect(page.locator('#job-state')).toHaveText('Failed')
  await page.getByRole('button', { name: 'Queue retry' }).click()
  await expect(page.locator('#job-state')).toHaveText('Pending')
  await expect(page.locator('#global-message')).toContainText('No worker or image generation was started.')
  expect(calls.map((call) => call.action)).toEqual(['approve', 'reject', 'retry'])
  expect(errors).toEqual([])
})

test('review desk blocks approval of corrupt images and shows authentication failures', async ({ page }) => {
  const { calls, errors } = await openDesk(page, { corruptImage: true })
  await expect(page.locator('#left-stage')).toContainText('image size changed')
  await expect(page.getByRole('button', { name: 'Approve output' })).toBeDisabled()
  expect(calls).toHaveLength(0)
  expect(errors).toEqual([])
})

test('review failure preserves a note and never reports false approval', async ({ page }) => {
  const { errors } = await openDesk(page, { failApproval: true })
  await expect(page.locator('#image-progress')).toHaveText('2 / 2 image artifacts opened')
  await page.getByLabel('Review note', { exact: false }).fill('This note must survive a failed save.')
  await page.getByRole('button', { name: 'Approve output' }).click()
  await expect(page.locator('#action-error')).toContainText('Artifact changed after completion')
  await expect(page.getByLabel('Review note', { exact: false })).toHaveValue('This note must survive a failed save.')
  await expect(page.locator('#job-state')).toHaveText('Needs review')
  expect(errors).toEqual([])
})

test('review authorization errors are visible', async ({ page }) => {
  const { errors } = await openDesk(page, { expired: true })
  await expect(page.locator('#global-error')).toHaveText('Review authorization required')
  await expect(page.getByRole('button', { name: 'Approve output' })).not.toBeVisible()
  expect(errors).toEqual([])
})
