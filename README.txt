SHINJUKU: UNSTYLED PLATEAU SOURCE STUDY
Prepared 21 October 2026. No AI styling or image generation.

SAMPLE AND METHOD
Approximately 797 x 799 m around the Tokyo Metropolitan Government buildings,
centred at 139.6917 E, 35.6896 N. Bounds: 139.6873, 35.6860 to 139.6961, 35.6932.
The 26 downloaded building tiles total 45,664,305 bytes and contain 1,076 source
features: 751 LOD2 and 325 LOD1. These are whole-tile counts, not counts of the
fewer buildings visible inside the cropped render.

Original B3DM files and extracted GLBs are preserved. CESIUM_RTC metadata is
removed only from temporary Blender import copies; the exact RTC centre and
tile transforms are applied before conversion from ECEF to local east/north/up.
Building geometry is cut at the crop boundary, not manually remodelled.
The original photographic building textures are retained.

Ground uses PLATEAU-Ortho 2023 imagery draped onto actual PLATEAU terrain, not a
flat display plane. Two zoom-14 terrain tiles total 7,441 bytes, with 545 vertices
and 1,023 faces before cropping. Terrain heights inside the crop are approximately
64.16-82.55 m above the reference ellipsoid, not heights above sea level.
Render: Blender 5.2, Cycles CPU, 2,400 x 2,400 pixels, 32 samples, no denoising.

WHAT TO JUDGE
Landmark forms are recognizable. Facades are photographs on simplified surfaces,
with limited balcony/window depth. Some smaller western blocks are untextured.
Trees in this sample exist only in the aerial ground image, not as 3D vegetation.
Baked photographic shadows and road/building ghosting remain visible. Geometry
is promising for a styled map; ground imagery and vegetation need a separate
strategy. These are observations of this sample, not a citywide quality audit.

SOURCE DATES AND RIGHTS
Buildings: Project PLATEAU, Tokyo/Shinjuku FY2025, specification 5.0. The original
archive README is dated 13 March 2026. FY2025 is an edition, not a capture date.
Its source-history CSV lists texture photography from Kokusai Kogyo FY2020 and
Tokyo FY2023; LOD2 laser sources from MLIT FY2020 and Tokyo FY2023. Footprint/LOD1
map sources include 2021/2025 editions with 2018/2022 photography. These dates
are dataset-level, not a verified assignment to each building in this crop.
Reviewed metadata: metadata/udx_13104_2025_resource_op.csv and the original
README.md inside the linked CityGML archive, read by small HTTP byte ranges.

The original dataset README explicitly permits choosing CC BY 4.0, including
commercial reuse. The current PLATEAU policy requires attribution and identifying
processing. Underlying city-model copyright belongs to the relevant authority.
This processed rendering is not an official PLATEAU product or endorsement.
Credit: Project PLATEAU / Tokyo Metropolitan Government, Shinjuku FY2025.
Ground credit: MLIT Project PLATEAU, PLATEAU-Ortho 2023 edition (not a verified
capture date); the service can combine PLATEAU and Geospatial Information
Authority of Japan imagery. Terrain credit: PLATEAU | Mapterhorn | 国土地理院.
All layers were selected, transformed, cropped and rendered for this study.

Buildings and original archive: https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025
Policy: https://www.mlit.go.jp/plateau/site-policy/
CC BY 4.0: https://creativecommons.org/licenses/by/4.0/
Ground: https://docs.plateauview.mlit.go.jp/datasets/ortho/
Terrain: https://docs.plateauview.mlit.go.jp/datasets/terrain/
Terrain endpoint: https://tile.plateauview.mlit.go.jp/terrain/layer.json
Exact building/tile URLs and checksums are recorded in data/sample/manifest.json;
ground sources are in data/ground/manifest.json, terrain sources in data/terrain/.

FILES AND REPRODUCTION
shinjuku-isometric.png: wide textured view. shinjuku-detail.png: closer inspection.
shinjuku-geometry.png: geometry comparison. shinjuku-source.blend: editable scene,
with textures packed, opens directly in Blender. render-metadata.json: settings.
shinjuku-source-kit.zip: scripts, tests, justfile and cached raw/derived source data.
No installation is needed to view the PNGs.

To rerender the extracted kit: install Blender 5.2, just, uv and Python 3.11+ with
Pillow (requirements.txt). Ruff is supplied through uvx; caches stay under .cache/.
Run: just check
     just --set out ./renders render
     just --set out ./renders verify
     just --set out ./renders verify-scene
     just --set out ./renders package
The cached data is complete. Optional refetch tasks: just fetch; just ground;
just terrain. PYTHON can override the default python3 executable. Fetches require
network access; published upstream datasets may change after this snapshot.
