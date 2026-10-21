"""Independent preview views, provenance gates and immutable release integrity."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_preview_release as preview


class PreviewReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.output = self.root / "preview"
        for name in preview.INPUTS:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        self.art = self.repo / "previews/shinjuku-art"
        Image.new("RGB", (67, 41), (31, 51, 71)).save(self.art / "ai-pixel-city.png")
        Image.new("RGB", (49, 33), (120, 87, 13)).save(
            self.art / "source-reference.png"
        )
        Image.new("RGB", (49, 33), (24, 52, 88)).save(self.art / "atlas-ink.png")
        self.ai = {
            "ai_styling": True,
            "production_release": False,
            "candidates": [
                {
                    "id": "pixel-city",
                    "sha256": preview.digest(self.art / "ai-pixel-city.png"),
                    "resolution_px": [67, 41],
                }
            ],
            "reference_public_png_sha256": preview.digest(self.art / "atlas-ink.png"),
            "reference_rgb_sha256": hashlib.sha256(
                Image.open(self.art / "atlas-ink.png").tobytes()
            ).hexdigest(),
        }
        self.write_ai()
        preview.write_json(
            self.art / "style-pilot.json",
            {
                "ai_styling": False,
                "outputs": [
                    {
                        "id": "source-reference",
                        "sha256": preview.digest(self.art / "source-reference.png"),
                        "width": 49,
                        "height": 33,
                    }
                ],
            },
        )
        preview.write_json(
            self.repo / "configs/art-direction.json",
            {
                "preferred_direction": "pixel-city",
                "artwork_approved": False,
                "production_release": False,
                "geographic_fidelity_approved": False,
            },
        )
        preview.write_json(
            self.repo / "configs/shinjuku-context.json",
            {"source_bounds_wsen": [139, 35, 140, 36]},
        )
        data = self.repo / "datasets/shinjuku-vegetation"
        (data / "SHA256SUMS").write_text(
            "".join(
                f"{preview.digest(data / name)}  {name}\n"
                for name in (
                    "vegetation.json.gz",
                    "NOTICE.txt",
                    "README.txt",
                    "query.overpassql",
                )
            )
        )

    def write_ai(self):
        preview.write_json(self.art / "ai-studies.json", self.ai)

    def build(self):
        return preview.build_release(self.repo, self.output, tile_size=32)

    def version(self):
        return (
            self.output
            / "releases"
            / json.loads((self.output / "manifest.json").read_text())["id"]
        )

    def test_independent_native_dimensions_and_unapproved_provenance(self):
        result = self.build()
        self.assertEqual(result, preview.verify_release(self.output))
        catalog = json.loads((self.output / "manifest.json").read_text())
        self.assertFalse(catalog["aligned_comparison"])
        self.assertFalse(catalog["production_release"])
        art = json.loads((self.version() / "pixel-city/manifest.json").read_text())
        source = json.loads((self.version() / "source/manifest.json").read_text())
        self.assertEqual((art["width"], art["height"]), (67, 41))
        self.assertEqual((source["width"], source["height"]), (49, 33))
        self.assertTrue(art["ai_styling"])
        self.assertFalse(source["ai_styling"])
        self.assertFalse(art["artwork_approved"])
        self.assertEqual(
            (self.version() / "pixel-city/original.png").read_bytes(),
            (self.art / "ai-pixel-city.png").read_bytes(),
        )
        self.assertTrue((self.version() / "data/vegetation.json.gz").exists())

    def test_idempotent_immutable_versions_and_notice_identity(self):
        first = self.build()
        original = self.version()
        self.assertEqual(first, self.build())
        (self.repo / "THIRD_PARTY_NOTICES.md").write_text("Changed attribution\n")
        second = self.build()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first, preview.verify_version(original, first["id"]))
        self.assertEqual(second, preview.verify_release(self.output))

    def test_corrupt_existing_release_is_not_overwritten(self):
        self.build()
        original = self.version() / "NOTICE.txt"
        original.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.build()
        self.assertEqual(original.read_text(), "corrupt")

    def test_bad_image_provenance_preserves_active_catalog(self):
        self.build()
        original = (self.output / "manifest.json").read_bytes()
        Image.new("RGB", (67, 41)).save(self.art / "ai-pixel-city.png")
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.build()
        self.assertEqual(original, (self.output / "manifest.json").read_bytes())
        preview.verify_release(self.output)

    def test_no_implicit_geography_approval(self):
        selection = self.repo / "configs/art-direction.json"
        data = json.loads(selection.read_text())
        data["artwork_approved"] = True
        preview.write_json(selection, data)
        with self.assertRaisesRegex(ValueError, "approval"):
            self.build()

    def test_metadata_leak_rejected(self):
        info = PngImagePlugin.PngInfo()
        info.add_text("File", "/private/example/render.blend")
        Image.new("RGB", (67, 41)).save(self.art / "ai-pixel-city.png", pnginfo=info)
        self.ai["candidates"][0]["sha256"] = preview.digest(
            self.art / "ai-pixel-city.png"
        )
        self.write_ai()
        with self.assertRaisesRegex(ValueError, "embedded metadata"):
            self.build()

    def test_symlinks_and_unowned_output_rejected(self):
        self.output.mkdir()
        (self.output / "precious.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.build()
        self.assertEqual((self.output / "precious.txt").read_text(), "keep")
        path = self.art / "ai-pixel-city.png"
        path.rename(path.with_suffix(".saved"))
        path.symlink_to(path.with_suffix(".saved"))
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.build()

    def test_unexpected_files_and_catalog_paths_fail(self):
        self.build()
        extra = self.version() / "secret.env"
        extra.write_text("not published")
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            preview.verify_release(self.output)
        extra.unlink()
        catalog = json.loads((self.output / "manifest.json").read_text())
        catalog["views"][0]["manifest"] = "../outside/manifest.json"
        preview.write_json(self.output / "manifest.json", catalog)
        with self.assertRaisesRegex(ValueError, "catalog"):
            preview.verify_release(self.output)

    def test_missing_tile_and_wrong_descriptor_fail(self):
        self.build()
        dzi = self.version() / "pixel-city/illustration.dzi"
        original = dzi.read_text()
        dzi.write_text(original.replace('Width="67"', 'Width="68"'))
        with self.assertRaisesRegex(ValueError, "descriptor"):
            preview.verify_release(self.output)
        dzi.write_text(original)
        (self.version() / "pixel-city/illustration_files/0/0_0.webp").unlink()
        with self.assertRaises(FileNotFoundError):
            preview.verify_release(self.output)

    def test_input_mutation_during_build_does_not_activate(self):
        original = preview.pyramid

        def mutate(*args, **kwargs):
            original(*args, **kwargs)
            (self.repo / "THIRD_PARTY_NOTICES.md").write_text("changed during build")

        with patch.object(preview, "pyramid", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed during"):
                self.build()
        self.assertFalse((self.output / "manifest.json").exists())

    def refresh_record(self, relative):
        path = self.version() / relative
        inventory = self.version() / "assets.json"
        records = json.loads(inventory.read_text())
        for record in records["assets"]:
            if record["path"] == relative:
                record.update(sha256=preview.digest(path), bytes=path.stat().st_size)
        preview.write_json(inventory, records)

    def test_lower_level_pixel_tamper_cannot_be_hidden_by_inventory(self):
        self.build()
        name = "pixel-city/illustration_files/0/0_0.webp"
        Image.new("RGB", (1, 1), (255, 0, 0)).save(
            self.version() / name, "WEBP", lossless=True
        )
        self.refresh_record(name)
        with self.assertRaisesRegex(ValueError, "Pyramid pixels"):
            preview.verify_release(self.output)

    def test_view_notice_cannot_be_changed_with_inventory(self):
        self.build()
        name = "pixel-city/NOTICE.txt"
        (self.version() / name).write_text("No attribution")
        self.refresh_record(name)
        with self.assertRaisesRegex(ValueError, "View notice"):
            preview.verify_release(self.output)

    def test_invalid_geographic_bounds_never_activate(self):
        for bbox in (
            [0, 0, 0, 0],
            [1, 2, 3],
            [0, 0, 181, 20],
            [True, 0, 2, 3],
            [0, 0, float("nan"), 3],
        ):
            preview.write_json(
                self.repo / "configs/shinjuku-context.json",
                {"source_bounds_wsen": bbox},
            )
            with self.assertRaises(ValueError):
                self.build()
            self.assertFalse((self.output / "manifest.json").exists())

    def test_bounds_and_asset_traversal(self):
        for value in (0, 15, 2049, True):
            with self.assertRaises(ValueError):
                preview.build_release(self.repo, self.output, value)
        for name in ("../else", "/tmp/else", "a//b", "a/./b", "a\\b"):
            with self.assertRaises(ValueError):
                preview.safe_file(self.root, name)
        with patch.object(preview, "MAX_INPUT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                self.build()


if __name__ == "__main__":
    unittest.main()
