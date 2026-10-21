"""Native nine-patch publication, explicit repair provenance and review gates."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_map_release as release
from atlas import load_config
from package_preview import package_release, verify_package, verify_preview

ROOT = Path(__file__).resolve().parents[1]


class NativeMapReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output = self.root / "public"
        self.index_path = self.inputs / "render-index.json"
        self.config_path = self.inputs / "config.json"
        self.evidence_path = self.inputs / "evidence.json"
        config = json.loads((ROOT / "configs/shinjuku-context.json").read_text())
        config.update(
            bounds_px=[0, 0, 3072, 3072],
            chunk_size_px=1024,
            pixel_origin=[1536, 1536],
            pixels_per_metre=3072 / 1260,
        )
        self.config_path.write_text(json.dumps(config))
        self.index = {
            "schema_version": 1,
            "status": "complete",
            "ai_styling": True,
            "bounds_px": [0, 0, 3072, 3072],
            "chunk_size_px": 1024,
            "config_sha256": load_config(self.config_path).fingerprint,
            "layers": ["styled", "textured"],
            "chunks": [],
        }
        self.evidence = {
            "schema_version": 1,
            "artwork_method": "ai-assisted",
            "native_artwork": True,
            "upscaled_artwork": False,
            "artwork_approved": False,
            "geographic_fidelity_approved": False,
            "comparison_mode": "shared-framing-unverified",
            "comparison_note": "Synthetic test pixels have no geographic accuracy.",
            "generation_tool": "Synthetic fixture",
            "model_version": "No image model used by this test",
            "prompt": "Synthetic fixture, not production art.",
            "patches": [],
            "repairs": [],
            "seam_review": {
                "status": "reviewed",
                "joins": [
                    {
                        "id": ident,
                        "passed": True,
                        "note": "Synthetic fixture review only",
                    }
                    for ident in sorted(release.JOINS)
                ],
                "junctions": [
                    {
                        "id": ident,
                        "passed": True,
                        "note": "Synthetic fixture review only",
                    }
                    for ident in sorted(release.JUNCTIONS)
                ],
                "note": "Synthetic fixture, not a real geographic review",
            },
        }
        for y in range(3):
            for x in range(3):
                ident = f"x{x}_y{y}"
                left, top = x * 1024, y * 1024
                folder = self.inputs / ident
                folder.mkdir()
                with Image.new(
                    "RGB", (1280, 1280), (10 + x * 30, 20 + y * 30, 70)
                ) as image:
                    image.save(folder / "generated.png")
                    image.crop((128, 128, 1152, 1152)).save(folder / "styled.png")
                with Image.new(
                    "RGB", (1024, 1024), (30 + x * 20, 60 + y * 20, 110)
                ) as image:
                    image.save(folder / "textured.png")
                outputs = {
                    layer: {
                        "path": f"{ident}/{layer}.png",
                        "width": 1024,
                        "height": 1024,
                        "bytes": (folder / f"{layer}.png").stat().st_size,
                        "sha256": release.digest(folder / f"{layer}.png"),
                    }
                    for layer in self.index["layers"]
                }
                self.index["chunks"].append(
                    {
                        "id": ident,
                        "bounds_px": [left, top, left + 1024, top + 1024],
                        "outputs": outputs,
                    }
                )
                self.evidence["patches"].append(
                    {
                        "id": ident,
                        "styled_sha256": outputs["styled"]["sha256"],
                        "source_sha256": outputs["textured"]["sha256"],
                        "generation_output": {
                            "path": f"{ident}/generated.png",
                            "sha256": release.digest(folder / "generated.png"),
                        },
                        "crop_box_px": [128, 128, 1152, 1152],
                        "source_bounds_px": [
                            left - 128,
                            top - 128,
                            left + 1152,
                            top + 1152,
                        ],
                        "reference_sha256": [outputs["textured"]["sha256"]],
                    }
                )
        for name in release.PUBLIC_INPUTS:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic licence or database fixture\n")
        data = self.repo / "datasets/shinjuku-vegetation"
        (data / "SHA256SUMS").write_text(
            "".join(
                f"{release.digest(data / name)}  {name}\n"
                for name in (
                    "vegetation.json.gz",
                    "NOTICE.txt",
                    "README.txt",
                    "query.overpassql",
                )
            )
        )
        self.save()

    def save(self):
        self.index_path.write_text(json.dumps(self.index))
        self.evidence_path.write_text(json.dumps(self.evidence))

    def validate(self):
        self.save()
        return release.validate_inputs(
            self.index_path, self.config_path, self.evidence_path
        )

    def build(self):
        self.save()
        return release.build_release(
            self.index_path,
            self.config_path,
            self.evidence_path,
            self.output,
            self.repo,
        )

    def version(self):
        ident = json.loads((self.output / "manifest.json").read_text())["id"]
        return self.output / "releases" / ident

    def test_native_release_is_verified_reproducible_and_stays_unapproved(self):
        first = self.build()
        self.assertEqual(first, release.verify_release(self.output))
        self.assertEqual(first, verify_preview(self.output))
        self.assertEqual(first, self.build())
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual((manifest["width"], manifest["height"]), (3072, 3072))
        self.assertEqual(
            [layer["id"] for layer in manifest["layers"]], ["styled", "textured"]
        )
        self.assertTrue(manifest["ai_styling"])
        for flag in (
            "production_release",
            "source_coverage_verified",
            "artwork_approved",
            "geographic_fidelity_approved",
        ):
            self.assertFalse(manifest[flag])
        self.assertEqual(manifest["comparison_mode"], "shared-framing-unverified")
        for layer in manifest["layers"]:
            self.assertTrue((self.output / layer["dzi"]).is_file())
        published = (self.version() / "evidence.json").read_text()
        self.assertNotIn(str(self.root), published)
        self.assertNotIn(
            str(self.inputs), (self.version() / "source-index.json").read_text()
        )
        # Verify the copied release through the existing static web packager.
        build = self.repo / "viewer/dist"
        (build / "assets").mkdir(parents=True)
        (build / "index.html").write_text('<script src="./assets/app.js"></script>')
        (build / "assets/app.js").write_text('const path = "preview/manifest.json";')
        (self.repo / "viewer/package.json").write_text(
            json.dumps({"dependencies": {"react": "1.0"}})
        )
        dependency = self.repo / "viewer/node_modules/react"
        dependency.mkdir(parents=True)
        (dependency / "package.json").write_text(
            json.dumps({"name": "react", "version": "1.0"})
        )
        (dependency / "LICENSE").write_text("Synthetic runtime licence fixture")
        package = package_release(build, self.output, self.root / "web", self.repo)
        self.assertEqual(
            verify_package(Path(package["directory"]))["preview_id"], first["id"]
        )

    def test_missing_or_duplicate_chunks_and_wrong_ai_provenance_fail(self):
        original = list(self.index["chunks"])
        for chunks in (original[:-1], [*original[:-1], original[0]]):
            self.index["chunks"] = chunks
            with self.assertRaises(ValueError):
                self.validate()
        self.index["chunks"] = original
        self.index["ai_styling"] = False
        with self.assertRaisesRegex(ValueError, "AI-assisted"):
            self.validate()

    def test_wrong_world_frame_and_mismatched_config_hash_fail(self):
        config = json.loads(self.config_path.read_text())
        config["pixels_per_metre"] = 1
        self.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "world framing"):
            self.validate()
        config["pixels_per_metre"] = 3072 / 1260
        self.config_path.write_text(json.dumps(config))
        self.index["config_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "configuration identity"):
            self.validate()

    def test_visual_review_requires_every_join_and_junction(self):
        original = json.loads(json.dumps(self.evidence["seam_review"]))
        for change in (
            {"status": "pending"},
            {"joins": original["joins"][:-1]},
            {"junctions": original["junctions"][:-1]},
            {"note": ""},
            {"joins": [original["joins"][0]] * 12},
        ):
            self.evidence["seam_review"] = original | change
            with self.assertRaises(ValueError):
                self.validate()
        self.evidence["seam_review"] = original
        self.evidence["seam_review"]["joins"][0]["passed"] = False
        with self.assertRaisesRegex(ValueError, "explicitly pass"):
            self.validate()

    def test_generation_claims_cannot_authorize_scaling_or_approval(self):
        for field, value in (
            ("native_artwork", False),
            ("upscaled_artwork", True),
            ("artwork_approved", True),
            ("geographic_fidelity_approved", True),
            ("comparison_mode", "exactly-registered"),
        ):
            original = self.evidence[field]
            self.evidence[field] = value
            with self.assertRaises(ValueError):
                self.validate()
            self.evidence[field] = original

    def test_crop_and_generation_dimensions_must_match_without_resizing(self):
        record = self.evidence["patches"][0]
        original = record["crop_box_px"]
        record["crop_box_px"] = [129, 128, 1153, 1152]
        with self.assertRaisesRegex(ValueError, "global source bounds"):
            self.validate()
        record["crop_box_px"] = original
        record["source_bounds_px"] = [-128, -128, 1153, 1152]
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.validate()

    def test_final_pixels_must_match_native_crops(self):
        path = self.inputs / "x0_y0/styled.png"
        with Image.new("RGB", (1024, 1024), "red") as image:
            image.save(path)
        checksum = release.digest(path)
        self.index["chunks"][0]["outputs"]["styled"].update(
            sha256=checksum, bytes=path.stat().st_size
        )
        self.evidence["patches"][0]["styled_sha256"] = checksum
        with self.assertRaisesRegex(ValueError, "native crops"):
            self.validate()

    def test_reused_generation_image_cannot_masquerade_as_nine_new_patches(self):
        self.evidence["patches"][1]["generation_output"] = self.evidence["patches"][0][
            "generation_output"
        ]
        with self.assertRaisesRegex(ValueError, "distinct native"):
            self.validate()

    def test_ordered_native_repairs_cross_chunk_edges_without_blending(self):
        bounds = [1000, 1000, 1048, 1048]
        self.add_repair("first", bounds, "red")
        self.add_repair("second", [1010, 1010, 1038, 1038], "blue")
        self.validate()
        self.evidence["repairs"].reverse()
        with self.assertRaisesRegex(ValueError, "ordered exact repairs"):
            self.validate()

    def add_repair(self, ident, bounds, color):
        path = self.inputs / f"repair-{ident}.png"
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        with Image.new("RGB", (width, height), color) as image:
            image.save(path)
        self.evidence["repairs"].append(
            {
                "id": ident,
                "bounds_px": bounds,
                "generation_output": {
                    "path": path.name,
                    "sha256": release.digest(path),
                },
                "crop_box_px": [0, 0, width, height],
                "source_bounds_px": bounds,
                "reference_sha256": ["a" * 64],
            }
        )
        for chunk, record in zip(
            self.index["chunks"], self.evidence["patches"], strict=True
        ):
            core = chunk["bounds_px"]
            intersection = [
                max(bounds[0], core[0]),
                max(bounds[1], core[1]),
                min(bounds[2], core[2]),
                min(bounds[3], core[3]),
            ]
            if intersection[0] >= intersection[2] or intersection[1] >= intersection[3]:
                continue
            output = chunk["outputs"]["styled"]
            final = self.inputs / output["path"]
            with Image.open(final) as image:
                image.paste(
                    color,
                    (
                        intersection[0] - core[0],
                        intersection[1] - core[1],
                        intersection[2] - core[0],
                        intersection[3] - core[1],
                    ),
                )
                image.save(final)
            output.update(sha256=release.digest(final), bytes=final.stat().st_size)
            record["styled_sha256"] = output["sha256"]

    def test_generated_paths_symlinks_and_local_text_are_rejected(self):
        record = self.evidence["patches"][0]
        path = record["generation_output"]["path"]
        record["generation_output"]["path"] = "../private.png"
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.validate()
        record["generation_output"]["path"] = path
        real = self.inputs / path
        real.rename(real.with_suffix(".saved"))
        real.symlink_to(real.with_suffix(".saved"))
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.validate()
        real.unlink()
        real.with_suffix(".saved").rename(real)
        self.evidence["prompt"] = "Input from /private/example/source.png"
        with self.assertRaisesRegex(ValueError, "filesystem paths"):
            self.validate()

    def test_mutation_during_export_preserves_active_manifest(self):
        self.output.mkdir()
        (self.output / ".preview-build").write_text("fixture")
        (self.output / "manifest.json").write_text("previous release")
        original = release.build_pyramid

        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.repo / "LICENSE").write_text("changed during export")
            return result

        with patch.object(release, "build_pyramid", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "Inputs changed"):
                self.build()
        self.assertEqual(
            (self.output / "manifest.json").read_text(), "previous release"
        )

    def test_derived_tile_tamper_cannot_keep_same_release_identity(self):
        first = self.build()
        path = self.version() / "pyramid/styled_files/0/0_0.webp"
        with Image.new("RGBA", (1, 1), "red") as image:
            image.save(path, "WEBP", lossless=True)
        inventory_path = self.version() / "pyramid/pyramid-index.json"
        inventory = json.loads(inventory_path.read_text())
        for record in inventory["assets"]:
            if record["path"] == "styled_files/0/0_0.webp":
                record.update(bytes=path.stat().st_size, sha256=release.digest(path))
        inventory_path.write_text(json.dumps(inventory))
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.verify_release(self.output)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.build()
        self.assertEqual(
            json.loads((self.output / "manifest.json").read_text())["id"], first["id"]
        )

    def test_optional_prompts_preserve_actual_patch_and_repair_instructions(self):
        self.evidence["patches"][0]["prompt"] = "Specific native patch instruction"
        self.add_repair("seam", [1010, 1000, 1040, 1040], "blue")
        self.evidence["repairs"][0]["prompt"] = (
            "Specific native seam repair instruction"
        )
        _, _, evidence, _ = self.validate()
        self.assertEqual(
            evidence["patches"][0]["prompt"], "Specific native patch instruction"
        )
        self.assertEqual(
            evidence["repairs"][0]["prompt"], "Specific native seam repair instruction"
        )

    def test_unreviewed_export_never_creates_or_replaces_active_release(self):
        self.evidence["seam_review"]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "completed visual seam review"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_pyramid_identity_must_bind_the_finalized_native_chunks(self):
        pyramid = {
            "settings": {
                field: self.index[field]
                for field in ("config_sha256", "bounds_px", "layers")
            },
            "sources": sorted(
                [
                    {
                        "id": chunk["id"],
                        "bounds_px": chunk["bounds_px"],
                        "hashes": {
                            layer: chunk["outputs"][layer]["sha256"]
                            for layer in self.index["layers"]
                        },
                    }
                    for chunk in self.index["chunks"]
                ],
                key=lambda chunk: chunk["id"],
            ),
        }
        release.verify_pyramid_sources(pyramid, self.index)
        pyramid["sources"][0]["hashes"]["styled"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source hashes"):
            release.verify_pyramid_sources(pyramid, self.index)
        pyramid["settings"]["config_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "settings"):
            release.verify_pyramid_sources(pyramid, self.index)


if __name__ == "__main__":
    unittest.main()
