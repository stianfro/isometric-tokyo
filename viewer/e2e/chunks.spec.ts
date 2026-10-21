import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { AtlasManifest } from '../src/atlas'

let fixture: { directory: string; manifest: AtlasManifest }

test.beforeAll(() => {
  const script = fileURLToPath(new URL('./generate_chunk_fixture.py', import.meta.url))
  const directory = resolve('../.cache/viewer-chunk-e2e', String(process.pid))
  fixture = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', [script, directory], { encoding: 'utf8' }))
})

async function useChunks(page: Page, mismatched = false) {
  const missing: string[] = []
  const errors: Error[] = []
  page.on('pageerror', (error) => errors.push(error))
  await page.route('**/atlas/manifest.json', (route) => route.fulfill({ json: fixture.manifest }))
  await page.route('**/atlas/**', async (route) => {
    const name = new URL(route.request().url()).pathname.split('/atlas/')[1]
    if (name === 'manifest.json') { await route.fulfill({ json: fixture.manifest }); return }
    try {
      let body = await readFile(resolve(fixture.directory, name))
      if (mismatched && name === 'geometry.dzi') body = Buffer.from(body.toString('utf8').replace('X="0"', 'X="1"'))
      await route.fulfill({ status: 200, contentType: name.endsWith('.dzi') ? 'application/xml' : 'image/webp', body })
    } catch {
      missing.push(name)
      await route.fulfill({ status: 404, body: 'No exported tile at this address' })
    }
  })
  return { missing, errors }
}

const map = (page: Page) => page.getByRole('region', { name: 'Synthetic fixture interactive map' })

test('real sparse chunk DZI loads aligned paired layers with signed global offsets', async ({ page }) => {
  const { missing, errors } = await useChunks(page)
  await page.goto('/#x=0.125&y=0.5&z=8&layer=source_texture.v1')
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  await expect(map(page)).toHaveAttribute('data-coverage', 'available')
  const before = new URL(page.url()).hash
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Geometry only', exact: true }).click()
  await page.getByRole('button', { name: 'Close map information' }).click()
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  await expect(map(page)).toHaveAttribute('data-layer', 'geometry')
  expect(new URL(page.url()).hash).toBe(before.replace('source_texture.v1', 'geometry'))
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await expect(page.getByRole('dialog')).toContainText('-128, 512, 384, 640')
  await expect(page.getByRole('dialog')).toContainText('Not verified. Rendered bounds do not prove complete geographic data.')
  await expect(page.getByRole('dialog')).toContainText('no single-image download')
  await expect(page.getByRole('dialog').getByRole('link', { name: /Download/ })).toHaveCount(0)
  expect(missing).toEqual([])
  expect(errors).toEqual([])
})

test('intentional sparse gaps finish loading without requesting missing tiles or claiming failure', async ({ page }) => {
  const { missing, errors } = await useChunks(page)
  await page.goto('/#x=0.5&y=0.5&z=8&layer=source_texture.v1')
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  await expect(map(page)).toHaveAttribute('data-coverage', 'empty')
  await expect(page.getByRole('status')).toContainText('No rendered chunks in this view.')
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await page.getByRole('button', { name: 'Reset view' }).click()
  await expect(map(page)).toHaveAttribute('data-coverage', 'available')
  await expect(map(page)).toHaveAttribute('data-ready', 'true')
  expect(missing).toEqual([])
  expect(errors).toEqual([])
})

test('shifted comparison-layer DisplayRects are rejected as source mismatch', async ({ page }) => {
  const { errors } = await useChunks(page, true)
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('This map layer could not open.')
  await expect(map(page)).toHaveAttribute('data-ready', 'false')
  expect(errors).toEqual([])
})
