import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_vegetation import (
    load_vegetation,
    register_vegetation_objects,
    vegetation_provenance,
)


class RenderVegetationTests(unittest.TestCase):
    def source(self):
        return {
            "schema_version": 1,
            "kind": "source-vegetation",
            "canopy_samples": [
                {
                    "id": "node-123",
                    "source_id": "node/123",
                    "placement": "mapped_tree",
                    "lon": 139.69,
                    "lat": 35.69,
                    "radius_m": 3,
                    "height_m": 8,
                    "dimensions_measured": False,
                }
            ],
        }

    def test_source_dimensions_are_explicitly_representational(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(self.source()))
            self.assertEqual(len(load_vegetation(path)["canopy_samples"]), 1)
            for field, bad in [
                ("dimensions_measured", True),
                ("placement", "surveyed_tree"),
                ("radius_m", -1),
                ("height_m", 100),
                ("lon", float("nan")),
            ]:
                data = self.source()
                data["canopy_samples"][0][field] = bad
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    load_vegetation(path)

    def test_nested_source_credits_and_public_data_reference_survive(self):
        source = {
            "attribution": "OpenStreetMap contributors",
            "license": {"id": "ODbL-1.0"},
            "public_data_reference": {
                "path": "osm-source-data.geojson",
                "sha256": "a" * 64,
            },
        }
        result = vegetation_provenance(
            {
                "source": {
                    key: value
                    for key, value in source.items()
                    if key != "public_data_reference"
                },
                "public_data_reference": source["public_data_reference"],
            }
        )
        self.assertEqual(result["source_attribution"], source["attribution"])
        self.assertEqual(result["source_license"], source["license"])
        self.assertEqual(
            result["public_data_reference"], source["public_data_reference"]
        )

    def test_representation_registry_is_deterministic_and_not_surveyed(self):
        objects = [
            {
                "atlas_direct_feature_key": key,
                "vegetation_source_id": "way/123",
                "vegetation_placement": "artistic_woodland_fill",
            }
            for key in ["b", "a", "b"]
        ]
        registry = {
            "records": [{"id": 0, "kind": "background"}, {"id": 1, "kind": "ground"}]
        }
        register_vegetation_objects(registry, objects)
        self.assertEqual(registry["direct_feature_ids"], {"a": 2, "b": 3})
        for item in registry["records"][2:]:
            self.assertEqual(item["source_id"], "way/123")
            self.assertFalse(item["surveyed_tree"])
            self.assertFalse(item["dimensions_measured"])
            self.assertNotIn("gml_id", item)
            self.assertNotIn("source_building_id", item)


if __name__ == "__main__":
    unittest.main()
