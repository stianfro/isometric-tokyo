import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_context import (
    CONTEXT_SEMANTICS,
    SEMANTIC_IDS,
    asset_path,
    context_assets,
    separation_summary,
    verify_context_assets,
)
from render_guides import feature_registry, source_registry


class RenderContextTests(unittest.TestCase):
    def make_manifest(self, root):
        paths = [
            ("path", "sha256", "tile.b3dm"),
            ("glb_path", "glb_sha256", "tile.glb"),
            ("tables_path", "tables_sha256", "tables.json"),
        ]
        tile = {"context_type": "tran", "dependencies": []}
        for field, checksum, name in paths:
            payload = name.encode()
            (root / name).write_bytes(payload)
            tile[field] = name
            tile[checksum] = hashlib.sha256(payload).hexdigest()
        manifest = {
            "schema_version": 1,
            "kind": "plateau-context",
            "status": "complete",
            "tiles": [tile],
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return path

    def test_context_classes_are_not_buildings(self):
        self.assertEqual(
            CONTEXT_SEMANTICS,
            {"tran": "transportation", "wtr": "water", "veg": "vegetation"},
        )
        self.assertEqual(SEMANTIC_IDS["building"], 2)
        self.assertEqual(SEMANTIC_IDS["transportation"], 3)
        self.assertEqual(SEMANTIC_IDS["water"], 4)

    def test_separations_are_reported_without_adjustment(self):
        self.assertIsNone(separation_summary([]))
        summary = separation_summary([12, -3, 0, 2, 4])
        self.assertEqual(summary["minimum_m"], -3)
        self.assertEqual(summary["median_m"], 2)
        self.assertEqual(summary["maximum_m"], 12)

    def test_complete_assets_are_verified_before_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, assets, counts = context_assets(self.make_manifest(root))
            self.assertEqual(counts, {"tran": 1})
            verify_context_assets(assets)
            (root / "tile.glb").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_context_assets(assets)

    def test_recorded_source_metadata_is_checked_with_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_manifest(root)
            data = json.loads(path.read_text())
            payload = b'{"asset":{"version":"1.0"}}'
            (root / "tileset.json").write_bytes(payload)
            data["sources"] = [
                {
                    "metadata_path": "tileset.json",
                    "metadata_sha256": hashlib.sha256(payload).hexdigest(),
                }
            ]
            path.write_text(json.dumps(data))
            _, assets, _ = context_assets(path)
            self.assertEqual(len(assets), 4)
            verify_context_assets(assets)
            (root / "tileset.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_context_assets(assets)

    def test_source_metadata_pair_hash_and_paths_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_manifest(root)
            data = json.loads(path.read_text())
            for source in [
                {"metadata_path": "tileset.json"},
                {"metadata_sha256": "a" * 64},
                {"metadata_path": "tileset.json", "metadata_sha256": "bad"},
                {"metadata_path": "../escape.json", "metadata_sha256": "a" * 64},
                {"metadata_path": None, "metadata_sha256": "a" * 64},
            ]:
                data["sources"] = [source]
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    context_assets(path)
            data["sources"] = [{"id": "source-without-recorded-metadata"}]
            path.write_text(json.dumps(data))
            _, assets, _ = context_assets(path)
            self.assertEqual(len(assets), 3)

    def test_unsupported_and_incomplete_context_cannot_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_manifest(root)
            original = json.loads(path.read_text())
            for mutate in (
                lambda data: data.update(status="partial"),
                lambda data: data["tiles"][0].update(context_type="invented"),
                lambda data: data["tiles"][0].update(
                    dependencies=[{"path": "external.png"}]
                ),
                lambda data: data["tiles"][0].update(glb_path="../outside.glb"),
            ):
                data = json.loads(json.dumps(original))
                mutate(data)
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    context_assets(path)
            with self.assertRaises(ValueError):
                asset_path(root, "/absolute.glb")

    def test_combined_registry_checks_original_context_and_preserves_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "buildings.json").write_text(json.dumps({"tiles": []}))
            glb_json = b"{}  "
            glb = struct.pack("<4s4I", b"glTF", 2, 24, 4, 0x4E4F534A) + glb_json
            feature = json.dumps({"BATCH_LENGTH": 1}).encode()
            batch = json.dumps({"gml_id": ["traf-original"]}).encode()
            payload = (
                struct.pack(
                    "<4s6I",
                    b"b3dm",
                    1,
                    28 + len(feature) + len(batch) + len(glb),
                    len(feature),
                    0,
                    len(batch),
                    0,
                )
                + feature
                + batch
                + glb
            )
            (root / "road.b3dm").write_bytes(payload)
            (root / "road.glb").write_bytes(glb)
            tile = {
                "path": "road.b3dm",
                "glb_path": "road.glb",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "context_type": "tran",
                "source_item_id": "source-road",
            }
            (root / "context.json").write_text(json.dumps({"tiles": [tile]}))
            registry, hashes = source_registry(
                root / "buildings.json", context_path=root / "context.json"
            )
            record = next(
                item for item in registry["records"] if item["kind"] == "transportation"
            )
            self.assertEqual(record["gml_id"], "traf-original")
            self.assertEqual(record["source_item_id"], "source-road")
            self.assertNotIn("source_building_id", record)
            self.assertIn("context-b3dm-0", hashes)
            (root / "road.b3dm").write_bytes(payload + b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                source_registry(
                    root / "buildings.json", context_path=root / "context.json"
                )

    def test_source_registry_keeps_feature_semantics_distinct(self):
        tables = {
            "buildings": [{"gml_id": "same-id", "source_building_id": "official"}],
            "roads": [{"gml_id": "same-id", "source_item_id": "roads-lod3"}],
            "water": [{"gml_id": "pond", "source_item_id": "water-lod1"}],
        }
        kinds = {"buildings": "building", "roads": "transportation", "water": "water"}
        registry = feature_registry(tables, kinds)
        records = [
            item
            for item in registry["records"]
            if item["id"] > 1 and item["kind"] != "unresolved-source-tile"
        ]
        self.assertEqual(
            [item["kind"] for item in records], ["building", "transportation", "water"]
        )
        self.assertNotEqual(
            registry["tile_batch_feature_ids"]["buildings"],
            registry["tile_batch_feature_ids"]["roads"],
        )
        self.assertEqual(
            feature_registry(dict(reversed(list(tables.items()))), kinds), registry
        )
        fallbacks = [
            item
            for item in registry["records"]
            if item["kind"] == "unresolved-source-tile"
        ]
        self.assertEqual(
            {item["semantic_kind"] for item in fallbacks}, set(kinds.values())
        )


if __name__ == "__main__":
    unittest.main()
