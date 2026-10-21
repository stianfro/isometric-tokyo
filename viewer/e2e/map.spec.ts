import { test, expect, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'

const errors = new WeakMap<Page, Error[]>()
test.beforeEach(async ({ page }) => {
  const recorded: Error[] = []
  errors.set(page, recorded)
  page.on('pageerror', (error) => recorded.push(error))
})
test.afterEach(async ({ page }) => { expect(errors.get(page)).toEqual([]) })

const map = (page: Page) => page.getByRole('region', { name: /interactive map$/ })
const state = (page: Page) => {
  const params = new URLSearchParams(new URL(page.url()).hash.slice(1))
  return { x: Number(params.get('x')), y: Number(params.get('y')), z: Number(params.get('z')), layer: params.get('layer') }
}
async function ready(page: Page) {
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  await expect(page).toHaveURL(/#x=/)
}

test('tiles render, layer stays aligned, shared URL restores the same viewport', async ({ page }, testInfo) => {
  const pngRequests: string[] = []
  page.on('request', (request) => { if (request.url().endsWith('.png')) pngRequests.push(request.url()) })
  await page.goto('/')
  await ready(page)
  if (process.env.SCREENSHOT_DIR) {
    await mkdir(process.env.SCREENSHOT_DIR, { recursive: true })
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, `atlas-${testInfo.project.name}.png`) })
  }
  const targetZoom = state(page).z * 1.5
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect.poll(() => state(page).z).toBeCloseTo(targetZoom, 4)
  await ready(page)
  const before = state(page)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Geometry only', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Geometry only', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Close map information' }).click()
  await expect.poll(() => state(page).layer).toBe('geometry')
  await ready(page)
  const after = state(page)
  expect(after.x).toBeCloseTo(before.x, 4)
  expect(after.y).toBeCloseTo(before.y, 4)
  expect(after.z).toBeCloseTo(before.z, 4)
  const shared = page.url()
  await page.goto(shared)
  await ready(page)
  expect(state(page)).toEqual(after)
  await expect(map(page)).toHaveAttribute('data-layer', 'geometry')
  expect(pngRequests).toHaveLength(0)
})

test('keyboard, overview, reset, credits and original download work', async ({ page }) => {
  await page.goto('/#x=0.5&y=0.5&z=2&layer=textured')
  await ready(page)
  await map(page).focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => state(page).x).toBeGreaterThan(0.5)
  await page.keyboard.press('+')
  await expect.poll(() => state(page).z).toBeGreaterThan(2)
  await expect(page.getByRole('complementary', { name: 'Overview map' })).toHaveCount(0)
  await expect(page.locator('#atlas-overview .openseadragon-container')).toHaveCount(0)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Toggle overview map' }).click()
  await expect(page.getByRole('complementary', { name: 'Overview map' })).toBeVisible()
  await ready(page)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Toggle overview map' }).click()
  await expect(page.getByRole('complementary', { name: 'Overview map' })).toHaveCount(0)
  await ready(page)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Reset view' }).click()
  await expect.poll(() => state(page).x).toBeCloseTo(0.5, 4)
  await expect.poll(() => state(page).z).toBeLessThan(2)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('None. These are direct 3D source renders.')).toBeVisible()
  await expect(dialog.getByRole('link', { name: /Download source textures PNG/ })).toHaveAttribute('href', /originals\/shinjuku-isometric.png$/)
  await page.getByRole('button', { name: 'Close map information' }).click()
  await expect(dialog).not.toBeVisible()
})

test('wheel or touch pinch zooms and dragging pans the image', async ({ page, isMobile }) => {
  await page.goto('/#x=0.5&y=0.5&z=2&layer=textured')
  await ready(page)
  const center = { x: isMobile ? 180 : 700, y: isMobile ? 370 : 450 }
  if (isMobile) {
    const cdp = await page.context().newCDPSession(page)
    const points = (distance: number) => [
      { x: center.x - distance, y: center.y, id: 1 },
      { x: center.x + distance, y: center.y, id: 2 },
    ]
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: points(30) })
    for (const distance of [40, 55, 70, 85]) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: points(distance) })
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await expect.poll(() => state(page).z).toBeGreaterThan(2.2)
    const before = state(page).x
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: center.x, y: center.y, id: 1 }] })
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: center.x + 45, y: center.y, id: 1 }] })
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await expect.poll(() => state(page).x).toBeLessThan(before)
  } else {
    await page.mouse.move(center.x, center.y)
    await page.mouse.wheel(0, -300)
    await expect.poll(() => state(page).z).toBeGreaterThan(2)
    const before = state(page).x
    await page.mouse.down()
    await page.mouse.move(center.x + 100, center.y, { steps: 10 })
    await page.mouse.up()
    await expect.poll(() => state(page).x).toBeLessThan(before)
  }
})

test('invalid URL state uses a safe home view', async ({ page }) => {
  await page.goto('/#x=NaN&y=-20&z=999&layer=unknown')
  await ready(page)
  expect(state(page).x).toBeGreaterThanOrEqual(0)
  expect(state(page).x).toBeLessThanOrEqual(1)
  expect(state(page).layer).toBe('textured')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('manifest failures give a working retry', async ({ page }) => {
  await page.route('**/atlas/manifest.json', (route) => route.fulfill({ status: 503, body: 'Unavailable' }))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('The map could not load.')
  await page.unroute('**/atlas/manifest.json')
  await page.getByRole('button', { name: 'Try again' }).click()
  await ready(page)
})

test('missing tiles are not represented as empty coverage and can recover', async ({ page }) => {
  await page.route('**/textured_files/**', (route) => route.fulfill({ status: 404, body: 'Missing' }))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('Some map tiles could not load.')
  await page.unroute('**/textured_files/**')
  await page.getByRole('button', { name: 'Retry map' }).click()
  await ready(page)
  await expect(page.getByRole('alert')).toHaveCount(0)
})
