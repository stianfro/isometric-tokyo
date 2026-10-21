"""Bounded non-building context selection and preserved source feature identity."""

import copy
import io
import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_context import (
    BoundedCache,
    NoRedirect,
    context_request_budget,
    dependency_path,
    digest,
    download_context,
    select_items,
    validate_tileset,
    validate_url,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://assets.cms.plateau.reearth.io/fixture/tileset.json"
PAYLOAD = "https://assets.cms.plateau.reearth.io/fixture/tile.b3dm"


def glb(document):
    raw = json.dumps(document).encode()
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<4s4I", b"glTF", 2, 20 + len(raw), len(raw), 0x4E4F534A) + raw


def b3dm(embedded):
    feature = json.dumps({"BATCH_LENGTH": 2, "RTC_CENTER": [1, 2, 3]}).encode()
    batch = json.dumps(
        {
            "gml_id": ["tree-one", "tree-two"],
            "_lod": [3, 3],
            "gml:name": ["Original tree", None],
        }
    ).encode()
    length = 28 + len(feature) + len(batch) + len(embedded)
    return (
        struct.pack("<4s6I", b"b3dm", 1, length, len(feature), 0, len(batch), 0)
        + feature
        + batch
        + embedded
    )


class Response(io.BytesIO):
    def __init__(self, blob, known_length=True):
        super().__init__(blob)
        self.headers = {"Content-Length": str(len(blob))} if known_length else {}


class ContextFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "context"
        self.snapshot_path = self.root / "sources.json"
        self.config_path = ROOT / "configs/shinjuku.json"
        self.item = {
            "id": "trees-lod3",
            "dataset_id": "trees",
            "type": "veg",
            "name": "Trees",
            "format": "CESIUM3DTILES",
            "lod": 3,
            "texture": "TEXTURE",
            "url": SOURCE,
        }
        self.snapshot = {
            "schema_version": 1,
            "wards": [
                {
                    "code": "13104",
                    "edition": 2025,
                    "name_en": "Shinjuku",
                    "metadata_status": "confirmed",
                    "package": {
                        "id": "source-package",
                        "url": "https://www.geospatial.jp/ckan/dataset/fixture",
                        "license": {
                            "id": "plateau",
                            "url": "https://www.mlit.go.jp/plateau/site-policy/",
                        },
                    },
                    "catalog": {"items": [self.item]},
                }
            ],
        }
        self.tileset = {
            "asset": {"version": "1.0"},
            "root": {
                "boundingVolume": {
                    "region": [
                        math.radians(value) for value in [139.68, 35.68, 139.70, 35.70]
                    ]
                    + [0, 100]
                },
                "content": {"uri": "tile.b3dm"},
            },
        }
        self.embedded = glb(
            {"asset": {"version": "2.0"}, "nodes": [{"name": "Original node"}]}
        )
        self.blob = b3dm(self.embedded)
        self.calls = []

    def cache(self, blobs=None):
        data = {SOURCE: json.dumps(self.tileset).encode(), PAYLOAD: self.blob} | (
            blobs or {}
        )

        def opener(request, timeout):
            self.calls.append(request.full_url)
            self.assertEqual(timeout, 45)
            return Response(data[request.full_url])

        return BoundedCache(self.root / "cache", opener=opener)

    def run_download(self, *, cache=None, **kwargs):
        self.snapshot_path.write_text(json.dumps(self.snapshot))
        return download_context(
            self.snapshot_path,
            self.config_path,
            self.output,
            self.root / "cache",
            cache=cache or self.cache(),
            **kwargs,
        )

    def test_preserves_generic_source_ids_tables_glb_and_terms(self):
        result = self.run_download()
        self.assertEqual(result["kind"], "plateau-context")
        self.assertEqual(result["tile_count"], 1)
        self.assertEqual(result["feature_count"], 2)
        self.assertFalse(result["source_coverage_verified"])
        self.assertEqual(result["license"]["id"], "plateau")
        tile = result["tiles"][0]
        self.assertEqual(tile["context_type"], "veg")
        self.assertEqual(tile["rtc_center"], [1, 2, 3])
        self.assertEqual(tile["feature_ids"]["gml_id"], ["tree-one", "tree-two"])
        self.assertEqual(tile["lod_counts"], {"3": 2})
        self.assertEqual((self.output / tile["path"]).read_bytes(), self.blob)
        self.assertEqual((self.output / tile["glb_path"]).read_bytes(), self.embedded)
        self.assertEqual(tile["glb_sha256"], digest(self.embedded))
        tables = json.loads((self.output / tile["tables_path"]).read_text())
        self.assertEqual(tables["batch_table"]["gml:name"], ["Original tree", None])
        text = (self.output / "manifest.json").read_text()
        self.assertNotIn("building_count", text)
        self.assertNotIn(str(self.root), text)
        self.assertEqual(result["sources"][0]["id"], self.item["id"])

    def test_selects_highest_allowed_per_distinct_dataset_not_all_lods(self):
        items = self.snapshot["wards"][0]["catalog"]["items"]
        items.append(self.item | {"id": "trees-lod1", "lod": 1})
        items.append(self.item | {"id": "cover-lod1", "lod": 1, "dataset_id": "cover"})
        _, chosen = select_items(self.snapshot, "13104", ["veg"])
        self.assertEqual({item["id"] for item in chosen}, {"trees-lod3", "cover-lod1"})
        _, chosen = select_items(self.snapshot, "13104", ["veg"], 1)
        self.assertEqual({item["id"] for item in chosen}, {"trees-lod1", "cover-lod1"})
        with self.assertRaisesRegex(ValueError, "not listed"):
            select_items(self.snapshot, "13104", ["tran"])

    def test_empty_intersection_is_explicit_and_fetches_no_payload(self):
        self.tileset["root"]["boundingVolume"]["region"] = [
            math.radians(value) for value in [139.8, 35.8, 139.9, 35.9]
        ] + [0, 100]
        result = self.run_download(preflight_only=True)
        self.assertEqual(result["selected_items"][0]["intersecting_tiles"], 0)
        self.assertEqual(self.calls, [SOURCE])
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError, "No selected context"):
            self.run_download()
        self.assertEqual(self.calls, [SOURCE])

    def test_expanded_tile_budget_is_explicit_and_preflight_only(self):
        leaf = copy.deepcopy(self.tileset["root"])
        self.tileset["root"].pop("content")
        self.tileset["root"]["children"] = [
            leaf | {"content": {"uri": f"tile-{number}.b3dm"}} for number in range(65)
        ]
        with self.assertRaisesRegex(ValueError, "64-tile budget"):
            self.run_download(preflight_only=True)
        result = self.run_download(preflight_only=True, max_tiles=128)
        self.assertEqual(result["selected_items"][0]["intersecting_tiles"], 65)
        self.assertEqual(result["tile_budget"], 128)
        self.assertEqual(self.calls, [SOURCE])
        self.assertFalse(self.output.exists())
        for invalid in (0, 513, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                context_request_budget(invalid)
        self.assertEqual(context_request_budget(512), 2080)

    def test_request_budget_is_enforced_independently_of_bytes(self):
        cache = BoundedCache(
            self.root / "strict-cache",
            max_requests=1,
            opener=lambda request, timeout: Response(b"ok"),
        )
        cache.fetch(SOURCE)
        with self.assertRaisesRegex(ValueError, "request budget"):
            cache.fetch(PAYLOAD)
        for invalid in (0, 4097, True):
            with self.assertRaises(ValueError):
                BoundedCache(self.root / "invalid", max_requests=invalid)

    def test_injected_cache_cannot_overstate_declared_budget(self):
        with self.assertRaisesRegex(ValueError, "Injected cache"):
            self.run_download(max_bytes=1)
        self.assertEqual(self.calls, [])
        for invalid in (True, 0, 100_000_001):
            with self.assertRaises(ValueError):
                self.run_download(max_bytes=invalid)

    def test_unsupported_tileset_fails_before_payload(self):
        self.tileset["root"]["boundingVolume"] = {"box": [0] * 12}
        with self.assertRaisesRegex(ValueError, "region bounds"):
            self.run_download()
        self.assertEqual(self.calls, [SOURCE])
        self.assertFalse(self.output.exists())
        malformed = copy.deepcopy(self.tileset)
        malformed["root"]["implicitTiling"] = {}
        with self.assertRaisesRegex(ValueError, "Implicit"):
            validate_tileset(malformed)

    def test_external_dependencies_preserve_relative_paths_and_hashes(self):
        self.embedded = glb(
            {"asset": {"version": "2.0"}, "images": [{"uri": "textures/tree.png"}]}
        )
        self.blob = b3dm(self.embedded)
        dependency_url = (
            "https://assets.cms.plateau.reearth.io/fixture/textures/tree.png"
        )
        result = self.run_download(
            cache=self.cache({dependency_url: b"test-image-bytes"})
        )
        record = result["tiles"][0]["dependencies"][0]
        self.assertEqual(
            (self.output / record["path"]).read_bytes(), b"test-image-bytes"
        )
        self.assertEqual(record["sha256"], digest(b"test-image-bytes"))
        for uri in (
            "../escape",
            "%2e%2e/escape",
            "/absolute",
            "a\\b",
            "file:///bad",
            "a?query",
            "a#fragment",
        ):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                dependency_path(uri)

    def test_failed_replacement_keeps_previous_manifest(self):
        self.run_download()
        before = (self.output / "manifest.json").read_bytes()
        self.embedded = glb(
            {"asset": {"version": "2.0"}, "images": [{"uri": "../bad"}]}
        )
        self.blob = b3dm(self.embedded)
        cache = self.cache()
        cache.root = self.root / "second-cache"
        with self.assertRaises(ValueError):
            self.run_download(cache=cache)
        self.assertEqual((self.output / "manifest.json").read_bytes(), before)

    def test_known_length_never_reads_beyond_budget_and_cache_rechecks(self):
        blob = b"12345678"
        cache = BoundedCache(
            self.root / "small", 8, opener=lambda *_args, **_kwargs: Response(blob)
        )
        self.assertEqual(cache.fetch(SOURCE), blob)
        self.assertEqual(cache.network_bytes, 8)
        self.assertEqual(cache.source_bytes, 8)
        self.assertEqual(cache.fetch(SOURCE), blob)
        self.assertEqual(cache.source_bytes, 8)
        raw = next((self.root / "small").glob("*.bin"))
        raw.write_bytes(b"87654321")
        with self.assertRaisesRegex(ValueError, "checksum"):
            cache.fetch(SOURCE)
        small = BoundedCache(
            self.root / "too-small", 7, opener=lambda *_args, **_kwargs: Response(blob)
        )
        with self.assertRaisesRegex(ValueError, "remaining"):
            small.fetch(SOURCE)
        self.assertEqual(small.network_bytes, 0)

    def test_unknown_length_exhaustion_stops_without_overreading(self):
        stream = Response(b"1234567890123", known_length=False)
        cache = BoundedCache(
            self.root / "stream", 8, opener=lambda *_args, **_kwargs: stream
        )
        with self.assertRaisesRegex(ValueError, "EOF"):
            cache.fetch(SOURCE)
        self.assertEqual(cache.network_bytes, 8)
        self.assertFalse(list((self.root / "stream").glob("*.bin")))

    def test_partial_retry_bytes_count_toward_actual_network_cap(self):
        class Partial(Response):
            def __init__(self):
                super().__init__(b"123456")
                self.calls = 0

            def read(self, size=-1):
                self.calls += 1
                if self.calls > 1:
                    raise URLError("interrupted")
                return super().read(min(size, 5))

        streams = iter([Partial(), Response(b"123456")])
        cache = BoundedCache(
            self.root / "retry", 10, opener=lambda *_args, **_kwargs: next(streams)
        )
        with self.assertRaisesRegex(ValueError, "remaining"):
            cache.fetch(SOURCE)
        self.assertEqual(cache.network_bytes, 5)
        self.assertEqual(cache.source_bytes, 0)
        self.assertEqual(cache.requests, 2)

    def test_safety_limits_and_invalid_regions(self):
        for budget in (0, True, 100_000_001):
            with self.assertRaises(ValueError):
                BoundedCache(self.root, budget)
        for url in (
            "file:///tmp/model",
            "https://untrusted.example/model.glb",
            "https://assets.cms.plateau.reearth.io/full.zip",
            "https://assets.cms.plateau.reearth.io/full%2ezip",
            "https://assets.cms.plateau.reearth.io:8080/model.glb",
            "https://assets.cms.plateau.reearth.io/model.glb?download=1",
        ):
            with self.assertRaises(ValueError):
                validate_url(url)
        self.tileset["root"]["boundingVolume"]["region"][0] = math.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_tileset(self.tileset)

    def test_content_bounds_and_structural_validation(self):
        for mutation in (
            {"content": {"boundingVolume": {"region": [math.nan] * 6}}},
            {"children": {}},
            {"children": [None]},
            {"transform": None},
            {"boundingVolume": None},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_tileset({"root": self.tileset["root"] | mutation})

    def test_redirects_require_explicit_adapter(self):
        with self.assertRaisesRegex(ValueError, "redirects"):
            NoRedirect().redirect_request(None, None, 302, "", {}, SOURCE)

    def test_unbatched_glb_count_is_unknown(self):
        url = PAYLOAD.replace(".b3dm", ".glb")
        self.tileset["root"]["content"]["uri"] = "tile.glb"
        result = self.run_download(cache=self.cache({url: self.embedded}))
        self.assertIsNone(result["feature_count"])
        self.assertIsNone(result["tiles"][0]["feature_count"])


if __name__ == "__main__":
    unittest.main()
