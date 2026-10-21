# Interactive source atlas

A React, TypeScript and OpenSeadragon viewer for aligned image pyramids. The
current atlas is an unstyled Shinjuku source study, not a completed Tokyo map.
No runtime image generation or geographic-data downloads happen in the viewer.

Use the repository's `just` tasks to install dependencies, generate the sample
atlas, run the viewer, and run checks. The generated atlas belongs under
`public/atlas/`; it is not part of the application source. Dependency versions
are pinned in `package-lock.json`, and the npm cache is project-local.

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
