import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { AtlasManifest } from '../src/atlas'

let fixture: { directory: string; manifest: AtlasManifest }
const failures = new WeakMap<Page, string[]>()
test.beforeAll(() => {
  fixture = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', [fileURLToPath(new URL('./generate_expansion_fixture.py', import.meta.url)), resolve('../.cache/viewer-expansion-e2e', String(process.pid))], { encoding: 'utf8' }))
})
test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  failures.set(page, errors)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/atlas/**', async (route) => {
    const name = new URL(route.request().url()).pathname.split('/atlas/')[1]
    if (name === 'manifest.json') { await route.fulfill({ json: fixture.manifest }); return }
    try {
      const body = await readFile(resolve(fixture.directory, name))
      await route.fulfill({ status: 200, contentType: name.endsWith('.dzi') ? 'application/xml' : name.endsWith('.txt') ? 'text/plain' : 'image/webp', body })
    } catch {
      errors.push(`Unexpected missing sparse asset: ${name}`)
      await route.fulfill({ status: 404 })
    }
  })
})
test.afterEach(async ({ page }) => expect(failures.get(page)).toEqual([]))

const position = (page: Page) => new URLSearchParams(new URL(page.url()).hash.slice(1))
const map = (page: Page) => page.getByRole('region', { name: 'Shinjuku interactive map' })
async function ready(page: Page, layer = 'styled') {
  await expect(map(page)).toHaveAttribute('data-layer', layer)
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  await expect(page).toHaveURL(new RegExp(`layer=${layer}`))
}
function globalPosition(page: Page) {
  const p = position(page)
  return ['px', 'py', 'span'].map((key) => Number(p.get(key)))
}

test('larger sparse atlas keeps the original home and loads only its visible tile region', async ({ page }) => {
  const tiles: string[] = []
  page.on('request', (request) => { if (request.url().endsWith('.webp')) tiles.push(new URL(request.url()).pathname) })
  await page.goto('/')
  await ready(page)
  await page.waitForLoadState('networkidle')
  const size = page.viewportSize()!
  const span = 3072 * Math.min(1, size.width / size.height)
  expect(position(page).get('v')).toBe('2')
  expect(position(page).get('frame')).toBe('shinjuku-native-v1')
  expect(globalPosition(page).slice(0, 2)).toEqual([1536, 1536])
  expect(globalPosition(page)[2]).toBeCloseTo(span, 4)
  expect(tiles.length).toBeGreaterThan(0)
  expect(tiles.every((tile) => tile.includes('/styled_files/'))).toBe(true)
  const currentLevel = tiles.filter((tile) => tile.includes('/12/'))
  expect(currentLevel.length).toBeGreaterThan(0)
  expect(currentLevel.length).toBeLessThan(60)
  expect(tiles.some((tile) => tile.includes('/13/'))).toBe(false)
  for (const tile of currentLevel) {
    const [x, y] = tile.split('/').at(-1)!.replace('.webp', '').split('_').map(Number)
    expect(x).toBeGreaterThan(0)
    expect(x).toBeLessThan(9)
    expect(y).toBeGreaterThan(0)
    expect(y).toBeLessThan(9)
  }
  await expect(page.locator('.place-label, .masthead, .map-footer, .coverage-note')).toHaveCount(0)
  await expect(page.locator('#atlas-overview .openseadragon-container')).toHaveCount(0)
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect.poll(() => globalPosition(page)[2]).toBeLessThan(span)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await expect(page.getByRole('dialog')).toContainText('expanding Pixel city pilot')
  await page.getByRole('button', { name: 'Reset view', exact: true }).click()
  await expect.poll(() => globalPosition(page)[2]).toBeCloseTo(span, 4)
  expect(globalPosition(page).slice(0, 2)).toEqual([1536, 1536])
})

test('old native URLs migrate precisely and source history preserves location and scale', async ({ page }) => {
  await page.goto('/#x=.58&y=.52&z=4&layer=styled')
  await ready(page)
  expect(globalPosition(page)).toEqual([1781.76, 1597.44, 768])
  const before = new URL(page.url()).hash
  await page.getByRole('button', { name: 'Show Source view', exact: true }).click()
  await ready(page, 'textured')
  expect(globalPosition(page)).toEqual([1781.76, 1597.44, 768])
  const source = new URL(page.url()).hash
  await page.goBack()
  await ready(page)
  expect(new URL(page.url()).hash).toBe(before)
  await page.goForward()
  await ready(page, 'textured')
  expect(new URL(page.url()).hash).toBe(source)
  await page.reload()
  await ready(page, 'textured')
  expect(new URL(page.url()).hash).toBe(source)
  await expect(page.getByRole('button', { name: 'Source · Credits', exact: true })).toBeVisible()
})

test('negative global positions round trip through pan, zoom and browser history', async ({ page }) => {
  const initial = '#v=2&frame=shinjuku-native-v1&px=-512.000&py=1536.000&span=400.00000&layer=styled'
  await page.goto(`/${initial}`)
  await ready(page)
  expect(new URL(page.url()).hash).toBe(initial)
  await expect(map(page)).toHaveAttribute('data-coverage', 'available')
  await map(page).focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => globalPosition(page)[0]).toBeGreaterThan(-512)
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect.poll(() => globalPosition(page)[2]).toBeLessThan(400)
  const moved = new URL(page.url()).hash
  await page.reload()
  await ready(page)
  expect(new URL(page.url()).hash).toBe(moved)
  await page.evaluate((hash) => { location.hash = hash }, initial)
  await ready(page)
  await expect.poll(() => globalPosition(page)).toEqual([-512, 1536, 400])
  await page.goBack()
  await ready(page)
  expect(new URL(page.url()).hash).toBe(moved)
  await page.goForward()
  await ready(page)
  expect(new URL(page.url()).hash).toBe(initial)
})

test('unrendered expansion cells remain honest gaps and reset returns to the original home', async ({ page }) => {
  await page.goto('/#v=2&frame=shinjuku-native-v1&px=1536&py=3584&span=300&layer=styled')
  await ready(page)
  await expect(map(page)).toHaveAttribute('data-coverage', 'empty')
  await expect(page.getByRole('status')).toContainText('No rendered chunks')
  await expect(page.getByRole('alert')).toHaveCount(0)
  await map(page).focus()
  await page.keyboard.press('Home')
  await expect(map(page)).toHaveAttribute('data-coverage', 'available')
  await expect.poll(() => globalPosition(page).slice(0, 2)).toEqual([1536, 1536])
})

test('historical unregistered illustration links open art at the stable home, not a false alignment', async ({ page }) => {
  await page.goto('/#view=pixel-city&x=.8&y=.2&z=4&layer=illustration')
  await ready(page)
  expect(globalPosition(page).slice(0, 2)).toEqual([1536, 1536])
  expect(position(page).get('view')).toBeNull()
})

test('a URL for another coordinate frame cannot silently move this atlas', async ({ page }) => {
  await page.goto('/#v=2&frame=another-city&px=-512&py=1536&span=400&layer=styled&x=.2&y=.2&z=4')
  await ready(page)
  expect(globalPosition(page).slice(0, 2)).toEqual([1536, 1536])
  expect(position(page).get('frame')).toBe('shinjuku-native-v1')
})
