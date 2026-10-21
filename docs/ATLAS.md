# Atlas coordinate contract

The version 1 contract is implemented in `scripts/atlas.py`, with a matching
`configs/shinjuku.json` and an editor-facing JSON Schema in
`schemas/atlas-config.schema.json`. The Python validator is authoritative: it
also checks finite numbers, ordered bounds, camera degeneracy and duplicate
JSON keys. Unknown fields are rejected instead of silently ignored.

## Coordinates and the existing sample

- Geographic input uses WGS84 longitude, latitude and **ellipsoidal height**.
- Geometry uses a fixed local east, north, up (ENU) frame in metres. Its ECEF
  origin is longitude 139.6917, latitude 35.6896, ellipsoidal height zero.
- ENU up is the tangent-frame coordinate, not an interchangeable synonym for
  ellipsoidal height. Earth curvature matters away from the origin.
- The camera looks from target plus `(1500, -1500, 1500)` toward the target.
  East points down-right, north up-right, and vertical structures point up.
- The target is `(0, 0, 120.08777160644532)` ENU metres, projected to atlas pixel
  `(1200, 1200)`. Its up value preserves the original sample framing: the recorded
  display-ground level 65.08777160644532 plus 55 metres.
- Native scale is `2400 / 1260` pixels per projected metre. The existing overview
  and geometry-only PNGs both cover `[0, 0, 2400, 2400]`. The detail PNG has a
  different camera and is not an aligned comparison layer.
- Atlas X increases right; Y increases down. Integer coordinates identify pixel
  **edges**. Pixel `(i, j)` samples `(i + 0.5, j + 0.5)`.
- Rectangles are half-open: `[left, top, right, bottom]`. Negative coordinates are
  allowed for future expansion without moving existing pixels.

The geographic `source_bounds_wsen` identifies the original source selection,
not a full-image coverage guarantee. The overview contains background outside
that area. It is not evidence of empty city. The existing sample also contains
cropped source geometry; a new camera contract does not repair that geometry.

## Projection and inverse

Let `F` be the normalized camera-forward vector, `R = normalize(F × ENU_up)`,
and `U = normalize(R × F)`. For world point `P`, target `T`, pixel origin `O`
and scale `s`:

```text
x = O.x + s * dot(P - T, R)
y = O.y - s * dot(P - T, U)
```

`world_to_pixel(config, point_enu)` and
`geographic_to_pixel(config, lon, lat, ellipsoidal_height_m)` implement these
transforms. No chunk-local offset is part of this global projection.

`pixel_to_world(config, pixel, up_m)` intersects the viewing ray with a known
constant-ENU-up plane. It cannot determine a terrain or rooftop position without
height/depth information. The same pixel has different east/north positions at
different heights. Terrain picking needs a ray/surface intersection, and landmark
markers need known elevation or a deliberate manually checked placement.

## Independent chunk cameras

`camera_for_bounds(config, bounds_px, overscan_px=0)` returns a JSON-native plan:

| Field | Meaning |
| --- | --- |
| `bounds_px` | Final core rectangle in global atlas pixels |
| `render_bounds_px` | Core plus the explicit image-space margin |
| `crop_box_px` | Half-open core crop in the expanded rendered image |
| `resolution_px` | Expanded image width and height |
| `target_enu`, `location_enu` | Global ENU camera placement |
| `right_enu`, `up_enu`, `forward_enu` | Orthonormal camera basis |
| `ortho_scale_m` | Expanded image width divided by native pixel scale |
| `sensor_fit` | `HORIZONTAL`, set explicitly for rectangular images |
| `source_footprint_enu_m` | Conservative east/north source-selection envelope |

The camera target is the image center projected onto the original target's
constant-up plane. Camera direction and pixel scale never change between chunks.
Set Blender pixel aspect to 1:1 and sensor fit to the returned value. A render job
may subtract a local rebase vector from both geometry and camera for numerical
precision, but must retain global coordinates in its manifest.

Render complete structures and their surroundings, then crop the image with
`crop_box_px`. Never cut geometry at the core image boundary. Overscan pixels
help image filtering and patch comparison; they do not replace fetching towers
and other objects outside the ground footprint.

`source_footprint_enu()` projects all image corners at both ends of the configured
height interval to ENU XY, then expands by `shadow_buffer_m`. The supplied sample
interval `[-100, 650]` metres and 600-metre shadow buffer are conservative planning
inputs, not a verified coverage certificate. Confirm actual geometry heights,
sun direction, shadow reach and available source coverage. A plan explicitly
returns `source_coverage_verified: false`. The original bounded sample cannot
satisfy every expanded footprint and must not be labelled a complete district.

`plan_chunks(config)` uses a global zero-anchored grid. With the sample's
800-pixel cores and 32-pixel overscan, it returns nine render plans. Edge chunks
are clipped to the requested atlas extent, not shifted off the grid. Chunk IDs
such as `x0_y0` describe grid positions; they are not sufficient content IDs.

## Identity and validation

`config.fingerprint` hashes canonical validated JSON. Whitespace and key order do
not matter; settings do. `job_fingerprint(config, bounds_px, input_hashes,
tool_versions)` also includes exact core bounds, checked SHA256 input hashes and
relevant tool versions. Use logical source IDs, not local filenames. A plan with
only a config hash is not proof that source assets are unchanged.

The validator rejects non-finite values, booleans used as numbers, unsupported
versions, malformed or empty bounds, unbounded planning requests, and cameras
that are horizontal or directly overhead. The camera is deliberately oblique.
The schema can assist an editor, but schema validation alone does not check all
cross-field constraints.

Run `just atlas-validate` to validate the sample, `just atlas-plan` to write its
chunk plan, and `just check` for the complete lint and test suite. The module also
accepts `validate` or `plan`, `--config`, optional `--output`, and a bounded
`--max-chunks` override. These tasks do not download source data or render images.

Tests include the original camera fixture, known-height round trips, image-axis
orientation, a non-unique inverse example, invariance across all nine cameras,
shared chunk edges, pixel-center spacing, negative/partial extents, conservative
height envelopes, source/tool identity changes and invalid JSON. A passing math
test is necessary but not sufficient: renderer integration must compare the
chunked image against a single larger reference render.
