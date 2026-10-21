import { describe, expect, it } from 'vitest'
import { imageToAtlas, parseManifest } from './atlas'
import { formatAtlasView, framedHome, framedZoomLimits, parseAtlasView } from './framing'

const frame = { id: 'shinjuku-native-v1', units: 'pixels' }
const fixture = {
  schema_version: 1, id: 'expanded-native', title: 'Shinjuku', width: 5120, height: 5120,
  tile_size: 256, overlap: 1, format: 'webp', ai_styling: true,
  comparison_mode: 'reviewed-approximate',
  layers: [{ id: 'styled', label: 'Pixel city', dzi: 'styled.dzi' }, { id: 'textured', label: 'Source', dzi: 'textured.dzi' }],
  attribution: [{ label: 'Fixture', url: 'https://example.com/' }],
  coverage: { label: 'Synthetic', bbox: [139.68, 35.68, 139.70, 35.70] },
  source_edition: 'Synthetic test pixels',
  bounds_px: [-1024, -1024, 4096, 4096], coordinate_frame: frame,
  legacy_view_bounds: [0, 0, 3072, 3072], home_bounds: [0, 0, 3072, 3072],
}
const atlas = parseManifest(fixture)

describe('stable coordinate frame metadata', () => {
  it('accepts shared native artwork and compatible deterministic source atlases', () => {
    expect(atlas.coordinate_frame).toEqual(frame)
    expect(parseManifest({ ...fixture, ai_styling: false, comparison_mode: undefined, layers: [fixture.layers[1]] }).home_bounds).toEqual([0, 0, 3072, 3072])
  })
  it.each([
    { bounds_px: undefined }, { coordinate_frame: undefined }, { home_bounds: undefined }, { legacy_view_bounds: undefined },
    { coordinate_frame: null }, { coordinate_frame: [] }, { coordinate_frame: { ...frame, id: '../other' } },
    { coordinate_frame: { ...frame, id: true } }, { coordinate_frame: { ...frame, units: 'meters' } },
    { home_bounds: [0, 0, Infinity, 3072] }, { home_bounds: [0, 0, 3072.5, 3072] }, { home_bounds: [0, 0, 0, 3072] },
    { legacy_view_bounds: [-1025, 0, 3072, 3072] }, { home_bounds: [0, 0, 4097, 3072] },
    { comparison_mode: undefined }, { layers: [fixture.layers[0]] },
  ])('rejects malformed or incompatible metadata %j', (change) => {
    expect(() => parseManifest({ ...fixture, ...change })).toThrow()
  })
})

describe('global pixel URL v2', () => {
  it('migrates normalized native URLs against the original 3072 frame, not the expanded image', () => {
    for (const suffix of ['', '&view=pixel-city']) {
      const view = parseAtlasView(atlas, `#x=.58&y=.52&z=4&layer=styled${suffix}`)!
      expect(imageToAtlas(atlas, view.x, view.y)[0]).toBeCloseTo(1781.76)
      expect(imageToAtlas(atlas, view.x, view.y)[1]).toBeCloseTo(1597.44)
      expect(atlas.width / view.z).toBe(768)
      expect(formatAtlasView(atlas, view)).toBe('#v=2&frame=shinjuku-native-v1&px=1781.760&py=1597.440&span=768.00000&layer=styled')
    }
  })
  it('round trips negative global coordinates and unchanged scale after further expansion', () => {
    const hash = '#v=2&frame=shinjuku-native-v1&px=-512.125&py=1536.000&span=900.00000&layer=textured'
    const view = parseAtlasView(atlas, hash)!
    expect(formatAtlasView(atlas, view)).toBe(hash)
    const larger = parseManifest({ ...fixture, width: 7168, height: 7168, bounds_px: [-2048, -2048, 5120, 5120] })
    const expanded = parseAtlasView(larger, hash)!
    expect(imageToAtlas(larger, expanded.x, expanded.y)).toEqual(imageToAtlas(atlas, view.x, view.y))
    expect(larger.width / expanded.z).toBeCloseTo(atlas.width / view.z)
    expect(formatAtlasView(larger, expanded)).toBe(hash)
  })
  it.each([
    'v=3', 'frame=other', 'px=NaN', 'py=Infinity', 'span=0', 'span=-1', 'span=999999',
    'px=-1025', 'py=4097', 'span=', 'layer=missing', 'v=02',
  ])('rejects invalid versioned URL field %s without interpreting it as legacy', (field) => {
    const params = new URLSearchParams('v=2&frame=shinjuku-native-v1&px=1536&py=1536&span=3072&layer=styled&x=.5&y=.5&z=1')
    const [key, value] = field.split('=')
    params.set(key, value)
    expect(parseAtlasView(atlas, `#${params}`)).toBeNull()
  })
  it('rejects ambiguous repeated keys and incomplete versioned URLs', () => {
    expect(parseAtlasView(atlas, '#v=2&v=2&frame=shinjuku-native-v1&px=0&py=0&span=3072&layer=styled')).toBeNull()
    expect(parseAtlasView(atlas, '#v=2&frame=shinjuku-native-v1&px=0&span=3072&layer=styled')).toBeNull()
  })
  it('does not pretend unregistered historical illustration links share this frame', () => {
    expect(parseAtlasView(atlas, '#view=pixel-city&x=.8&y=.2&z=4&layer=illustration')).toBeNull()
  })
  it('keeps metadata-free legacy atlases on their existing URL format', () => {
    const legacy = parseManifest({ ...fixture, coordinate_frame: undefined, home_bounds: undefined, legacy_view_bounds: undefined })
    const view = { x: .5, y: .5, z: 2, layer: 'styled' }
    expect(parseAtlasView(legacy, formatAtlasView(legacy, view))).toEqual(view)
    expect(formatAtlasView(legacy, view)).toBe('#x=0.50000&y=0.50000&z=2.00000&layer=styled')
  })
})

describe('stable opening frame and zoom limits', () => {
  it.each([[1440, 1000, 3072], [360, 780, 3072 * 360 / 780]])('keeps the old home center and cover scale at %i by %i', (width, height, span) => {
    const home = framedHome(atlas, width, height, 'styled')!
    expect(imageToAtlas(atlas, home.x, home.y)).toEqual([1536, 1536])
    expect(atlas.width / home.z).toBeCloseTo(span)
  })
  it('expresses zoom limits in the original frame rather than the larger export', () => {
    expect(framedZoomLimits(atlas).minZoomLevel).toBeCloseTo(.1 * 5120 / 3072)
    expect(framedZoomLimits(atlas).maxZoomLevel).toBeCloseTo(16 * 5120 / 3072)
  })
})
