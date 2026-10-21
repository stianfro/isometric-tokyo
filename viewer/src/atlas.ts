export type Bounds = [number, number, number, number]

export interface AtlasLayer {
  id: string
  label: string
  dzi: string
  original?: string
}

export interface AtlasManifest {
  schema_version: 1
  id: string
  title: string
  width: number
  height: number
  tile_size: number
  overlap: number
  format: 'webp' | 'png' | 'jpg'
  layers: AtlasLayer[]
  attribution: { label: string; url: string }[]
  coverage: { label: string; bbox: Bounds; rectangles_px?: Bounds[] }
  bounds_px?: Bounds
  place?: { name: string; name_ja?: string }
  artwork_approved?: boolean
  source_coverage_verified?: boolean
  source_edition: string
  ai_styling: boolean
  comparison_mode?: 'shared-framing-unverified' | 'reviewed-approximate'
  modifications?: string
  limitations?: string
  notice?: string
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected an object')
  return value as Record<string, unknown>
}

function label(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 1000) throw new Error('Invalid text')
  return value
}

function identifier(value: unknown): string {
  const result = label(value)
  if (!/^[a-z0-9][a-z0-9._-]{0,95}$/.test(result)) throw new Error('Invalid identifier')
  return result
}

function integer(value: unknown, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) {
    throw new Error('Invalid image dimensions')
  }
  return value
}

/** Assets must stay within the manifest's directory, never load a remote URL. */
function assetPath(value: unknown): string {
  const result = label(value)
  if (!/^[a-zA-Z0-9_./-]+$/.test(result) || result.startsWith('/') || result.split('/').includes('..')) {
    throw new Error('Invalid atlas asset path')
  }
  return result
}

/** Build-time selection stays below the application's own URL directory. */
export function resolveManifestUrl(value: unknown, baseURI: string): string {
  const path = value === undefined ? 'atlas/manifest.json' : value
  if (typeof path !== 'string' || !path || path.length > 1000 || !/^[a-zA-Z0-9_./-]+$/.test(path) || path.split('/').some((part) => !part || part === '.' || part === '..')) {
    throw new Error('VITE_ATLAS_MANIFEST must be a relative file path without empty segments, dot segments, URL syntax or a leading slash.')
  }
  return new URL(path, baseURI).href
}

export function parseManifest(input: unknown): AtlasManifest {
  const data = record(input)
  if (data.schema_version !== 1) throw new Error('Unsupported atlas version')
  if (!Array.isArray(data.layers) || data.layers.length < 1 || data.layers.length > 8) throw new Error('Invalid layers')
  const layers = data.layers.map((raw) => {
    const layer = record(raw)
    return { id: identifier(layer.id), label: label(layer.label), dzi: assetPath(layer.dzi), ...(layer.original === undefined ? {} : { original: assetPath(layer.original) }) }
  })
  if (new Set(layers.map((layer) => layer.id)).size !== layers.length) throw new Error('Duplicate layers')
  if (!Array.isArray(data.attribution) || !data.attribution.length) throw new Error('Missing source attribution')
  const attribution = data.attribution.map((raw) => {
    const credit = record(raw)
    const url = new URL(label(credit.url))
    if (url.protocol !== 'https:') throw new Error('Invalid attribution URL')
    return { label: label(credit.label), url: url.href }
  })
  const coverage = record(data.coverage)
  const bbox = coverage.bbox
  if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every((value) => typeof value === 'number' && Number.isFinite(value))) {
    throw new Error('Invalid coverage bounds')
  }
  const [west, south, east, north] = bbox as number[]
  if (west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north) throw new Error('Invalid coverage bounds')
  if (!['webp', 'png', 'jpg'].includes(String(data.format))) throw new Error('Unsupported tile format')
  if (typeof data.ai_styling !== 'boolean') throw new Error('Missing style provenance')
  if (data.comparison_mode !== undefined && !['shared-framing-unverified', 'reviewed-approximate'].includes(String(data.comparison_mode))) throw new Error('Invalid comparison mode')
  const tileSize = integer(data.tile_size, 16, 2048)
  const width = integer(data.width, 1, 100_000_000)
  const height = integer(data.height, 1, 100_000_000)
  const bounds = data.bounds_px === undefined ? undefined : pixelBounds(data.bounds_px)
  if (bounds && (bounds[2] - bounds[0] !== width || bounds[3] - bounds[1] !== height)) throw new Error('Atlas bounds disagree with image size')
  let rectangles: Bounds[] | undefined
  if (coverage.rectangles_px !== undefined) {
    if (!bounds || !Array.isArray(coverage.rectangles_px) || !coverage.rectangles_px.length || coverage.rectangles_px.length > 100_000) throw new Error('Invalid rendered coverage')
    rectangles = coverage.rectangles_px.map(pixelBounds)
    if (rectangles.some((rectangle) => rectangle[0] < bounds[0] || rectangle[1] < bounds[1] || rectangle[2] > bounds[2] || rectangle[3] > bounds[3]) || new Set(rectangles.map(String)).size !== rectangles.length) throw new Error('Rendered coverage is outside atlas bounds or repeated')
  }
  const place = data.place === undefined ? undefined : record(data.place)
  for (const field of ['artwork_approved', 'source_coverage_verified']) {
    if (data[field] !== undefined && typeof data[field] !== 'boolean') throw new Error('Invalid approval metadata')
  }
  return {
    schema_version: 1,
    id: identifier(data.id), title: label(data.title),
    width, height,
    tile_size: tileSize, overlap: integer(data.overlap, 0, tileSize / 2),
    format: data.format as AtlasManifest['format'], layers, attribution,
    coverage: { label: label(coverage.label), bbox: [west, south, east, north], ...(rectangles ? { rectangles_px: rectangles } : {}) },
    ...(bounds ? { bounds_px: bounds } : {}),
    ...(place ? { place: { name: label(place.name), ...(place.name_ja === undefined ? {} : { name_ja: label(place.name_ja) }) } } : {}),
    ...(data.artwork_approved === undefined ? {} : { artwork_approved: data.artwork_approved as boolean }),
    ...(data.source_coverage_verified === undefined ? {} : { source_coverage_verified: data.source_coverage_verified as boolean }),
    source_edition: label(data.source_edition), ai_styling: data.ai_styling,
    ...(data.comparison_mode === undefined ? {} : { comparison_mode: data.comparison_mode as AtlasManifest['comparison_mode'] }),
    ...(data.modifications === undefined ? {} : { modifications: label(data.modifications) }),
    ...(data.limitations === undefined ? {} : { limitations: label(data.limitations) }),
    ...(data.notice === undefined ? {} : { notice: assetPath(data.notice) }),
  }
}

