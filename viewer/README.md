# Interactive source atlas

A React, TypeScript and OpenSeadragon viewer for aligned image pyramids. The
current atlas is an unstyled Shinjuku source study, not a completed Tokyo map.
No runtime image generation or geographic-data downloads happen in the viewer.

Use the repository's `just` tasks to install dependencies, generate the sample
atlas, run the viewer, and run checks. The generated atlas belongs under
`public/atlas/`; it is not part of the application source. Dependency versions
are pinned in `package-lock.json`, and the npm cache is project-local.

## Selecting a release

The default manifest is `atlas/manifest.json`, relative to the application URL.
Set `VITE_ATLAS_MANIFEST=chunks/current/manifest.json` before starting development
or building to select a generated chunk release instead. This is build-time
configuration, not a URL parameter. Restart development or rebuild after changing
it. The repository's chunk-viewer tasks set this value for their generated output.

The override must be a nonempty relative file path below the application directory.
Remote URLs, leading slashes, traversal, dot segments, encoded characters and URL
query/fragment syntax are rejected. An invalid value shows a configuration error
at startup and makes no atlas request. An unset value uses the default; an empty
value is an error. DZI files, source notices and optional image downloads remain
relative to the selected manifest, not to the original sample directory.

## Manifest contract

`public/atlas/manifest.json` describes one release. The parser requires schema
version 1, positive native image dimensions, a tile size and overlap, unique
layer identifiers, relative DZI and original-image paths, source attribution,
coverage bounds, source edition and an explicit AI-styling flag. The optional
modification notice, limitations and full-notice link are shown with the credits.
Unsafe paths and invalid dimensions stop loading rather than guessing.

Every comparison layer must have exactly the same dimensions and image alignment.
The viewer verifies DZI dimensions at load time. It cannot verify geographic
alignment on its own; that is checked by the renderer and atlas-export tests.

Image pyramids load only the image tiles needed for a view. Original PNGs are
available as an explicit download, never used as a whole-atlas viewer fallback.
Failed image tiles produce an error with retry. Outside-coverage text does not
hide loading failures.

## View state and controls

A link such as `#x=0.50000&y=0.50000&z=2.00000&layer=textured` contains:

- `x` and `y`: image position normalized separately by width and height, from 0 to 1.
- `z`: viewer width divided by the visible image-space width. A larger number
  zooms in. It does not depend on image resolution, device pixel ratio or a
  particular geographic map projection.
- `layer`: a layer identifier from the current manifest.

The same link restores the same horizontal image extent on a different screen;
the vertical extent changes with the screen's aspect ratio. Viewport constraints
can center an image that is smaller than the available view. Unknown layers or
invalid/non-finite values reset to a safe initial view. Layer changes retain the
position and zoom. Navigation updates the current URL without filling browser
history with individual pans.

Drag or swipe to pan; scroll, pinch or double-click to zoom. Focus the map and
use arrow keys to pan, `+` or `-` to zoom, and `Home` or `0` to reset. The overview
can be hidden. Credits open in a modal dialog with keyboard focus containment.
Reduced-motion preferences remove map animation.

## Verification

- ESLint and TypeScript check the application and browser tests.
- Vitest tests valid/invalid manifests, unsafe asset paths and URL round trips.
- Playwright tests desktop and emulated mobile navigation, including real touch
  events, URL restoration, layer alignment, errors/retry, keyboard use and credits.
- The development app uses React StrictMode to exercise mount/unmount cleanup.
- Set `SCREENSHOT_DIR` when running browser tests to save desktop and mobile
  preview PNGs. The output directory is supplied by the caller, not embedded in
  application files.

Browser tests use the full Chromium channel. Emulation is not a substitute for
benchmarking a physical phone, and these tests do not claim citywide performance.

## Renderer-chunk pyramids

The same viewer accepts a sparse export from the chunk renderer, not only the
original flat sample. A chunk export still needs a viewer `manifest.json` with
explicit source attribution, edition and limitations. `pyramid-index.json` by
itself is an exporter integrity index, not a public map manifest and does not
supply those source notices.

Chunk manifests add:

- `bounds_px: [x0, y0, x1, y1]`: signed global atlas-pixel bounds. Their width and
  height must equal the local DZI image dimensions.
- `coverage.rectangles_px`: the nonempty list of rendered chunk rectangles in the
  same global pixel frame. Each rectangle must fit within `bounds_px`. These are
  rendered-image extents, not proof of complete geographic source data.
- Optional `place: {name, name_ja}` for the visible location label. Without it,
  the manifest title is used rather than assuming every fixture is Shinjuku.
- Optional boolean `source_coverage_verified` and `artwork_approved` fields. False
  is displayed as unverified or unapproved, not inferred from available imagery.
- Layer `original` is optional. Omit it when originals remain separate render
  chunks; the viewer does not invent a whole-image download link.

DZI `DisplayRect` coordinates are local: subtract the global atlas origin from
each recorded chunk rectangle. The viewer checks descriptor dimensions, format,
tile size, overlap, levels and rectangles against the manifest for every layer.
A shifted or differently covered comparison layer is rejected. The standard DZI
sparse-tile logic decides which tile requests exist. Empty rendered coverage has
its own message and finishes loading; an actual failed tile remains an error.

URL x/y remain normalized to the local exported image, including for negative or
nonzero global origins and nonsquare exports. `imageToAtlas` and `atlasToImage`
provide the explicit conversion. These are image-grid coordinates, not latitude
and longitude and not an inverse from arbitrary roof pixels to ground locations.

Browser interoperability fixtures call the actual Python chunk exporter with two
small synthetic chunks, signed global offsets, paired layers and an intentional
missing grid cell. Set `PYTHON` to the Python environment containing the root
project requirements when running browser tests. Fixture output stays under the
project's `.cache/`; it never changes the active public atlas or source data.
