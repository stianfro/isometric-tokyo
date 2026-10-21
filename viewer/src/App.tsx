import { useCallback, useEffect, useRef, useState } from 'react'
import OpenSeadragon from 'openseadragon'
import { parseManifest, parseView, resolveManifestUrl, validateDescriptor, viewportHasCoverage, type AtlasManifest, type ViewState } from './atlas'
import { formatAtlasView, framedHome, framedZoomLimits, parseAtlasView } from './framing'
import { formatCollectionView, isCollection, parseCollection, selectedCollectionView, validateCollectionAtlas, type AtlasCollection, type AtlasCollectionView } from './collection'

const manifestConfiguration = (() => {
  try {
    return { url: resolveManifestUrl(import.meta.env.VITE_ATLAS_MANIFEST, document.baseURI), error: null }
  } catch {
    return { url: null, error: 'VITE_ATLAS_MANIFEST must be a relative file path without URL syntax, a leading slash, empty segments or dot segments. Correct the build or development configuration and restart it.' }
  }
})()

function Icon({ name }: { name: 'plus' | 'minus' | 'home' | 'overview' | 'share' | 'info' | 'close' }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    minus: <path d="M5 12h14" />,
    home: <><path d="m4 11 8-7 8 7M6 10v10h12V10" /><path d="M10 20v-7h4v7" /></>,
    overview: <><rect x="3" y="4" width="18" height="16" rx="1" /><rect x="11" y="11" width="7" height="6" rx=".5" /><path d="M6 8h4M6 11h2" /></>,
    share: <><path d="M12 15V3m-4 4 4-4 4 4M6 10H4v11h16V10h-2" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7v.2" /></>,
    close: <path d="m6 6 12 12M18 6 6 18" />,
  }
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

interface CollectionContext {
  collection: AtlasCollection
  view: AtlasCollectionView
  select: (view: AtlasCollectionView) => void
}

function ViewToggle({ context }: { context: CollectionContext }) {
  const index = context.collection.views.findIndex((view) => view.id === context.view.id)
  const next = context.collection.views[(index + 1) % context.collection.views.length]
  return <button className="view-toggle" aria-label={`Show ${next.label} view`} onClick={() => context.select(next)}>{next.label}</button>
}

function Startup({ error, retry, context }: { error?: boolean; retry: () => void; context?: CollectionContext }) {
  return <main className="startup">
    <div className="wordmark">isometric.tokyo</div>
    {context && <ViewToggle context={context} />}
    {manifestConfiguration.error ? <div role="alert"><h1>The map configuration is invalid.</h1><p>{manifestConfiguration.error}</p></div> : error ? <div role="alert"><h1>The map could not load.</h1><p>The atlas is unavailable or its manifest is invalid.</p><button className="text-button" onClick={retry}>Try again</button></div> : <p role="status">Opening {context?.view.label ?? 'Tokyo'}…</p>}
  </main>
}

export function App() {
  const [document, setDocument] = useState<AtlasManifest | AtlasCollection | null>(null)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!manifestConfiguration.url) return
    const abort = new AbortController()
    fetch(manifestConfiguration.url, { signal: abort.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Atlas manifest unavailable')
        return response.json() as Promise<unknown>
      })
      .then((data) => { setDocument(isCollection(data) ? parseCollection(data) : parseManifest(data)); setError(false) })
      .catch(() => { if (!abort.signal.aborted) setError(true) })
    return () => abort.abort()
  }, [attempt])

  const retry = () => { setError(false); setAttempt((value) => value + 1) }
  if (!document || !manifestConfiguration.url) return <Startup error={error} retry={retry} />
  return 'kind' in document
    ? <CollectionMap collection={document} manifestUrl={manifestConfiguration.url} />
    : <AtlasMap atlas={document} manifestUrl={manifestConfiguration.url} />
}

