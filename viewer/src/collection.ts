import { formatView, resolveManifestUrl, type AtlasManifest, type ViewState } from './atlas'

export interface AtlasCollectionView {
  id: string
  label: string
  manifest: string
  kind: 'illustration-preview' | 'source'
}

export interface AtlasCollection {
  schema_version: 1
  kind: 'atlas_collection'
  id: string
  title: string
  default_view: string
  views: AtlasCollectionView[]
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid collection object')
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 1000) throw new Error('Invalid collection text')
  return value
}

function id(value: unknown): string {
  const result = text(value)
  if (!/^[a-z0-9][a-z0-9._-]{0,95}$/.test(result)) throw new Error('Invalid collection identifier')
  return result
}

export function isCollection(value: unknown): boolean {
  return Boolean(value && typeof value === 'object' && 'kind' in value && value.kind === 'atlas_collection')
}

/** Each view has its own image coordinate system. A collection never implies alignment. */
export function parseCollection(value: unknown): AtlasCollection {
  const data = object(value)
  if (data.schema_version !== 1 || data.kind !== 'atlas_collection') throw new Error('Unsupported collection version')
  if (!Array.isArray(data.views) || data.views.length < 2 || data.views.length > 8) throw new Error('Invalid collection views')
  const views = data.views.map((value): AtlasCollectionView => {
    const view = object(value)
    if (view.kind !== 'illustration-preview' && view.kind !== 'source') throw new Error('Invalid collection view kind')
    const manifest = text(view.manifest)
    resolveManifestUrl(manifest, 'https://example.invalid/')
    return { id: id(view.id), label: text(view.label), manifest, kind: view.kind }
  })
  if (new Set(views.map((view) => view.id)).size !== views.length || new Set(views.map((view) => view.manifest)).size !== views.length) throw new Error('Duplicate collection views')
  const defaultView = id(data.default_view)
  if (!views.some((view) => view.id === defaultView)) throw new Error('Unknown default view')
  if (!views.some((view) => view.kind === 'source')) throw new Error('Collection must retain a source view')
  return { schema_version: 1, kind: 'atlas_collection', id: id(data.id), title: text(data.title), default_view: defaultView, views }
}

export function selectedCollectionView(collection: AtlasCollection, hash: string): AtlasCollectionView {
  const selected = new URLSearchParams(hash.replace(/^#/, '')).get('view')
  return collection.views.find((view) => view.id === selected) ?? collection.views.find((view) => view.id === collection.default_view)!
}

export function validateCollectionAtlas(view: AtlasCollectionView, atlas: AtlasManifest): void {
  if (view.kind === 'source' && atlas.ai_styling) throw new Error('Source view cannot contain AI styling')
  if (view.kind === 'illustration-preview' && (!atlas.ai_styling || atlas.artwork_approved !== false || atlas.source_coverage_verified !== false)) throw new Error('Illustration previews require explicit unverified geographic and artwork status')
}

export function formatCollectionView(view: ViewState, collectionView?: AtlasCollectionView): string {
  const hash = formatView(view)
  return collectionView ? `${hash}&view=${encodeURIComponent(collectionView.id)}` : hash
}