function pixelBounds(value: unknown): Bounds {
  if (!Array.isArray(value) || value.length !== 4) throw new Error('Invalid pixel bounds')
  const bounds = value.map((coordinate) => integer(coordinate, -(2 ** 31), 2 ** 31 - 1)) as Bounds
  if (bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) throw new Error('Empty pixel bounds')
  return bounds
}

/** DZI coordinates are local to the exported rectangle, not the global atlas. */
export function imageToAtlas(atlas: AtlasManifest, x: number, y: number): [number, number] {
  const origin = atlas.bounds_px ?? [0, 0, atlas.width, atlas.height]
  return [origin[0] + x * atlas.width, origin[1] + y * atlas.height]
}

export function atlasToImage(atlas: AtlasManifest, x: number, y: number): [number, number] {
  const origin = atlas.bounds_px ?? [0, 0, atlas.width, atlas.height]
  return [(x - origin[0]) / atlas.width, (y - origin[1]) / atlas.height]
}

/** OpenSeadragon viewport x/y/width/height all use image-width units. */
export function viewportHasCoverage(atlas: AtlasManifest, viewport: { x: number; y: number; width: number; height: number }): boolean {
  const bounds = atlas.bounds_px ?? [0, 0, atlas.width, atlas.height]
  const view: Bounds = [bounds[0] + viewport.x * atlas.width, bounds[1] + viewport.y * atlas.width, bounds[0] + (viewport.x + viewport.width) * atlas.width, bounds[1] + (viewport.y + viewport.height) * atlas.width]
  return (atlas.coverage.rectangles_px ?? [bounds]).some((rectangle) => rectangle[0] < view[2] && rectangle[2] > view[0] && rectangle[1] < view[3] && rectangle[3] > view[1])
}

export interface DziDescriptor {
  width: number
  height: number
  tile_size: number
  tile_height: number
  overlap: number
  format: string
  max_level: number
  rectangles: { x: number; y: number; width: number; height: number; minLevel: number; maxLevel: number }[]
}

/** Reject mixed releases and shifted or incomplete comparison layers. */
export function validateDescriptor(atlas: AtlasManifest, source: DziDescriptor): void {
  const maximum = Math.ceil(Math.log2(Math.max(atlas.width, atlas.height)))
  if (source.width !== atlas.width || source.height !== atlas.height || source.tile_size !== atlas.tile_size || source.tile_height !== atlas.tile_size || source.overlap !== atlas.overlap || source.format !== atlas.format || source.max_level !== maximum) throw new Error('DZI settings disagree with the atlas')
  const origin = atlas.bounds_px ?? [0, 0, atlas.width, atlas.height]
  const expected = (atlas.coverage.rectangles_px ?? []).map((box) => [box[0] - origin[0], box[1] - origin[1], box[2] - box[0], box[3] - box[1]].join(',')).sort()
  const actual = source.rectangles.map((box) => {
    if (box.minLevel !== 0 || box.maxLevel !== maximum) throw new Error('DZI coverage levels disagree')
    return [box.x, box.y, box.width, box.height].join(',')
  }).sort()
  if (expected.length !== actual.length || expected.some((value, index) => value !== actual[index])) throw new Error('DZI rendered coverage disagrees with the atlas')
}

export interface ViewState { x: number; y: number; z: number; layer: string }

/** x/y are normalized image coordinates; z is viewport width / image-view width. */
export function parseView(hash: string, layers: readonly string[]): ViewState | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const raw = ['x', 'y', 'z'].map((key) => params.get(key))
  if (raw.some((value) => value === null || value.trim() === '')) return null
  const [x, y, z] = raw.map(Number)
  const layer = params.get('layer')
  if (![x, y, z].every(Number.isFinite) || x < 0 || x > 1 || y < 0 || y > 1 || z < 0.1 || z > 16 || !layer || !layers.includes(layer)) return null
  return { x, y, z, layer }
}

export function formatView(view: ViewState): string {
  const params = new URLSearchParams({
    x: Math.max(0, Math.min(1, view.x)).toFixed(5),
    y: Math.max(0, Math.min(1, view.y)).toFixed(5),
    z: Math.max(0.1, Math.min(16, view.z)).toFixed(5),
    layer: view.layer,
  })
  return `#${params.toString()}`
}
