import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

let fixture: { directory: string }
const failures = new WeakMap<Page, Error[]>()
test.beforeAll(() => {
  fixture = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', [fileURLToPath(new URL('./generate_pilot_fixture.py', import.meta.url)), resolve('../.cache/viewer-pilot-e2e', String(process.pid))], { encoding: 'utf8' }))
})
test.beforeEach(async ({ page }) => {
  const errors: Error[] = []
  failures.set(page, errors)
  page.on('pageerror', (error) => errors.push(error))
  await page.route('**/atlas/**', async (route) => {
    const name = new URL(route.request().url()).pathname.split('/atlas/')[1]
    try {
      const body = await readFile(resolve(fixture.directory, name))
      await route.fulfill({ status: 200, contentType: name.endsWith('.json') ? 'application/json' : name.endsWith('.dzi') ? 'application/xml' : name.endsWith('.txt') ? 'text/plain' : 'image/webp', body })
    } catch { await route.fulfill({ status: 404, body: 'Missing pilot fixture' }) }
  })
})
test.afterEach(async ({ page }) => expect(failures.get(page)).toEqual([]))

const position = (page: Page) => new URLSearchParams(new URL(page.url()).hash.slice(1))
async function ready(page: Page, layer: string) {
  const map = page.getByRole('region', { name: 'Shinjuku interactive map' })
  await expect(map).toHaveAttribute('data-layer', layer)
  await expect(map).toHaveAttribute('data-ready', 'true')
  await expect(page).toHaveURL(new RegExp(`layer=${layer}`))
}

test('native pilot stays minimal and uses viewport-covering home on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/')
  await ready(page, 'styled')
  expect(Number(position(page).get('z'))).toBeCloseTo(780 / 360, 4)
  await expect(page.getByRole('button', { name: 'AI preview · Credits', exact: true })).toBeVisible()
  const toolbar = page.getByRole('navigation', { name: 'Map navigation' })
  await expect(toolbar.getByRole('button')).toHaveCount(4)
  const box = await toolbar.boundingBox()
  expect(box!.width).toBeLessThan(344)
  await expect(page.locator('.place-label, .masthead, .map-footer, .coverage-note')).toHaveCount(0)
  await expect(page.locator('#atlas-overview .openseadragon-container')).toHaveCount(0)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await expect(page.getByRole('dialog')).toContainText('nine-patch Pixel city pilot')
  await expect(page.getByRole('dialog')).toContainText('This does not mean buildings or streets are pixel-aligned')
  await expect(page.getByRole('dialog')).toContainText('Geographic fidelity is not verified')
  const notice = await page.getByRole('link', { name: /Full source terms/ }).getAttribute('href')
  expect(notice).toMatch(/atlas\/NOTICE.txt$/)
})

test('source toggle preserves shared framing but correctly changes provenance', async ({ page }) => {
  await page.goto('/#x=0.58&y=0.52&z=4&layer=styled')
  await ready(page, 'styled')
  const original = position(page)
  await page.getByRole('button', { name: 'Show Source view', exact: true }).click()
  await ready(page, 'textured')
  for (const key of ['x', 'y', 'z']) expect(position(page).get(key)).toBe(original.get(key))
  await expect(page.getByRole('button', { name: 'Source · Credits', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await expect(page.getByRole('dialog')).toContainText('None in this view. These are direct 3D source renders.')
  await expect(page.getByRole('dialog')).toContainText('unchanged native-resolution 3D source render')
  await page.getByRole('button', { name: 'Copy link to this view' }).click()
  const sourceHash = new URL(page.url()).hash
  await page.reload()
  await ready(page, 'textured')
  expect(new URL(page.url()).hash).toBe(sourceHash)
  await page.getByRole('button', { name: 'Show Pixel city view', exact: true }).click()
  await ready(page, 'styled')
  await expect(page.getByRole('button', { name: 'AI preview · Credits', exact: true })).toBeVisible()
  for (const key of ['x', 'y', 'z']) expect(position(page).get(key)).toBe(original.get(key))
})
