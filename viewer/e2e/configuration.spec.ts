import { test as base, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { cp, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer, type ViteDevServer } from 'vite'

// These isolated servers read real Vite environment files and serve real exports.
// They do not replace the active sample, share an environment or use a fixed port.
const test = base.extend<{ configuredApp: (manifest: string) => Promise<string> }>({
  configuredApp: async ({ browserName }, run) => {
    await mkdir(resolve('../.cache'), { recursive: true })
    const directory = await mkdtemp(resolve(`../.cache/viewer-config-e2e-${browserName}-`))
    const servers: ViteDevServer[] = []
    try {
      await run(async (manifest) => {
        const instance = join(directory, String(servers.length))
        await mkdir(instance, { recursive: true })
        await writeFile(join(instance, '.env'), `VITE_ATLAS_MANIFEST=${JSON.stringify(manifest)}\n`)
        if (manifest === 'chunks/current/manifest.json') {
          const script = fileURLToPath(new URL('./generate_chunk_fixture.py', import.meta.url))
          const fixture = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', [script, join(instance, 'fixture')], { encoding: 'utf8' })) as { directory: string }
          await cp(fixture.directory, join(instance, 'public/chunks/current'), { recursive: true })
        }
        const server = await createServer({
          configFile: resolve('vite.config.ts'),
          envDir: instance,
          publicDir: join(instance, 'public'),
          cacheDir: join(instance, 'vite'),
          server: { host: '127.0.0.1', port: 0, strictPort: false, hmr: false },
          logLevel: 'error',
        })
        servers.push(server)
        await server.listen()
        const address = server.httpServer?.address()
        if (!address || typeof address === 'string') throw new Error('Missing isolated test-server address')
        return `http://127.0.0.1:${address.port}`
      })
    } finally {
      await Promise.all(servers.map((server) => server.close()))
      await rm(directory, { recursive: true, force: true })
    }
  },
})

test('configured release loads actual exported files and keeps notice paths relative', async ({ page, configuredApp }) => {
  const url = await configuredApp('chunks/current/manifest.json')
  const requested: string[] = []
  const failures: string[] = []
  const errors: Error[] = []
  page.on('request', (request) => requested.push(new URL(request.url()).pathname))
  page.on('response', (response) => { if (response.status() >= 400) failures.push(response.url()) })
  page.on('pageerror', (error) => errors.push(error))
  await page.goto(`${url}/#x=0.125&y=0.5&z=8&layer=source_texture.v1`)
  const map = page.getByRole('region', { name: 'Synthetic fixture interactive map' })
  await expect(map).toHaveAttribute('data-ready', 'true')
  await page.getByRole('button', { name: 'Geometry only', exact: true }).click()
  await expect(map).toHaveAttribute('data-layer', 'geometry')
  await expect(map).toHaveAttribute('data-ready', 'true')
  await page.getByRole('button', { name: 'About this map and credits' }).click()
  await expect(page.getByRole('link', { name: /Full source terms/ })).toHaveAttribute('href', `${url}/chunks/current/NOTICE.txt`)
  expect(requested).toContain('/chunks/current/manifest.json')
  expect(requested.some((path) => path.startsWith('/chunks/current/') && path.endsWith('.webp'))).toBe(true)
  expect(requested.some((path) => path.startsWith('/atlas/'))).toBe(false)
  expect(failures).toEqual([])
  expect(errors).toEqual([])
})

for (const manifest of ['https://example.com/manifest.json', '../manifest.json']) {
  test(`unsafe configured manifest shows startup error without a request: ${manifest}`, async ({ page, configuredApp }) => {
    const url = await configuredApp(manifest)
    const atlasRequests: string[] = []
    const errors: Error[] = []
    page.on('request', (request) => { if (/manifest\.json|\.dzi(?:$|\?)/.test(request.url())) atlasRequests.push(request.url()) })
    page.on('pageerror', (error) => errors.push(error))
    await page.goto(url)
    await expect(page.getByRole('alert')).toContainText('The map configuration is invalid.')
    await expect(page.getByRole('alert')).toContainText('VITE_ATLAS_MANIFEST')
    await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0)
    expect(atlasRequests).toEqual([])
    expect(errors).toEqual([])
  })
}
