"""Offline source inventory tests, including bounded metadata-only fetching."""

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inventory_sources import (
    CKAN,
    GRAPHQL,
    MAX_RESPONSE_BYTES,
    PORTAL,
    DatasetLinks,
    MetadataCache,
    catalog_by_ward,
    catalog_query,
    inventory,
    load_config,
    package_summary,
    readiness,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/sources-tokyo-wards.json"


class Response(io.BytesIO):
    def __init__(self, content, length=None):
        super().__init__(content)
        self.headers = {} if length is None else {"Content-Length": str(length)}


class SourceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config(CONFIG_PATH)
        self.package = "plateau-13104-shinjuku-ku-2025"
        self.ckan = {
            "success": True,
            "result": {
                "id": "confirmed-package-uuid",
                "name": self.package,
                "title": "Shinjuku 2025",
                "state": "active",
                "private": False,
                "author_email": "do-not-export@example.com",
                "license_id": "plateau",
                "license_url": "https://www.mlit.go.jp/plateau/site-policy/",
                "metadata_modified": "2026-04-03T05:57:45",
                "resources": [
                    {
                        "id": "archive-id",
                        "name": "CityGML",
                        "format": "ZIP",
                        "url": "https://assets.cms.plateau.reearth.io/example.zip",
                        "size": None,
                        "description": "Do not copy full prose",
                    },
                    {"id": "inline", "url": "data:text/markdown;base64,ignored"},
                ],
            },
        }
        self.catalog = {
            "data": {
                "datasets": [
                    {
                        "id": "d_13104_bldg",
                        "name": "Buildings",
                        "year": 2025,
                        "cityCode": "13104",
                        "typeCode": "bldg",
                        "items": [
                            {
                                "id": "di_13104_bldg_lod2",
                                "name": "LOD2",
                                "url": "https://assets.cms.plateau.reearth.io/source/tileset.json",
                                "format": "CESIUM3DTILES",
                                "lod": 2,
                                "lodEx": None,
                                "texture": "TEXTURE",
                                "formatVersion": "1.0",
                                "compositeUrl": "https://api.plateauview.mlit.go.jp/datacatalog/3dtiles/13104-bldg-lod2-2025/tileset.json",
                            }
                        ],
                    }
                ],
                "citygmlDatasets": [
                    {
                        "id": "cg_13104",
                        "year": 2025,
                        "cityCode": "13104",
                        "featureTypes": ["dem", "bldg"],
                        "gspatialjpDatasetUrl": "https://www.geospatial.jp/ckan/dataset/"
                        + self.package,
                        "metadataZipUrls": [
                            "https://assets.cms.plateau.reearth.io/metadata.zip"
                        ],
                        "plateauSpecMinor": {"version": "5.0"},
                    }
                ],
            }
        }

    def test_target_has_exactly_the_23_wards_and_invalid_codes_rejected(self):
        self.assertEqual(len(self.config["wards"]), 23)
        config = copy.deepcopy(self.config)
        config["wards"][0]["code"] = "99999"
        path = self.root / "config.json"
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "codes"):
            load_config(path)
        config["edition"] = True
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "edition"):
            load_config(path)

    def test_portal_links_confirm_packages_and_ignore_other_editions(self):
        parser = DatasetLinks(2025)
        parser.feed(
            f'<a href="https://www.geospatial.jp/ckan/dataset/{self.package}">2025</a><a href="https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2023">old</a><a href="https://untrusted.example/ckan/dataset/plateau-13101-chiyoda-ku-2025">ignore</a>'
        )
        self.assertEqual(parser.links, {"13104": self.package})
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            parser.feed(
                '<a href="https://www.geospatial.jp/ckan/dataset/plateau-13104-other-ku-2025">conflict</a>'
            )

    def test_package_output_is_allowlisted_metadata_not_raw_record(self):
        result = package_summary(self.ckan, self.package)
        self.assertEqual(result["id"], "confirmed-package-uuid")
        self.assertEqual(len(result["resources"]), 1)
        self.assertNotIn("author_email", result)
        self.assertNotIn("description", result["resources"][0])
        self.assertIsNone(result["resources"][0]["size_bytes"])
        self.assertEqual(
            result["metadata_modified"], self.ckan["result"]["metadata_modified"]
        )
        with self.assertRaisesRegex(ValueError, "identity"):
            package_summary(self.ckan, "wrong-package")
        self.ckan["result"]["private"] = True
        with self.assertRaisesRegex(ValueError, "public"):
            package_summary(self.ckan, self.package)

    def test_catalog_claims_never_mark_spatial_quality_verified(self):
        by_ward = catalog_by_ward(self.catalog, self.config)
        result = readiness(by_ward["13104"])
        self.assertEqual(result["building_lods_listed"], [2])
        self.assertTrue(result["textured_building_tileset_listed"])
        self.assertTrue(result["terrain_in_citygml_catalog"])
        self.assertFalse(result["spatial_coverage_verified"])
        self.assertFalse(result["ready_for_district_production"])
        self.assertIsNone(result["acquisition_dates"])
        self.assertIsNone(result["per_building_texture_completeness"])
        self.assertFalse(readiness(by_ward["13101"])["building_tilesets_listed"])

    def test_wrong_year_duplicates_and_partial_graphql_are_rejected(self):
        broken = copy.deepcopy(self.catalog)
        broken["errors"] = [{"message": "partial response"}]
        with self.assertRaisesRegex(ValueError, "partial"):
            catalog_by_ward(broken, self.config)
        broken = copy.deepcopy(self.catalog)
        broken["data"]["datasets"][0]["year"] = 2023
        with self.assertRaisesRegex(ValueError, "outside"):
            catalog_by_ward(broken, self.config)
        broken = copy.deepcopy(self.catalog)
        broken["data"]["datasets"].append(broken["data"]["datasets"][0])
        with self.assertRaisesRegex(ValueError, "outside"):
            catalog_by_ward(broken, self.config)

    def test_cache_offline_hashes_and_request_identity(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.data, timeout))
            return Response(b'{"ok":true}')

        cache = MetadataCache(self.root, opener=opener)
        self.assertEqual(cache.fetch(GRAPHQL, {"query": "first"}), '{"ok":true}')
        self.assertEqual(cache.requests, 1)
        offline = MetadataCache(
            self.root,
            offline=True,
            opener=lambda *_: self.fail("Offline mode opened the network"),
        )
        self.assertEqual(offline.fetch(GRAPHQL, {"query": "first"}), '{"ok":true}')
        self.assertEqual(offline.evidence[0], cache.evidence[0])
        with self.assertRaisesRegex(ValueError, "absent"):
            offline.fetch(GRAPHQL, {"query": "different"})
        path = next(self.root.glob("*.json"))
        data = json.loads(path.read_text())
        data["body"] = '{"bad":true}'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "checksum"):
            offline.fetch(GRAPHQL, {"query": "first"})

    def test_network_budget_size_limits_and_no_model_downloads(self):
        cache = MetadataCache(
            self.root,
            opener=lambda *_args, **_kwargs: Response(b"x", MAX_RESPONSE_BYTES + 1),
        )
        with self.assertRaisesRegex(ValueError, "size limit"):
            cache.fetch(PORTAL)
        with self.assertRaisesRegex(ValueError, "official metadata"):
            cache.fetch("https://assets.cms.plateau.reearth.io/archive.zip")
        cache.requests = 52
        with self.assertRaisesRegex(ValueError, "request limit"):
            cache.fetch(PORTAL)
        with self.assertRaises(ValueError):
            MetadataCache(self.root, offline=True, refresh=True)

    def test_network_retry_is_bounded(self):
        attempts = []

        def opener(*_args, **_kwargs):
            attempts.append(True)
            raise URLError("offline fixture")

        cache = MetadataCache(self.root, opener=opener, sleep=lambda _: None)
        with self.assertRaises(URLError):
            cache.fetch(PORTAL)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_inventory_links_all_three_sources_and_flags_missing_portal_entry(self):
        requested = []

        def opener(request, timeout):
            requested.append(request.full_url)
            if request.full_url == PORTAL:
                return Response(
                    f'<a href="https://www.geospatial.jp/ckan/dataset/{self.package}">Shinjuku</a>'.encode()
                )
            if request.full_url == GRAPHQL:
                self.assertIn('"13123"', json.loads(request.data)["query"])
                return Response(json.dumps(self.catalog).encode())
            self.assertTrue(request.full_url.startswith(CKAN + "?id="))
            return Response(json.dumps(self.ckan).encode())

        cache = MetadataCache(self.root, opener=opener)
        result = inventory(self.config, cache)
        self.assertEqual(result["summary"]["packages_confirmed"], 1)
        self.assertEqual(result["summary"]["production_ready_wards"], 0)
        self.assertEqual(result["wards"][0]["metadata_status"], "not_listed_in_portal")
        self.assertEqual(result["wards"][3]["metadata_status"], "confirmed")
        self.assertEqual(len(requested), 3)
        self.assertEqual(len(result["evidence"]), 3)
        self.assertIn("year: 2025", catalog_query(self.config))


if __name__ == "__main__":
    unittest.main()
