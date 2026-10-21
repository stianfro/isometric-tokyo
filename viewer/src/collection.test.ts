import { describe, expect, it } from 'vitest'
import { formatCollectionView, isCollection, parseCollection, selectedCollectionView, validateCollectionAtlas } from './collection'
import { parseManifest } from './atlas'

const collection = {
  schema_version: 1, kind: 'atlas_collection', id: 'shinjuku-preview', title: 'Shinjuku preview', default_view: 'pixel-city',
  views: [
    { id: 'pixel-city', label: 'Pixel city', manifest: 'pixel-city/manifest.json', kind: 'illustration-preview' },
    { id: 'source', label: 'Source', manifest: 'source/manifest.json', kind: 'source' },
  ],
}
const atlas = {
  schema_version: 1, id: 'shinjuku-art', title: 'Shinjuku', width: 1254, height: 1254,
  tile_size: 256, overlap: 1, format: 'webp',
  layers: [{ id: 'illustration', label: 'Pixel city', dzi: 'illustration.dzi' }],
  attribution: [{ label: 'PLATEAU', url: 'https://www.mlit.go.jp/plateau/' }],
  coverage: { label: 'Shinjuku art preview', bbox: [139.68, 35.68, 139.70, 35.70] },
  source_edition: '2025', ai_styling: true, artwork_approved: false, source_coverage_verified: false,
}

describe('independent preview collection', () => {
  it('retains only the safe public catalog fields', () => {
    expect(parseCollection({ ...collection, extra: 'ignored' })).toEqual(collection)
    expect(isCollection(collection)).toBe(true)
    expect(isCollection(atlas)).toBe(false)
    expect(isCollection(null)).toBe(false)
  })
  it.each([
    { schema_version: 2 }, { kind: 'gallery' }, { id: '../bad' }, { title: '' },
    { views: [] }, { views: [collection.views[0]] }, { default_view: 'missing' },
    { views: [collection.views[0], collection.views[0]] },
    { views: [collection.views[0], { ...collection.views[1], manifest: collection.views[0].manifest }] },
    { views: collection.views.map((view) => ({ ...view, kind: 'illustration-preview' })) },
    { views: [collection.views[0], { ...collection.views[1], kind: 'verified' }] },
  ])('rejects invalid or source-free collections %j', (change) => {
    expect(() => parseCollection({ ...collection, ...change })).toThrow()
  })
  it.each(['../source.json', '/source.json', 'https://example.com/atlas.json', './source.json', 'source//manifest.json', 'source/manifest.json?x=1'])('keeps child manifests below the catalog directory: %s', (manifest) => {
    expect(() => parseCollection({ ...collection, views: [{ ...collection.views[0], manifest }, collection.views[1]] })).toThrow()
  })
  it('selects a valid shared view and safely defaults invalid IDs', () => {
    const parsed = parseCollection(collection)
    expect(selectedCollectionView(parsed, '#view=source').id).toBe('source')
    expect(selectedCollectionView(parsed, '#view=../source').id).toBe('pixel-city')
    expect(selectedCollectionView(parsed, '').id).toBe('pixel-city')
  })
  it('preserves view identity in shared coordinates but leaves legacy hashes unchanged', () => {
    const position = { x: 0.5, y: 0.6, z: 2, layer: 'illustration' }
    const hash = formatCollectionView(position, parseCollection(collection).views[0])
    expect(hash).toBe('#x=0.50000&y=0.60000&z=2.00000&layer=illustration&view=pixel-city')
    expect(formatCollectionView(position)).not.toContain('&view=')
  })
  it('requires explicit preview provenance without inventing geographic approval', () => {
    const view = parseCollection(collection).views[0]
    expect(() => validateCollectionAtlas(view, parseManifest(atlas))).not.toThrow()
    for (const change of [{ ai_styling: false }, { artwork_approved: true }, { artwork_approved: undefined }, { source_coverage_verified: true }, { source_coverage_verified: undefined }]) {
      expect(() => validateCollectionAtlas(view, parseManifest({ ...atlas, ...change }))).toThrow()
    }
    const source = parseCollection(collection).views[1]
    expect(() => validateCollectionAtlas(source, parseManifest(atlas))).toThrow()
    expect(() => validateCollectionAtlas(source, parseManifest({ ...atlas, ai_styling: false, width: 1024, height: 1024 }))).not.toThrow()
  })
})
