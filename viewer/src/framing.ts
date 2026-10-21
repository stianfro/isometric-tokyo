import { atlasToImage, formatView, imageToAtlas, parseView, type AtlasManifest, type ViewState } from './atlas'

/** URL v2 stores global native pixels, not coordinates relative to a release crop.
 * span is the viewport width in global pixels, independent of image extent.
 */
export function parseAtlasView(atlas: AtlasManifest, hash: string): ViewState | null {
  const layers = atlas.layers.map((layer) => layer.id)
  const frame = atlas.coordinate_frame
  if (!frame) return parseView(hash, layers)
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  if (params.has('v')) {
    const keys = ['v', 'frame', 'px', 'py', 'span', 'layer']
    if (keys.some((key) => params.getAll(key).length !== 1) || params.get('v') !== '2' || params.get('frame') !== frame.id) return null
    const raw = ['px', 'py', 'span'].map((key) => params.get(key)!)
    if (raw.some((value) => !value.trim())) return null
    const [px, py, span] = raw.map(Number)
    const layer = params.get('layer')!
    const bounds = atlas.bounds_px!
    const referenceWidth = atlas.legacy_view_bounds![2] - atlas.legacy_view_bounds![0]
    if (![px, py, span].every(Number.isFinite) || px < bounds[0] || px > bounds[2] || py < bounds[1] || py > bounds[3] || span < referenceWidth / 16 || span > referenceWidth / 0.1 || !layers.includes(layer)) return null
    const [x, y] = atlasToImage(atlas, px, py)
    return { x, y, z: atlas.width / span, layer }
  }
  // The older illustration collection is not registered to the native source.
  // Do not reinterpret an old independently redrawn image as this frame.
  if (params.get('view') === 'pixel-city' && params.get('layer') === 'illustration') return null
  const legacy = parseView(hash, layers)
  if (!legacy) return null
  const bounds = atlas.legacy_view_bounds!
  const width = bounds[2] - bounds[0]
  const height = bounds[3] - bounds[1]
  const [x, y] = atlasToImage(atlas, bounds[0] + legacy.x * width, bounds[1] + legacy.y * height)
  return { x, y, z: legacy.z * atlas.width / width, layer: legacy.layer }
}

export function formatAtlasView(atlas: AtlasManifest, view: ViewState): string {
  if (!atlas.coordinate_frame) return formatView(view)
  const [px, py] = imageToAtlas(atlas, view.x, view.y)
  return `#${new URLSearchParams({
    v: '2', frame: atlas.coordinate_frame.id,
    px: px.toFixed(3), py: py.toFixed(3),
    span: (atlas.width / view.z).toFixed(5), layer: view.layer,
  })}`
}

/** Keep the original opening frame, not a progressively zoomed-out whole atlas. */
export function framedHome(atlas: AtlasManifest, viewportWidth: number, viewportHeight: number, layer: string): ViewState | null {
  const box = atlas.home_bounds
  if (!atlas.coordinate_frame || !box) return null
  const [x, y] = atlasToImage(atlas, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
  const span = Math.min(box[2] - box[0], (box[3] - box[1]) * viewportWidth / viewportHeight)
  return { x, y, z: atlas.width / span, layer }
}

export function framedZoomLimits(atlas: AtlasManifest): { minZoomLevel: number; maxZoomLevel: number } {
  const legacy = atlas.legacy_view_bounds
  const ratio = atlas.coordinate_frame && legacy ? atlas.width / (legacy[2] - legacy[0]) : 1
  return { minZoomLevel: 0.1 * ratio, maxZoomLevel: 16 * ratio }
}
