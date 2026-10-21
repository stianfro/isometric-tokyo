import { describe, expect, it } from 'vitest'
import { formatView, parseManifest, parseView } from './atlas'

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
