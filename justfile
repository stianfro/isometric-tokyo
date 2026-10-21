set shell := ["sh", "-eu", "-c"]

python := env("PYTHON", "python3")
out := env("OUTPUT_DIR", "renders")

default:
    just --list

fetch:
    {{python}} scripts/fetch_sample.py --output data/sample --bbox 139.6873 35.6860 139.6961 35.6932

ground:
    {{python}} scripts/fetch_ground.py --output data/ground --bbox 139.6873 35.6860 139.6961 35.6932

terrain:
    {{python}} scripts/fetch_terrain.py --output data/terrain

lint: python-lint viewer-lint review-lint

python-lint:
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 check scripts tests viewer/e2e
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 format --check scripts tests viewer/e2e
    {{python}} -m compileall -q scripts tests viewer/e2e

format:
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 format scripts tests viewer/e2e

fix:
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 check --fix scripts tests viewer/e2e
    just format

test: python-test viewer-test review-test

python-test:
    {{python}} -m unittest discover -s tests -v

check: lint test

render:
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_sample.py -- --manifest data/sample/manifest.json --ground data/ground/manifest.json --terrain data/terrain/mesh.json --output {{out}}

verify:
    {{python}} scripts/verify_outputs.py --output {{out}}

verify-scene:
    blender --background {{out}}/shinjuku-source.blend --python-exit-code 1 --python scripts/verify_blend.py -- --output {{out}}/scene-verification.json

package: verify
    {{python}} scripts/package_sample.py --output {{out}}

previews:
    {{python}} scripts/prepare_previews.py --source {{out}} --output previews

# Atlas contract and bounded image export.
atlas-validate:
    {{python}} scripts/atlas.py validate --config configs/shinjuku.json

atlas-plan:
    {{python}} scripts/atlas.py plan --config configs/shinjuku.json --output .cache/atlas/shinjuku-plan.json

tiles: atlas-validate
    {{python}} scripts/build_atlas.py --config configs/shinjuku.json

verify-atlas:
    {{python}} scripts/build_atlas.py --verify

# Browser development. Install dependencies once before lint/test/build.
viewer-install:
    cd viewer && npm ci

viewer-browsers:
    cd viewer && PLAYWRIGHT_BROWSERS_PATH=../.cache/playwright npx playwright install chromium

viewer-dev: tiles
    cd viewer && npm run dev

viewer-lint:
    cd viewer && npm run lint

viewer-test:
    cd viewer && npm test

viewer-build: tiles
    cd viewer && npm run build

viewer-e2e: tiles
    export PYTHON="$({{python}} -c 'import sys; print(sys.executable)')"; cd viewer && PLAYWRIGHT_BROWSERS_PATH=../.cache/playwright npm run test:e2e

# Full loaded source meshes are retained for every pixel-aligned chunk.
atlas-render destination="renders/atlas-shinjuku": atlas-validate
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_atlas.py -- --config configs/shinjuku.json --manifest data/sample/manifest.json --ground data/ground/manifest.json --terrain data/terrain/mesh.json --output "{{destination}}"
    {{python}} scripts/render_geometry.py finalize --index "{{destination}}/render-index.json" --assemble

atlas-finalize directory="renders/atlas-shinjuku":
    {{python}} scripts/render_geometry.py finalize --index "{{directory}}/render-index.json" --assemble

seam-check:
    #!/usr/bin/env sh
    set -eu
    mkdir -p .cache
    directory="$(mktemp -d .cache/seam-check.XXXXXX)"
    if ! blender --background --factory-startup --python-exit-code 1 --python scripts/render_atlas.py -- --config configs/shinjuku.json --fixture --samples 128 --guides --output "$directory" > "$directory/render.log" 2>&1; then
        tail -60 "$directory/render.log"
        exit 1
    fi
    {{python}} scripts/render_geometry.py finalize --index "$directory/render-index.json" --verify-seams

jobs *args="list":
    {{python}} scripts/jobs.py {{args}}

integration: viewer-build verify-atlas viewer-e2e seam-check

chunk-tiles index output:
    {{python}} scripts/chunk_pyramid.py --index "{{index}}" --output "{{output}}"

chunk-verify output:
    {{python}} scripts/chunk_pyramid.py --verify --output "{{output}}"

source-inventory:
    {{python}} scripts/inventory_sources.py --refresh

source-inventory-offline:
    {{python}} scripts/inventory_sources.py --offline

