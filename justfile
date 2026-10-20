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

lint:
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 check scripts tests
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 format --check scripts tests
    {{python}} -m compileall -q scripts tests

format:
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 format scripts tests

fix:
    UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools uvx ruff@0.17.1 check --fix scripts tests
    just format

test:
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

