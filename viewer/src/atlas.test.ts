import { describe, expect, it } from 'vitest'
import { atlasToImage, formatView, imageToAtlas, parseManifest, parseView, resolveManifestUrl, validateDescriptor, viewportHasCoverage } from './atlas'

describe('manifest configuration', () => {
  const base = 'https://example.com/viewer/'
  it('uses the existing atlas by default', () => {
    expect(resolveManifestUrl(undefined, base)).toBe(`${base}atlas/manifest.json`)
  })
  it('resolves an explicit chunk release below the app directory', () => {
    expect(resolveManifestUrl('chunks/current/manifest.json', `${base}index.html`)).toBe(`${base}chunks/current/manifest.json`)
  })
  it.each(['', ' ', '../manifest.json', 'chunks/../manifest.json', '/manifest.json', '//example.com/manifest.json', 'https://example.com/manifest.json', 'data:application/json,{}', 'chunks/%2e%2e/manifest.json', 'chunks\\manifest.json', 'chunks//manifest.json', './manifest.json', 'chunks/./manifest.json', 'chunks/', 'manifest.json?other=1', 'manifest.json#other', null, false, 123])('rejects unsafe or empty configuration %j', (path) => {
    expect(() => resolveManifestUrl(path, base)).toThrow('VITE_ATLAS_MANIFEST')
  })
})

const fixture = {
  schema_version: 1, id: 'test-atlas', title: 'Shinjuku', width: 2400, height: 2400,
  tile_size: 256, overlap: 1, format: 'webp',
  layers: [{ id: 'textured', label: 'Source textures', dzi: 'textured.dzi', original: 'originals/source.png' }],
  attribution: [{ label: 'PLATEAU', url: 'https://www.mlit.go.jp/plateau/' }],
  coverage: { label: 'Shinjuku', bbox: [139.68, 35.68, 139.70, 35.70] },
  source_edition: '2025', ai_styling: false,
}

describe('atlas manifest validation', () => {
  it('reads a valid manifest without retaining unspecified fields', () => {
    expect(parseManifest({ ...fixture, extra: 'not carried into application' })).toEqual(fixture)
  })
  it.each([
    { schema_version: 2 }, { width: 0 }, { width: 1.5 }, { height: Infinity },
    { tile_size: 12 }, { overlap: -1 }, { format: 'gif' }, { layers: [] },
    { ai_styling: 'no' }, { attribution: [] }, { id: '../atlas' },
    { coverage: { label: 'invalid', bbox: [140, 36, 139, 35] } },
    { layers: [fixture.layers[0], fixture.layers[0]] },
    { attribution: [{ label: 'bad', url: 'javascript:alert(1)' }] },
  ])('rejects invalid manifest fields %j', (change) => {
    expect(() => parseManifest({ ...fixture, ...change })).toThrow()
  })
  it.each(['../secret.dzi', '/external.dzi', 'https://example.com/map.dzi', 'folder/%2e%2e/file.dzi', '//host/file'])('rejects unsafe asset path %s', (dzi) => {
    expect(() => parseManifest({ ...fixture, layers: [{ ...fixture.layers[0], dzi }] })).toThrow()
  })
})

describe('shareable image coordinates', () => {
  it('round trips position, zoom and layer', () => {
    const view = { x: 0.34567, y: 0.54321, z: 2.5, layer: 'geometry' }
    expect(parseView(formatView(view), ['textured', 'geometry'])).toEqual(view)
  })
  it.each(['', '#x=1&y=0.5&z=1', '#x=&y=.5&z=1&layer=textured', '#x=NaN&y=.5&z=1&layer=textured', '#x=Infinity&y=.5&z=1&layer=textured', '#x=-1&y=.5&z=1&layer=textured', '#x=.5&y=1.01&z=1&layer=textured', '#x=.5&y=.5&z=999&layer=textured', '#x=.5&y=.5&z=.01&layer=textured', '#x=.5&y=.5&z=1&layer=missing'])('falls back on invalid URL state %s', (hash) => {
    expect(parseView(hash, ['textured', 'geometry'])).toBeNull()
  })
  it('clamps numerical rounding at image bounds', () => {
    expect(parseView(formatView({ x: -0.1, y: 1.01, z: 20, layer: 'textured' }), ['textured'])).toEqual({ x: 0, y: 1, z: 16, layer: 'textured' })
  })
})