review-import index:
    {{python}} scripts/review_server.py --import-index "{{index}}"

review:
    {{python}} scripts/review_server.py

review-lint:
    ./viewer/node_modules/.bin/eslint --config review/eslint.config.mjs review/review.js review/model.mjs review/model.test.mjs

review-test:
    node --test review/model.test.mjs

atlas-batch config="configs/shinjuku.json" destination="renders/batch-shinjuku" *args="":
    {{python}} scripts/run_render_jobs.py --config "{{config}}" --manifest data/sample/manifest.json --ground data/ground/manifest.json --terrain data/terrain/mesh.json --output "{{destination}}" {{args}}

bootstrap:
    test -d .venv || uv venv .venv
    uv pip install --python .venv/bin/python -r requirements.txt
    just viewer-install

viewer-browsers-ci:
    cd viewer && PLAYWRIGHT_BROWSERS_PATH=../.cache/playwright npx playwright install --with-deps chromium

atlas-render-guides destination="renders/atlas-guides": atlas-validate
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_atlas.py -- --config configs/shinjuku.json --manifest data/sample/manifest.json --ground data/ground/manifest.json --terrain data/terrain/mesh.json --guides --output "{{destination}}"
    {{python}} scripts/render_geometry.py finalize --index "{{destination}}/render-index.json"

context-fetch:
    {{python}} scripts/fetch_context.py --types veg tran wtr

context-preflight:
    {{python}} scripts/fetch_context.py --types veg --preflight-only

style-pilot destination="renders/style-pilot":
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_style_pilot.py -- render --config configs/shinjuku.json --manifest data/sample/manifest.json --ground data/ground/manifest.json --terrain data/terrain/mesh.json --output "{{destination}}"
    {{python}} scripts/render_style_pilot.py finalize --output "{{destination}}"

review-region *args:
    {{python}} scripts/review_regions.py {{args}}

chunk-viewer index output metadata="configs/shinjuku-viewer.json":
    {{python}} scripts/chunk_pyramid.py --index "{{index}}" --output "{{output}}" --viewer-metadata "{{metadata}}"

atlas-render-context destination="renders/atlas-context": atlas-validate
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_atlas.py -- --config configs/shinjuku.json --manifest data/sample/manifest.json --ground data/ground/manifest.json --terrain data/terrain/mesh.json --context data/context/manifest.json --guides --output "{{destination}}"
    {{python}} scripts/render_geometry.py finalize --index "{{destination}}/render-index.json"

viewer-dev-chunks index metadata="configs/shinjuku-viewer.json":
    just chunk-viewer "{{index}}" viewer/public/chunks/current "{{metadata}}"
    cd viewer && VITE_ATLAS_MANIFEST=chunks/current/manifest.json npm run dev

viewer-build-chunks index metadata="configs/shinjuku-viewer.json":
    just chunk-viewer "{{index}}" viewer/public/chunks/current "{{metadata}}"
    cd viewer && VITE_ATLAS_MANIFEST=chunks/current/manifest.json npm run build

repair-release *args:
    {{python}} scripts/apply_repairs.py {{args}}

art-pilot config manifest ground terrain context vegetation destination="renders/shinjuku-art":
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_style_pilot.py -- render --config "{{config}}" --manifest "{{manifest}}" --ground "{{ground}}" --terrain "{{terrain}}" --context "{{context}}" --vegetation "{{vegetation}}" --presets configs/shinjuku-art-presets.json --samples 128 --resolution 1024 --max-source-tiles 512 --output "{{destination}}"
    {{python}} scripts/render_style_pilot.py finalize --output "{{destination}}"

style-render *args:
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_style_pilot.py -- render {{args}}

style-finalize destination:
    {{python}} scripts/render_style_pilot.py finalize --output "{{destination}}"

vegetation-fetch *args:
    {{python}} scripts/fetch_vegetation.py {{args}}

coverage *args:
    {{python}} scripts/source_coverage.py {{args}}

terrain-fetch *args:
    {{python}} scripts/fetch_terrain.py {{args}}

context-fetch-bounded *args:
    {{python}} scripts/fetch_context.py {{args}}

# Full fixed-camera source study, with bounded public-data acquisition.
ground-study:
    {{python}} scripts/fetch_ground.py --bbox 139.67688 35.67835 139.70549 35.70169 --zoom 17 --max-bytes 50000000 --output data/ground-study-v2

