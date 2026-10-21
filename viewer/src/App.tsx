import { useCallback, useEffect, useRef, useState } from 'react'
import OpenSeadragon from 'openseadragon'
import { formatView, parseManifest, parseView, resolveManifestUrl, validateDescriptor, viewportHasCoverage, type AtlasManifest, type ViewState } from './atlas'

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

export function App() {
  const [atlas, setAtlas] = useState<AtlasManifest | null>(null)
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
      .then((data) => { setAtlas(parseManifest(data)); setError(false) })
      .catch(() => { if (!abort.signal.aborted) setError(true) })
    return () => abort.abort()
  }, [attempt])

  if (!atlas || !manifestConfiguration.url) return <main className="startup">
    <div className="wordmark">isometric<span>.tokyo</span></div>
    {manifestConfiguration.error ? <div role="alert"><h1>The map configuration is invalid.</h1><p>{manifestConfiguration.error}</p></div> : error ? <div role="alert"><h1>The map could not load.</h1><p>The atlas is unavailable or its manifest is invalid.</p><button className="text-button" onClick={() => { setError(false); setAttempt((value) => value + 1) }}>Try again</button></div> : <p role="status">Opening Tokyo…</p>}
  </main>
  return <AtlasMap atlas={atlas} manifestUrl={manifestConfiguration.url} />
}

