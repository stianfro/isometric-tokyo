export interface AtlasLayer {
  id: string
  label: string
  dzi: string
  original: string
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
  coverage: { label: string; bbox: [number, number, number, number] }
  source_edition: string
  ai_styling: boolean
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
  if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(result)) throw new Error('Invalid identifier')
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

export function parseManifest(input: unknown): AtlasManifest {
  const data = record(input)
  if (data.schema_version !== 1) throw new Error('Unsupported atlas version')
  if (!Array.isArray(data.layers) || data.layers.length < 1 || data.layers.length > 8) throw new Error('Invalid layers')
  const layers = data.layers.map((raw) => {
    const layer = record(raw)
    return { id: identifier(layer.id), label: label(layer.label), dzi: assetPath(layer.dzi), original: assetPath(layer.original) }
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
  const tileSize = integer(data.tile_size, 64, 2048)
  return {
    schema_version: 1,
    id: identifier(data.id), title: label(data.title),
    width: integer(data.width, 1, 100_000_000), height: integer(data.height, 1, 100_000_000),
    tile_size: tileSize, overlap: integer(data.overlap, 0, tileSize / 2),
    format: data.format as AtlasManifest['format'], layers, attribution,
    coverage: { label: label(coverage.label), bbox: [west, south, east, north] },
    source_edition: label(data.source_edition), ai_styling: data.ai_styling,
    ...(data.modifications === undefined ? {} : { modifications: label(data.modifications) }),
    ...(data.limitations === undefined ? {} : { limitations: label(data.limitations) }),
    ...(data.notice === undefined ? {} : { notice: assetPath(data.notice) }),
  }
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