terrain-study:
    {{python}} scripts/fetch_terrain.py --bbox 139.67688 35.67835 139.70549 35.70169 --zoom 15 --method native --output data/terrain-study-v2

context-study:
    just context-fetch-bounded --config configs/shinjuku-context.json --output data/context-study-v2 --types tran wtr veg --max-tiles 128 --max-bytes 64000000

art-verify:
    {{python}} -m unittest discover -s tests -p test_art_assets.py -v

# About eight minutes on the measured CPU. Use a fresh destination for every run.
source-study destination="renders/source-study":
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_atlas.py -- --config configs/shinjuku-source-check.json --manifest data/coverage-buildings/manifest.json --ground data/ground-study-v2/manifest.json --terrain data/terrain-study-v2/mesh.json --context data/context-study-v2/manifest.json --vegetation data/vegetation/manifest.json --guides --reference --max-source-tiles 512 --max-source-mib 1024 --output "{{destination}}"
    {{python}} scripts/render_geometry.py finalize --index "{{destination}}/render-index.json" --assemble --verify-seams

# Independent art/source views, not an aligned or approved geographic atlas.
preview-tiles:
    {{python}} scripts/build_preview_release.py

preview-verify:
    {{python}} scripts/build_preview_release.py --verify

preview-dev: preview-tiles
    cd viewer && VITE_ATLAS_MANIFEST=preview/manifest.json npm run dev

preview-build: preview-tiles
    cd viewer && ISOMETRIC_SKIP_PUBLIC=1 VITE_ATLAS_MANIFEST=preview/manifest.json npm run build

preview-package: preview-build
    {{python}} scripts/package_preview.py --build viewer/dist --preview viewer/public/preview --output renders/web-releases

viewer-e2e-preview: tiles
    export PYTHON="$({{python}} -c 'import sys; print(sys.executable)')"; cd viewer && PLAYWRIGHT_BROWSERS_PATH=../.cache/playwright npm run test:e2e -- e2e/preview.spec.ts

preview-package-verify directory:
    {{python}} scripts/package_preview.py --verify "{{directory}}"

# Native 3072px source, one camera, exact 3 by 3 source and guide cores.
pilot-source destination="renders/shinjuku-pilot-source-v1":
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_pilot_source.py -- render --output "{{destination}}"
    {{python}} scripts/render_pilot_source.py finalize --output "{{destination}}"

pilot-source-test:
    {{python}} -m unittest discover -s tests -p test_pilot_source.py -v

art-pilot-grid *args:
    {{python}} scripts/art_pilot.py {{args}}

art-pilot-test:
    {{python}} -m unittest discover -s tests -p test_art_pilot.py -v

# Native patch map. These tasks never reactivate the older image study.
map-tiles index config evidence:
    {{python}} scripts/build_map_release.py --index "{{index}}" --config "{{config}}" --evidence "{{evidence}}" --output viewer/public/preview

map-verify:
    {{python}} scripts/build_map_release.py --verify --output viewer/public/preview

map-build: map-verify
    cd viewer && ISOMETRIC_SKIP_PUBLIC=1 VITE_ATLAS_MANIFEST=preview/manifest.json npm run build

map-package: map-build
    {{python}} scripts/package_preview.py --build viewer/dist --preview viewer/public/preview --output renders/web-releases

map-dev: map-verify
    cd viewer && VITE_ATLAS_MANIFEST=preview/manifest.json npm run dev

# Fixed-grid geographic expansion, preserving the native pilot's coordinates.
expansion-source-plan:
    just coverage plan --config configs/shinjuku-expansion-source.json --output .cache/coverage/expansion-plan.json

ground-fetch *args:
    {{python}} scripts/fetch_ground.py {{args}}

expansion-vegetation *args:
    {{python}} scripts/fetch_expansion_vegetation.py {{args}}

expansion-source destination="renders/shinjuku-expansion-source-v1":
    blender --background --factory-startup --python-exit-code 1 --python scripts/render_expansion_source.py -- render --output "{{destination}}"
    {{python}} scripts/render_expansion_source.py finalize --output "{{destination}}"

expansion-source-finalize destination="renders/shinjuku-expansion-source-v1":
    {{python}} scripts/render_expansion_source.py finalize --output "{{destination}}"

expansion-source-test:
    {{python}} -m unittest discover -s tests -p test_expansion_source.py -v

art-expansion-test:
    {{python}} -m unittest discover -s tests -p test_art_expansion.py -v