function AtlasMap({ atlas, manifestUrl }: { atlas: AtlasManifest; manifestUrl: string }) {
  const mapElement = useRef<HTMLDivElement>(null)
  const overviewElement = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<OpenSeadragon.Viewer | null>(null)
  const [layer, setLayer] = useState(() => parseView(location.hash, atlas.layers.map((item) => item.id))?.layer ?? atlas.layers[0].id)
  const layerRef = useRef(layer)
  const [loadedLayer, setLoadedLayer] = useState<string | null>(null)
  const ready = loadedLayer === layer
  const [failure, setFailure] = useState<'source' | 'tile' | null>(null)
  const [retry, setRetry] = useState(0)
  const [overview, setOverview] = useState(true)
  const [info, setInfo] = useState(false)
  const [message, setMessage] = useState('')
  const [zoom, setZoom] = useState(1)
  const [outside, setOutside] = useState(false)
  const [emptyView, setEmptyView] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const selected = atlas.layers.find((item) => item.id === layer)!
  const imageAspect = atlas.height / atlas.width
  const placeName = atlas.place?.name ?? atlas.title

  const currentView = useCallback((): ViewState | null => {
    const viewer = viewerRef.current
    if (!viewer || !viewer.isOpen()) return null
    const center = viewer.viewport.getCenter(true)
    return { x: center.x, y: center.y / imageAspect, z: viewer.viewport.getZoom(true), layer: layerRef.current }
  }, [imageAspect])

  const saveView = useCallback(() => {
    const view = currentView()
    if (!view) return
    history.replaceState(null, '', `${location.pathname}${location.search}${formatView(view)}`)
    setZoom(view.z)
    // The image rectangle is not a promise of geographic coverage outside its source patch.
    const bounds = viewerRef.current!.viewport.getBounds(true)
    setOutside(bounds.x < -0.02 || bounds.y < -0.02 || bounds.x + bounds.width > 1.02 || bounds.y + bounds.height > imageAspect + 0.02)
  }, [currentView, imageAspect])

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
    const desired = parseView(location.hash, atlas.layers.map((item) => item.id))
    const viewer = OpenSeadragon({
      element: mapElement.current,
      showNavigationControl: false,
      showNavigator: true,
      navigatorElement,
      navigatorAutoFade: false,
      navigatorDisplayRegionColor: '#215bd6',
      navigatorBorderColor: 'transparent',
      navigatorBackground: '#d5dade',
      visibilityRatio: 1,
      constrainDuringPan: true,
      minZoomLevel: 0.1,
      maxZoomLevel: 16,
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
      const view = parseView(location.hash, atlas.layers.map((item) => item.id))
      if (view && viewer.isOpen()) { setLayer(view.layer); layerRef.current = view.layer; apply(view) }
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
      else {
        viewer.viewport.goHome(true)
        viewer.viewport.zoomBy(1.12, undefined, true)
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
  }, [atlas, imageAspect, manifestUrl, retry, saveView])

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

  function reset() {
    viewerRef.current?.viewport.goHome()
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
    <div ref={mapElement} className="map-canvas" aria-label={`${placeName} interactive map`} role="region" tabIndex={0} onKeyDownCapture={keyboard} aria-describedby="map-help" data-ready={ready} data-layer={layer} data-coverage={emptyView ? 'empty' : 'available'} />
    <header className="masthead">
      <a className="brand" href={location.pathname} aria-label="isometric.tokyo, reset map">
        <span className="brand-glyph" lang="ja" aria-hidden="true">東京</span>
        <span className="wordmark">isometric<span>.tokyo</span></span>
      </a>
      <span className="source-tag"><span /> {atlas.ai_styling ? 'Illustrated atlas' : 'Source atlas'} <span className="edition">/ 01</span></span>
      <button className="icon-button info-button" aria-label="About this map and credits" onClick={() => setInfo(true)}><Icon name="info" /></button>
    </header>

    <section className="place-label" aria-label="Current coverage">
      <div className="place-japanese" lang="ja">{atlas.place?.name_ja ?? '東京'}</div>
      <h1>{placeName}</h1>
      <p>Tokyo, block by block.</p>
      <span className="coverage-label">{atlas.coverage.label}</span>
    </section>

    <div className="layer-control" role="group" aria-label="Map layers">
      {atlas.layers.length > 3 ? <label className="layer-select">Map layer<select value={layer} onChange={(event) => setLayer(event.target.value)}>{atlas.layers.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label> : atlas.layers.map((item) => <button key={item.id} aria-pressed={layer === item.id} onClick={() => setLayer(item.id)}>{item.label}</button>)}
    </div>

    <nav className="map-controls" aria-label="Map navigation">
      <button className="icon-button" aria-label="Zoom in" onClick={() => zoomBy(1.5)}><Icon name="plus" /></button>
      <button className="icon-button" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.5)}><Icon name="minus" /></button>
      <span className="control-divider" />
      <button className="icon-button" aria-label="Reset view" onClick={reset}><Icon name="home" /></button>
      <button className="icon-button" aria-label="Toggle overview map" aria-pressed={overview} onClick={() => setOverview(!overview)}><Icon name="overview" /></button>
      <button className="icon-button" aria-label="Copy link to this view" onClick={share}><Icon name="share" /></button>
    </nav>

    <aside className={`overview ${overview ? '' : 'is-hidden'}`} aria-label="Overview map" aria-hidden={!overview}>
      <div className="overview-label">{placeName.toUpperCase()} <span>OVERVIEW</span></div>
      <div id="atlas-overview" ref={overviewElement} />
    </aside>

    {emptyView && ready && !failure && <div className="map-notice coverage-empty" role="status">No rendered chunks in this view. Pan back to available imagery.</div>}
    {!ready && !failure && <div className="map-notice loading" role="status">Loading map tiles…</div>}
    {failure && <div className="map-notice failure" role="alert"><strong>{failure === 'source' ? 'This map layer could not open.' : 'Some map tiles could not load.'}</strong><span>{failure === 'source' ? 'The image source is missing or does not match the atlas.' : 'The missing imagery is a loading failure, not empty city.'}</span><button onClick={() => { setLoadedLayer(null); setFailure(null); setRetry((value) => value + 1) }}>Retry map</button></div>}
    {message && <div className="map-notice toast" role="status">{message}</div>}

    <footer className="map-footer">
      <div className="map-state"><span className="status-dot" /><span>{atlas.ai_styling ? 'Illustrated map' : 'Unstyled source data'}</span><span className="zoom-level" data-testid="zoom-level">{zoom.toFixed(2)}×</span></div>
      <p id="map-help">Drag to explore <span>·</span> Scroll or pinch to zoom <span>·</span> Focus map for arrow keys</p>
      <button className="credit-button" onClick={() => setInfo(true)}>Data & credits ↗</button>
    </footer>
    <div className="coverage-note">{emptyView ? 'Outside available rendered coverage.' : atlas.coverage.rectangles_px ? 'Chunked source atlas. Transparent gaps are unrendered.' : outside ? 'Outside the image: no additional coverage yet.' : 'Limited source coverage. Not a navigation map.'}</div>

    <dialog ref={dialog} className="info-dialog" aria-labelledby="about-title" onCancel={() => setInfo(false)} onClick={(event) => { if (event.target === dialog.current) setInfo(false) }}>
      <div className="dialog-header"><span className="eyebrow">THE SOURCE ATLAS</span><button className="icon-button" aria-label="Close map information" onClick={() => setInfo(false)}><Icon name="close" /></button></div>
      <h2 id="about-title">A first piece of Tokyo.</h2>
      <p>This is a small, explorable source study for an illustrated map of Tokyo. It is not the finished city map.</p>
      <dl><div><dt>Coverage</dt><dd>{atlas.coverage.label}</dd></div><div><dt>Source edition</dt><dd>{atlas.source_edition}</dd></div><div><dt>Atlas</dt><dd>{atlas.id}</dd></div><div><dt>AI styling</dt><dd>{atlas.ai_styling ? 'Applied. Compare against source imagery.' : 'None. These are direct 3D source renders.'}</dd></div>{atlas.bounds_px && <div><dt>Atlas pixel bounds</dt><dd>{atlas.bounds_px.join(', ')}</dd></div>}{atlas.source_coverage_verified !== undefined && <div><dt>Source completeness</dt><dd>{atlas.source_coverage_verified ? 'Verified for this release.' : 'Not verified. Rendered bounds do not prove complete geographic data.'}</dd></div>}{atlas.artwork_approved !== undefined && <div><dt>Artwork approval</dt><dd>{atlas.artwork_approved ? 'Approved for this release.' : 'Not approved. This is a source or working release.'}</dd></div>}</dl>
      <h3>Read the gaps, too.</h3>
      <p>White buildings can indicate missing textures or lower-detail geometry. Trees mostly appear in ground photography. Source datasets can have mixed building detail, acquisition dates and alignment limitations. It is not a live map or a navigation aid.</p>
      <p>{atlas.limitations}</p>
      <p>{atlas.modifications ?? "Independent project. No source-provider endorsement."}</p>
      <h3>Source credits</h3>
      <ul>{atlas.attribution.map((credit) => <li key={credit.url}><a href={credit.url} target="_blank" rel="noreferrer">{credit.label} ↗</a></li>)}</ul>
      {atlas.notice && <p><a href={new URL(atlas.notice, manifestUrl).href} target="_blank" rel="noreferrer">Full source terms and modification notice ↗</a></p>}
      {selected.original ? <a className="download-link" href={new URL(selected.original, manifestUrl).href} download>Download {selected.label.toLowerCase()} PNG ↓</a> : <p>Original images are retained per render chunk. This layer has no single-image download.</p>}
      <p className="dialog-help">Keyboard: arrow keys pan; + and − zoom; Home or 0 resets. Shared links preserve the image position, zoom and selected layer.</p>
    </dialog>
  </main>
}