function CollectionMap({ collection, manifestUrl }: { collection: AtlasCollection; manifestUrl: string }) {
  const [view, setView] = useState(() => selectedCollectionView(collection, location.hash))
  useEffect(() => {
    const sync = () => setView(selectedCollectionView(collection, location.hash))
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [collection])
  const select = (next: AtlasCollectionView) => {
    if (next.id === view.id) return
    // Art is redrawn, not registered to the source. Do not transfer coordinates.
    history.pushState(null, '', `${location.pathname}${location.search}#view=${encodeURIComponent(next.id)}`)
    setView(next)
  }
  const context = { collection, view, select }
  return <CollectionAtlas key={view.id} context={context} manifestUrl={new URL(view.manifest, manifestUrl).href} />
}

function CollectionAtlas({ context, manifestUrl }: { context: CollectionContext; manifestUrl: string }) {
  const [atlas, setAtlas] = useState<AtlasManifest | null>(null)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const view = context.view
  useEffect(() => {
    const abort = new AbortController()
    fetch(manifestUrl, { signal: abort.signal })
      .then((response) => {
        if (!response.ok) throw new Error('View manifest unavailable')
        return response.json() as Promise<unknown>
      })
      .then((data) => {
        const manifest = parseManifest(data)
        validateCollectionAtlas(view, manifest)
        if (!abort.signal.aborted) { setAtlas(manifest); setError(false) }
      })
      .catch(() => { if (!abort.signal.aborted) setError(true) })
    return () => abort.abort()
  }, [manifestUrl, view, attempt])
  if (!atlas) return <Startup context={context} error={error} retry={() => { setError(false); setAttempt((value) => value + 1) }} />
  return <AtlasMap atlas={atlas} manifestUrl={manifestUrl} context={context} />
}

function AtlasMap({ atlas, manifestUrl, context }: { atlas: AtlasManifest; manifestUrl: string; context?: CollectionContext }) {
  const mapElement = useRef<HTMLDivElement>(null)
  const overviewElement = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<OpenSeadragon.Viewer | null>(null)
  const [layer, setLayer] = useState(() => (context ? parseView(location.hash, atlas.layers.map((item) => item.id)) : parseAtlasView(atlas, location.hash))?.layer ?? atlas.layers[0].id)
  const layerRef = useRef(layer)
  const [loadedLayer, setLoadedLayer] = useState<string | null>(null)
  const ready = loadedLayer === layer
  const [failure, setFailure] = useState<'source' | 'tile' | null>(null)
  const [retry, setRetry] = useState(0)
  const [overview, setOverview] = useState(false)
  const [info, setInfo] = useState(false)
  const [message, setMessage] = useState('')
  const [emptyView, setEmptyView] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const selected = atlas.layers.find((item) => item.id === layer)!
  const imageAspect = atlas.height / atlas.width
  const placeName = atlas.place?.name ?? atlas.title
  const collection = context?.collection
  const collectionView = context?.view
  const sharedPilot = !context && Boolean(atlas.comparison_mode) && atlas.layers.some((item) => item.id === 'styled') && atlas.layers.some((item) => item.id === 'textured')
  const artPreview = collectionView?.kind === 'illustration-preview' || (!context && atlas.ai_styling && (sharedPilot ? layer === 'styled' : true))
  const coverHome = Boolean(collection) || atlas.ai_styling
  const isCollectionView = Boolean(context)

  const currentView = useCallback((): ViewState | null => {
    const viewer = viewerRef.current
    if (!viewer || !viewer.isOpen()) return null
    const center = viewer.viewport.getCenter(true)
    return { x: center.x, y: center.y / imageAspect, z: viewer.viewport.getZoom(true), layer: layerRef.current }
  }, [imageAspect])

  const saveView = useCallback(() => {
    const view = currentView()
    if (!view) return
    history.replaceState(null, '', `${location.pathname}${location.search}${collectionView ? formatCollectionView(view, collectionView) : formatAtlasView(atlas, view)}`)
  }, [currentView, collectionView, atlas])

  useEffect(() => {
    if (!mapElement.current || !overviewElement.current) return
    // OpenSeadragon owns and removes its navigator node. Keep React's host separate.
    const navigatorElement = document.createElement('div')
    navigatorElement.style.width = '100%'
    navigatorElement.style.height = '100%'
    overviewElement.current.replaceChildren(navigatorElement)
    let active = true
    let lastReady: string | null | undefined
    let lastEmpty: boolean | undefined
    let sourceInvalid = false
    const desired = isCollectionView ? parseView(location.hash, atlas.layers.map((item) => item.id)) : parseAtlasView(atlas, location.hash)
    const viewer = OpenSeadragon({
      element: mapElement.current,
      showNavigationControl: false,
      showNavigator: overview,
      homeFillsViewer: coverHome,
      // High-density pilot displays load native detail on zoom, not at home.
      minPixelRatio: sharedPilot && window.devicePixelRatio >= 2 ? 1 : 0.5,
      navigatorElement,
      navigatorAutoFade: false,
      navigatorDisplayRegionColor: '#215bd6',
      navigatorBorderColor: 'transparent',
      navigatorBackground: '#d5dade',
      visibilityRatio: 1,
      constrainDuringPan: true,
      ...(isCollectionView ? { minZoomLevel: 0.1, maxZoomLevel: 16 } : framedZoomLimits(atlas)),
      animationTime: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 0.25,
      blendTime: 0,
      immediateRender: true,
      imageLoaderLimit: 6,
      timeout: 15000,
      maxImageCacheCount: 120,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true, scrollToZoom: true },
      gestureSettingsTouch: { pinchToZoom: true, flickEnabled: false },
      showRotationControl: false,
    })
    viewerRef.current = viewer
    const apply = (view: ViewState) => {
      viewer.viewport.zoomTo(view.z, undefined, true)
      viewer.viewport.panTo(new OpenSeadragon.Point(view.x, view.y * imageAspect), true)
      viewer.viewport.applyConstraints(true)
    }
    const syncHash = () => {
      const view = isCollectionView ? parseView(location.hash, atlas.layers.map((item) => item.id)) : parseAtlasView(atlas, location.hash)
      if (collection && selectedCollectionView(collection, location.hash).id !== collectionView?.id) return
      if (view && viewer.isOpen()) { setLayer(view.layer); layerRef.current = view.layer; apply(view); saveView() }
    }
    const initialize = () => {
      if (!active) return
      if (sourceInvalid) return
      try {
        atlas.layers.forEach((_, index) => {
          const source = viewer.world.getItemAt(index)?.source
          if (!(source instanceof OpenSeadragon.DziTileSource)) throw new Error('Expected a DZI source')
          validateDescriptor(atlas, { width: source.dimensions.x, height: source.dimensions.y, tile_size: source.getTileWidth(source.maxLevel), tile_height: source.getTileHeight(source.maxLevel), overlap: source.tileOverlap, format: source.fileFormat, max_level: source.maxLevel, rectangles: source.displayRects ?? [] })
        })
      } catch {
        sourceInvalid = true
        viewer.world.removeAll()
        setFailure('source')
        return
      }
      atlas.layers.forEach((item, index) => viewer.world.getItemAt(index).setOpacity(item.id === layerRef.current ? 1 : 0))
      if (desired) apply(desired)
      else if (!isCollectionView && atlas.coordinate_frame) {
        const size = viewer.viewport.getContainerSize()
        apply(framedHome(atlas, size.x, size.y, layerRef.current)!)
      } else {
        viewer.viewport.goHome(true)
        if (!coverHome) viewer.viewport.zoomBy(1.12, undefined, true)
      }
      saveView()
    }
    viewer.addHandler('update-viewport', () => {
      if (!active) return
      const index = atlas.layers.findIndex((item) => item.id === layerRef.current)
      const empty = !viewportHasCoverage(atlas, viewer.viewport.getBounds(true))
      if (empty !== lastEmpty) { lastEmpty = empty; setEmptyView(empty) }
      const loaded = !sourceInvalid && sourcesLoaded === atlas.layers.length && (empty || Boolean(viewer.world.getItemAt(index)?.getFullyLoaded()))
      const readiness = loaded ? layerRef.current : null
      if (readiness !== lastReady) { lastReady = readiness; setLoadedLayer(readiness) }
    })
    viewer.addHandler('open-failed', () => { if (active) setFailure('source') })
    viewer.addHandler('tile-load-failed', () => { if (active && !sourceInvalid) setFailure('tile') })
    viewer.addHandler('animation-finish', saveView)
    viewer.addHandler('resize', saveView)
    window.addEventListener('hashchange', syncHash)
    let sourcesLoaded = 0
    atlas.layers.forEach((item, index) => viewer.addTiledImage({
      tileSource: new URL(item.dzi, manifestUrl).href,
      opacity: (desired?.layer ?? atlas.layers[0].id) === item.id ? 1 : 0,
      x: 0, y: 0, width: 1, index,
      success: () => { sourcesLoaded += 1; if (sourcesLoaded === atlas.layers.length) initialize() },
      error: () => { if (active) setFailure('source') },
    }))
    return () => {
      active = false
      window.removeEventListener('hashchange', syncHash)
      viewer.destroy()
      viewerRef.current = null
    }
  }, [atlas, imageAspect, manifestUrl, retry, saveView, collection, collectionView, overview, coverHome, sharedPilot, isCollectionView])

  useEffect(() => {
    layerRef.current = layer
    const viewer = viewerRef.current
    if (!viewer) return
    atlas.layers.forEach((item, index) => viewer.world.getItemAt(index)?.setOpacity(item.id === layer ? 1 : 0))
    saveView()
  }, [atlas.layers, layer, saveView])

  useEffect(() => {
    if (info) dialog.current?.showModal()
    else dialog.current?.close()
  }, [info])

  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => setMessage(''), 4500)
    return () => clearTimeout(timer)
  }, [message])

  function zoomBy(factor: number) {
    viewerRef.current?.viewport.zoomBy(factor)
    viewerRef.current?.viewport.applyConstraints()
  }

  function selectLayer(next: string) {
    if (next === layer) return
    const view = currentView()
    if (!context && atlas.coordinate_frame && view) {
      history.pushState(null, '', `${location.pathname}${location.search}${formatAtlasView(atlas, { ...view, layer: next })}`)
    }
    layerRef.current = next
    setLayer(next)
  }

  function reset() {
    const viewport = viewerRef.current?.viewport
    if (!viewport) return
    const size = viewport.getContainerSize()
    const home = !context && framedHome(atlas, size.x, size.y, layerRef.current)
    if (home) {
      viewport.zoomTo(home.z)
      viewport.panTo(new OpenSeadragon.Point(home.x, home.y * imageAspect))
      viewport.applyConstraints()
    } else viewport.goHome()
  }

  function keyboard(event: React.KeyboardEvent<HTMLDivElement>) {
    const viewport = viewerRef.current?.viewport
    if (!viewport || event.altKey || event.ctrlKey || event.metaKey) return
    const step = 0.12 / viewport.getZoom()
    const offsets: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    if (offsets[event.key]) viewport.panBy(new OpenSeadragon.Point(...offsets[event.key]))
    else if (event.key === '+' || event.key === '=') zoomBy(1.4)
    else if (event.key === '-') zoomBy(1 / 1.4)
    else if (event.key === 'Home' || event.key === '0') reset()
    else return
    event.preventDefault()
    event.stopPropagation()
    viewport.applyConstraints()
  }

  async function share() {
    saveView()
    try {
      await navigator.clipboard.writeText(location.href)
      setMessage('Link copied. It opens this view and layer.')
    } catch {
      setMessage('This view is saved in the address bar. Copy its URL to share.')
    }
  }

  return <main className="atlas-shell">
    <div ref={mapElement} className="map-canvas" aria-label={`${placeName} interactive map`} role="region" tabIndex={0} onKeyDownCapture={keyboard} aria-describedby="map-help" data-ready={ready} data-layer={layer} data-view={collectionView?.id} data-coverage={emptyView ? 'empty' : 'available'} />
    <nav className="map-toolbar" aria-label="Map navigation">
      <a className="wordmark" href={location.pathname} aria-label="isometric.tokyo, reset map">isometric.tokyo</a>
      {context && <ViewToggle context={context} />}
      {sharedPilot && <button className="view-toggle" aria-label={`Show ${layer === 'styled' ? 'Source' : 'Pixel city'} view`} onClick={() => selectLayer(layer === 'styled' ? 'textured' : 'styled')}>{layer === 'styled' ? 'Source' : 'Pixel city'}</button>}
      <button className="icon-button" aria-label="Zoom in" onClick={() => zoomBy(1.5)}><Icon name="plus" /></button>
      <button className="icon-button" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.5)}><Icon name="minus" /></button>
      <button className="icon-button" aria-label="About this map and credits" onClick={() => setInfo(true)}><Icon name="info" /></button>
    </nav>

    <aside className="overview" aria-label="Overview map" hidden={!overview}>
      <div id="atlas-overview" ref={overviewElement} />
    </aside>

    {emptyView && ready && !failure && <div className="map-notice coverage-empty" role="status">No rendered chunks in this view. Pan back to available imagery.</div>}
    {!ready && !failure && <div className="map-notice loading" role="status">Loading map tiles…</div>}
    {failure && <div className="map-notice failure" role="alert"><strong>{failure === 'source' ? 'This map layer could not open.' : 'Some map tiles could not load.'}</strong><span>{failure === 'source' ? 'The image source is missing or does not match the atlas.' : 'The missing imagery is a loading failure, not empty city.'}</span><button onClick={() => { setLoadedLayer(null); setFailure(null); setRetry((value) => value + 1) }}>Retry map</button></div>}
    {message && <div className="map-notice toast" role="status">{message}</div>}

    <button className="credit-button" onClick={() => setInfo(true)}>{artPreview ? 'AI preview' : 'Source'} · Credits</button>
    <p id="map-help" className="sr-only">Drag to explore. Scroll or pinch to zoom. Focus the map for arrow keys. Use the information button for controls and credits.</p>

    <dialog ref={dialog} className="info-dialog" aria-labelledby="about-title" onCancel={() => setInfo(false)} onClick={(event) => { if (event.target === dialog.current) setInfo(false) }}>
      <div className="dialog-header"><span className="eyebrow">{artPreview ? 'THE PIXEL CITY PREVIEW' : 'THE SOURCE ATLAS'}</span><button className="icon-button" aria-label="Close map information" onClick={() => setInfo(false)}><Icon name="close" /></button></div>
      <h2 id="about-title">{placeName}</h2>
      <div className="secondary-controls" role="group" aria-label="More map controls">
        <button onClick={() => { reset(); setInfo(false) }}><Icon name="home" />Reset view</button>
        <button aria-label="Toggle overview map" aria-pressed={overview} onClick={() => { setLoadedLayer(null); setOverview(!overview); setInfo(false) }}><Icon name="overview" />{overview ? 'Hide overview' : 'Show overview'}</button>
        <button aria-label="Copy link to this view" onClick={() => { void share(); setInfo(false) }}><Icon name="share" />Copy link</button>
      </div>
      {atlas.layers.length > 1 && <div className="layer-control" role="group" aria-label="Map layers">
        {atlas.layers.length > 3 ? <label>Map layer<select value={layer} onChange={(event) => selectLayer(event.target.value)}>{atlas.layers.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label> : atlas.layers.map((item) => <button key={item.id} aria-pressed={layer === item.id} onClick={() => selectLayer(item.id)}>{item.label}</button>)}
      </div>}
      {context && context.collection.views.length > 2 && <div className="layer-control" role="group" aria-label="All map views">{context.collection.views.map((view) => <button key={view.id} aria-pressed={view.id === collectionView?.id} onClick={() => context.select(view)}>{view.label}</button>)}</div>}
      {artPreview && <p className="view-warning">AI illustration. Geography not verified. Not a navigation map.</p>}
      <p>{sharedPilot ? (artPreview ? `Explore a bounded, ${atlas.coordinate_frame ? 'expanding' : 'nine-patch'} Pixel city pilot of Shinjuku. The illustration uses a native-resolution source render, but AI may change buildings, streets and vegetation. This is not a complete Tokyo map.` : `This is the unchanged native-resolution 3D source render beneath the ${atlas.coordinate_frame ? 'expanding' : 'nine-patch'} Shinjuku art pilot. It is not a complete Tokyo map.`) : artPreview ? 'Explore an AI-styled Shinjuku image in the Pixel city direction. This is a bounded art preview, not a completed or geographically verified city map.' : 'This is a small, explorable source study for an illustrated map of Tokyo. It is not the finished city map.'}</p>
      {sharedPilot && <p className="view-warning">Source and Pixel city share framing, so switching keeps your position. This does not mean buildings or streets are pixel-aligned. Geographic fidelity is not verified. Not a navigation map.</p>}
      {context && <p className="view-warning">{artPreview ? 'The illustration redraws buildings, trees and streets. Open the Source view to inspect the underlying 3D render. The two views are not pixel-aligned, so switching resets the framing.' : 'This is the unstyled 3D source render used for the art study. It has its own image coordinates. Switching to Pixel city resets the framing rather than implying an aligned comparison.'}</p>}
      <dl><div><dt>Coverage</dt><dd>{atlas.coverage.label}</dd></div><div><dt>Source edition</dt><dd>{atlas.source_edition}</dd></div><div><dt>Atlas</dt><dd>{atlas.id}</dd></div><div><dt>AI styling</dt><dd>{artPreview ? 'Applied. Artistic details may be invented or changed.' : sharedPilot ? 'None in this view. These are direct 3D source renders.' : 'None. These are direct 3D source renders.'}</dd></div>{atlas.bounds_px && <div><dt>Atlas pixel bounds</dt><dd>{atlas.bounds_px.join(', ')}</dd></div>}{atlas.source_coverage_verified !== undefined && <div><dt>Source completeness</dt><dd>{atlas.source_coverage_verified ? 'Verified for this release.' : 'Not verified. Rendered bounds do not prove complete geographic data.'}</dd></div>}{atlas.artwork_approved !== undefined && <div><dt>Artwork approval</dt><dd>{atlas.artwork_approved ? 'Approved for this release.' : artPreview ? 'Art direction selected. Geographic fidelity and production artwork are not approved.' : 'Not approved. This is a source or working release.'}</dd></div>}</dl>
      <h3>Read the gaps, too.</h3>
      <p>{artPreview ? 'Buildings, paths, vegetation and road widths can differ from the source. A preferred visual style is not approval of geographic accuracy. Coverage ends at the image edge.' : 'White buildings can indicate missing textures or lower-detail geometry. Vegetation may include mapped trees and artistic woodland fill. Source datasets can have mixed building detail, acquisition dates and alignment limitations. It is not a live map or a navigation aid.'}</p>
      <p>{atlas.limitations}</p>
      <p>{atlas.modifications ?? "Independent project. No source-provider endorsement."}</p>
      <h3>Source credits</h3>
      <ul>{atlas.attribution.map((credit) => <li key={credit.url}><a href={credit.url} target="_blank" rel="noreferrer">{credit.label} ↗</a></li>)}</ul>
      {atlas.notice && <p><a href={new URL(atlas.notice, manifestUrl).href} target="_blank" rel="noreferrer">Full source terms and modification notice ↗</a></p>}
      {selected.original ? <a className="download-link" href={new URL(selected.original, manifestUrl).href} download>Download {selected.label.toLowerCase()} PNG ↓</a> : <p>Original images are retained per render chunk. This layer has no single-image download.</p>}
      <p className="dialog-help">Keyboard: arrow keys pan; + and − zoom; Home or 0 resets. Shared links preserve the image position, zoom and selected {context ? 'view and layer' : 'layer'}.</p>
    </dialog>
  </main>
}
