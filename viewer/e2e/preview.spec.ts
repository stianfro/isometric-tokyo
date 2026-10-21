import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFile, mkdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

let fixture: { directory: string }
const errors = new WeakMap<Page, Error[]>()
test.beforeAll(() => {
  const script = fileURLToPath(new URL('./generate_preview_fixture.py', import.meta.url))
  fixture = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', [script, resolve('../.cache/viewer-preview-e2e', String(process.pid))], { encoding: 'utf8' }))
})
test.beforeEach(async ({ page }) => {
  const recorded: Error[] = []
  errors.set(page, recorded)
  page.on('pageerror', (error) => recorded.push(error))
  await page.route('**/atlas/**', async (route) => {
    const name = new URL(route.request().url()).pathname.split('/atlas/')[1]
    try {
      const body = await readFile(resolve(fixture.directory, name))
      await route.fulfill({ status: 200, contentType: name.endsWith('.json') ? 'application/json' : name.endsWith('.dzi') ? 'application/xml' : name.endsWith('.txt') ? 'text/plain' : 'image/webp', body })
    } catch {
      await route.fulfill({ status: 404, body: 'Missing preview fixture file' })
    }
  })
})
test.afterEach(async ({ page }) => { expect(errors.get(page)).toEqual([]) })

const map = (page: Page) => page.getByRole('region', { name: 'Shinjuku interactive map' })
const viewButton = (page: Page, view: 'Source' | 'Pixel city') => page.getByRole('button', { name: `Show ${view} view`, exact: true })
async function ready(page: Page, view: string) {
  await expect(map(page)).toHaveAttribute('data-view', view)
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  await expect(page).toHaveURL(new RegExp(`view=${view}`))
}

const position = (page: Page) => new URLSearchParams(new URL(page.url()).hash.slice(1))

test('art preview is explorable, explicitly unverified and retains source access', async ({ page }, testInfo) => {
  await page.goto('/')
  await ready(page, 'pixel-city')
  await expect(page.getByRole('button', { name: 'AI preview · Credits', exact: true })).toBeVisible()
  await expect(page.locator('.place-label, .masthead, .map-footer, .coverage-note, .source-tag, .view-switcher')).toHaveCount(0)
  await expect(page.getByRole('complementary', { name: 'Overview map' })).toHaveCount(0)
  await expect(page.locator('#atlas-overview .openseadragon-container')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Reset view', exact: true })).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Map layers' })).toHaveCount(0)
  await expect(viewButton(page, 'Source')).toBeVisible()
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await expect(page.getByRole('dialog')).toContainText('AI illustration. Geography not verified.')
  await expect(page.getByRole('dialog')).toContainText('not pixel-aligned')
  await expect(page.getByRole('dialog')).toContainText('Geographic fidelity and production artwork are not approved')
  await expect(page.getByRole('link', { name: /Full source terms/ })).toHaveAttribute('href', /atlas\/pixel-city\/NOTICE.txt$/)
  await page.getByRole('button', { name: 'Close map information' }).click()
  if (process.env.SCREENSHOT_DIR) {
    await mkdir(process.env.SCREENSHOT_DIR, { recursive: true })
    await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, `preview-fixture-${testInfo.project.name}.png`) })
  }
})

test('independent source view resets framing, shared art view reloads and browser back restores it', async ({ page }) => {
  await page.goto('/#x=0.6&y=0.55&z=3&layer=illustration&view=pixel-city')
  await ready(page, 'pixel-city')
  const artHash = new URL(page.url()).hash
  await viewButton(page, 'Source').click()
  await ready(page, 'source')
  expect(Number(position(page).get('x'))).toBeCloseTo(0.5, 4)
  expect(Number(position(page).get('y'))).toBeCloseTo(0.5, 4)
  expect(Number(position(page).get('z'))).not.toBeCloseTo(3, 4)
  expect(position(page).get('layer')).toBe('textured')
  await expect(page.getByRole('button', { name: 'Source · Credits', exact: true })).toBeVisible()
  await page.goBack()
  await ready(page, 'pixel-city')
  expect(new URL(page.url()).hash).toBe(artHash)
  await page.reload()
  await ready(page, 'pixel-city')
  expect(new URL(page.url()).hash).toBe(artHash)
})

test('failed source can retry and does not trap visitors away from the art view', async ({ page }) => {
  await page.route('**/source/manifest.json', (route) => route.fulfill({ status: 503, body: 'Unavailable' }))
  await page.goto('/#view=source')
  await expect(page.getByRole('alert')).toContainText('The map could not load.')
  await page.unroute('**/source/manifest.json')
  await page.getByRole('button', { name: 'Try again' }).click()
  await ready(page, 'source')
  await viewButton(page, 'Pixel city').click()
  await ready(page, 'pixel-city')
})

test('a collection cannot mislabel AI imagery as an unstyled source', async ({ page }) => {
  const source = JSON.parse(await readFile(join(fixture.directory, 'source/manifest.json'), 'utf8'))
  await page.route('**/source/manifest.json', (route) => route.fulfill({ json: { ...source, ai_styling: true } }))
  await page.goto('/#view=source')
  await expect(page.getByRole('alert')).toContainText('The map could not load.')
  await expect(map(page)).toHaveCount(0)
  await viewButton(page, 'Pixel city').click()
  await ready(page, 'pixel-city')
})


test('only a small toolbar overlays the full-viewport map at 360px', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/')
  await ready(page, 'pixel-city')
  const initialZoom = Number(position(page).get('z'))
  expect(initialZoom).toBeCloseTo(780 / 360, 4)
  const canvas = await map(page).boundingBox()
  expect(canvas).toEqual({ x: 0, y: 0, width: 360, height: 780 })
  const toolbar = page.getByRole('navigation', { name: 'Map navigation' })
  const bounds = await toolbar.boundingBox()
  expect(bounds!.width).toBeLessThan(344)
  expect(bounds!.height).toBeLessThan(48)
  expect(bounds!.x + bounds!.width).toBeLessThan(360)
  await expect(toolbar.getByRole('button')).toHaveCount(4)
  for (const button of await toolbar.getByRole('button').all()) {
    const box = await button.boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(36)
  }
  await expect(page.getByRole('contentinfo')).toHaveCount(0)
  await expect(page.getByRole('banner')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Shinjuku', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect.poll(() => Number(position(page).get('z'))).toBeGreaterThan(initialZoom)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Reset view', exact: true }).click()
  await expect.poll(() => Number(position(page).get('z'))).toBeCloseTo(initialZoom, 4)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Copy link to this view' }).click()
  await expect(page.getByRole('status')).toContainText(/Link copied|Copy its URL/)
})