const chunkFixture = {
  ...fixture,
  id: 'chunks-source_v1.0', width: 400, height: 200, tile_size: 32,
  bounds_px: [-200, 800, 200, 1000],
  place: { name: 'Shinjuku', name_ja: '新宿' },
  coverage: { ...fixture.coverage, rectangles_px: [[-200, 800, -100, 1000], [100, 800, 200, 1000]] },
  artwork_approved: false, source_coverage_verified: false,
  layers: [{ id: 'source_texture.v1', label: 'Source textures', dzi: 'source_texture.v1.dzi' }],
}

describe('chunk-rendered atlas contract', () => {
  it('allows a source export without fabricated whole-image originals', () => {
    const atlas = parseManifest(chunkFixture)
    expect(atlas.layers[0].original).toBeUndefined()
    expect(atlas.bounds_px).toEqual([-200, 800, 200, 1000])
    expect(atlas.artwork_approved).toBe(false)
  })
  it.each([
    { bounds_px: [0, 0, 400, 201] },
    { bounds_px: [400, 0, 0, 200] },
    { bounds_px: [-200.5, 800, 199.5, 1000] },
    { coverage: { ...fixture.coverage, rectangles_px: [] } },
    { coverage: { ...fixture.coverage, rectangles_px: [[-201, 800, 200, 1000]] } },
    { coverage: { ...fixture.coverage, rectangles_px: [[-200, 800, 200, 1000], [-200, 800, 200, 1000]] } },
    { artwork_approved: 'yes' },
    { source_coverage_verified: 1 },
    { place: { name: '' } },
  ])('rejects inconsistent chunk metadata %j', (change) => {
    expect(() => parseManifest({ ...chunkFixture, ...change })).toThrow()
  })
  it('requires explicit global bounds when sparse coverage is declared', () => {
    expect(() => parseManifest({ ...chunkFixture, bounds_px: undefined })).toThrow()
  })
  it('preserves global pixel offsets without shifting local DZI navigation', () => {
    const atlas = parseManifest(chunkFixture)
    expect(imageToAtlas(atlas, 0, 0)).toEqual([-200, 800])
    expect(imageToAtlas(atlas, 0.75, 0.25)).toEqual([100, 850])
    expect(atlasToImage(atlas, 100, 850)).toEqual([0.75, 0.25])
  })
  it('distinguishes empty sparse regions from covered regions and outside the full image', () => {
    const atlas = parseManifest(chunkFixture)
    expect(viewportHasCoverage(atlas, { x: 0, y: 0, width: 0.2, height: 0.2 })).toBe(true)
    expect(viewportHasCoverage(atlas, { x: 0.3, y: 0.1, width: 0.3, height: 0.2 })).toBe(false)
    expect(viewportHasCoverage(atlas, { x: 0.9, y: 0.2, width: 0.2, height: 0.2 })).toBe(true)
    expect(viewportHasCoverage(atlas, { x: 0, y: 0.6, width: 0.2, height: 0.2 })).toBe(false)
  })
  it('matches each sparse DZI descriptor against global coverage in local image coordinates', () => {
    const atlas = parseManifest(chunkFixture)
    const source = {
      width: 400, height: 200, tile_size: 32, tile_height: 32, overlap: 1, format: 'webp', max_level: 9,
      rectangles: [
        { x: 0, y: 0, width: 100, height: 200, minLevel: 0, maxLevel: 9 },
        { x: 300, y: 0, width: 100, height: 200, minLevel: 0, maxLevel: 9 },
      ],
    }
    expect(() => validateDescriptor(atlas, source)).not.toThrow()
    expect(() => validateDescriptor(atlas, { ...source, rectangles: [...source.rectangles].reverse() })).not.toThrow()
    for (const change of [{ width: 401 }, { tile_size: 256 }, { tile_height: 16 }, { overlap: 0 }, { format: 'png' }, { rectangles: [] }, { max_level: 10 }]) {
      expect(() => validateDescriptor(atlas, { ...source, ...change })).toThrow()
    }
    expect(() => validateDescriptor(atlas, { ...source, rectangles: [{ ...source.rectangles[0], x: -200 }, source.rectangles[1]] })).toThrow()
    expect(() => validateDescriptor(atlas, { ...source, rectangles: [{ ...source.rectangles[0], minLevel: 1 }, source.rectangles[1]] })).toThrow()
  })
})
