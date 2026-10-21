"""Static browser bundles include only verified public assets and licences."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_preview
from package_preview import digest, package_release, verify_package


class PreviewPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.build = self.repo / "viewer/dist"
        self.build.mkdir(parents=True)
        (self.build / "assets").mkdir()
        (self.build / "index.html").write_text(
            '<!doctype html><html><head><script type="module" src="./assets/app.js"></script><link rel="stylesheet" href="./assets/app.css"></head><body></body></html>'
        )
        (self.build / "assets/app.js").write_text(
            'const manifest = "preview/manifest.json";'
        )
        (self.build / "assets/app.css").write_text("body { color: black; }")
        (self.repo / "LICENSE").write_text("Original project MIT licence fixture\n")
        (self.repo / "viewer/package.json").write_text(
            json.dumps({"dependencies": {"react-dom": "1.0", "openseadragon": "1.0"}})
        )
        for name, dependencies in (
            ("react-dom", {"react": "1.0", "scheduler": "1.0"}),
            ("react", {}),
            ("scheduler", {}),
            ("openseadragon", {}),
        ):
            folder = self.repo / "viewer/node_modules" / name
            folder.mkdir(parents=True)
            (folder / "package.json").write_text(
                json.dumps(
                    {"name": name, "version": "1.0", "dependencies": dependencies}
                )
            )
            (folder / "LICENSE").write_text(f"{name} copyright and licence fixture\n")
        self.preview = self.repo / "viewer/public/preview"
        self.release_id = "shinjuku-preview-" + "a" * 24
        selected = self.preview / "releases" / self.release_id
        selected.mkdir(parents=True)
        (selected / "pixels.webp").write_bytes(b"synthetic immutable pixels")
        (selected / "NOTICE.txt").write_text("Synthetic preview attribution\n")
        (selected / "assets.json").write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "path": "pixels.webp",
                            "sha256": digest(selected / "pixels.webp"),
                        },
                        {
                            "path": "NOTICE.txt",
                            "sha256": digest(selected / "NOTICE.txt"),
                        },
                    ]
                }
            )
        )
        (self.preview / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": self.release_id,
                    "views": [
                        {"manifest": f"releases/{self.release_id}/source/manifest.json"}
                    ],
                }
            )
        )
        self.output = self.root / "releases"
        verifier = patch(
            "package_preview.verify_preview", side_effect=self.verify_fixture
        )
        verifier.start()
        self.addCleanup(verifier.stop)

    def verify_fixture(self, directory):
        """Contract stub: real preview validation is covered by its own tests."""
        catalog = json.loads((Path(directory) / "manifest.json").read_text())
        ident = catalog["id"]
        release = Path(directory) / "releases" / ident
        for record in json.loads((release / "assets.json").read_text())["assets"]:
            if digest(release / record["path"]) != record["sha256"]:
                raise ValueError("Preview checksum mismatch")
        return {"id": ident, "assets": 3, "tiles": 1, "bytes": 100}

    def package(self):
        return package_release(self.build, self.preview, self.output, self.repo)

    def test_reproducible_immutable_bundle_and_archive(self):
        first = self.package()
        second = self.package()
        self.assertEqual(first, second)
        directory = Path(first["directory"])
        self.assertEqual(verify_package(directory)["id"], first["id"])
        self.assertEqual(digest(Path(first["archive"])), first["archive_sha256"])
        with zipfile.ZipFile(first["archive"]) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertIn("index.html", names)
            self.assertIn("web-release.json", names)
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(
                    archive.read(info), (directory / info.filename).read_bytes()
                )
        metadata = json.loads((directory / "web-release.json").read_text())
        self.assertNotIn(str(self.root), json.dumps(metadata))
        self.assertEqual(len(metadata["assets"]) + 1, first["assets"])
        self.assertEqual(metadata["assets"], metadata["identity"]["assets"])

    def test_only_selected_preview_and_runtime_licences_are_copied(self):
        old = self.preview / "releases" / ("shinjuku-preview-" + "b" * 24)
        old.mkdir()
        (old / "old.txt").write_text("old release")
        first = self.package()
        directory = Path(first["directory"])
        self.assertEqual(
            [path.name for path in (directory / "preview/releases").iterdir()],
            [self.release_id],
        )
        licences = (directory / "SOFTWARE_LICENSES.txt").read_text()
        for name in ("react", "react-dom", "scheduler", "openseadragon"):
            self.assertIn(f"## {name} 1.0", licences)
            self.assertIn(f"{name} copyright", licences)
        self.assertFalse((directory / "node_modules").exists())
        source = self.preview / "releases" / self.release_id / "pixels.webp"
        copied = directory / "preview/releases" / self.release_id / "pixels.webp"
        self.assertEqual(source.read_bytes(), copied.read_bytes())

    def test_unexpected_build_files_and_directories_are_rejected(self):
        for name in (
            "secret.txt",
            "assets/app.js.map",
            "assets/config.json",
            "public/private.txt",
        ):
            with self.subTest(name=name):
                path = self.build / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("not a build input")
                with self.assertRaisesRegex(ValueError, "Unexpected"):
                    self.package()
                path.unlink()
                if path.parent.name == "public":
                    path.parent.rmdir()
        (self.build / "atlas").mkdir()
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_browser_urls_must_be_safe_relative_assets(self):
        original = (self.build / "index.html").read_text()
        for url in (
            "/assets/app.js",
            "https://example.org/app.js",
            "../assets/app.js",
            "assets/../app.js",
            "./assets/app.js?x=1",
            "assets/missing.js",
            "assets\\app.js",
        ):
            with self.subTest(url=url):
                (self.build / "index.html").write_text(
                    original.replace("./assets/app.js", url)
                )
                with self.assertRaises(ValueError):
                    self.package()
        (self.build / "index.html").write_text(original + '<base href="./assets/">')
        with self.assertRaisesRegex(ValueError, "base"):
            self.package()

    def test_wrong_manifest_build_and_inline_script_are_rejected(self):
        (self.build / "assets/app.js").write_text(
            'const manifest = "atlas/manifest.json";'
        )
        with self.assertRaisesRegex(ValueError, "preview/manifest.json"):
            self.package()
        (self.build / "assets/app.js").write_text(
            'const manifest = "preview/manifest.json";'
        )
        (self.build / "index.html").write_text("<script>privateFunction()</script>")
        with self.assertRaisesRegex(ValueError, "external built scripts"):
            self.package()

    def test_input_symlinks_are_rejected(self):
        (self.build / "assets/linked.js").symlink_to(self.build / "assets/app.js")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.package()
        (self.build / "assets/linked.js").unlink()
        (self.preview / "old-link").symlink_to(self.build, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.package()

    def test_corrupt_preview_does_not_create_bundle(self):
        (self.preview / "releases" / self.release_id / "pixels.webp").write_bytes(
            b"modified"
        )
        with self.assertRaisesRegex(ValueError, "Preview checksum"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_corrupt_existing_bundle_is_not_overwritten(self):
        first = self.package()
        path = Path(first["directory"]) / "index.html"
        path.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.package()
        self.assertEqual(path.read_text(), "tampered")

    def test_unexpected_bundle_file_is_rejected(self):
        first = self.package()
        (Path(first["directory"]) / "private.txt").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "inventory"):
            verify_package(first["directory"])

    def test_corrupt_archive_is_not_overwritten(self):
        first = self.package()
        archive = Path(first["archive"])
        archive.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "archive is corrupt"):
            self.package()
        self.assertEqual(archive.read_bytes(), b"corrupt")

    def test_concurrent_archive_creation_is_not_overwritten(self):
        first = self.package()
        archive = Path(first["archive"])
        archive.unlink()
        link = Path.hardlink_to

        def concurrent(destination, source):
            destination.write_bytes(b"concurrent file")
            return link(destination, source)

        with patch("package_preview.Path.hardlink_to", new=concurrent):
            with self.assertRaisesRegex(ValueError, "archive is corrupt"):
                package_preview.archive_package(first["directory"])
        self.assertEqual(archive.read_bytes(), b"concurrent file")

    def test_bundle_mutation_during_archive_aborts_publication(self):
        first = self.package()
        archive = Path(first["archive"])
        archive.unlink()
        write = zipfile.ZipFile.writestr

        def mutate(archive_file, entry, content):
            result = write(archive_file, entry, content)
            (Path(first["directory"]) / "index.html").write_text(
                "changed during archive"
            )
            return result

        with patch("package_preview.zipfile.ZipFile.writestr", new=mutate):
            with self.assertRaisesRegex(ValueError, "checksum"):
                package_preview.archive_package(first["directory"])
        self.assertFalse(archive.exists())

    def test_asset_or_licence_change_creates_new_release(self):
        first = self.package()
        (self.build / "assets/app.css").write_text("body { color: blue; }")
        second = self.package()
        self.assertNotEqual(first["id"], second["id"])
        (self.repo / "viewer/node_modules/react/LICENSE").write_text(
            "Updated copyright fixture\n"
        )
        third = self.package()
        self.assertNotEqual(second["id"], third["id"])
        verify_package(first["directory"])
        verify_package(second["directory"])

    def test_missing_runtime_licence_or_nested_dependency_is_rejected(self):
        license_path = self.repo / "viewer/node_modules/react/LICENSE"
        license_path.unlink()
        with self.assertRaisesRegex(ValueError, "explicit runtime dependency licence"):
            self.package()
        license_path.write_text("fixture licence")
        (license_path.parent / "node_modules").mkdir()
        with self.assertRaisesRegex(ValueError, "Nested runtime"):
            self.package()

    def test_source_mutation_during_copy_aborts_publication(self):
        copy = package_preview.shutil.copyfile
        changed = False

        def mutate(source, destination):
            nonlocal changed
            result = copy(source, destination)
            if not changed:
                changed = True
                (self.build / "assets/app.css").write_text("changed mid-copy")
            return result

        with patch("package_preview.shutil.copyfile", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "checksum|changed during"):
                self.package()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_cannot_be_inside_build_or_preview(self):
        for output in (self.build / "releases", self.preview / "releases2"):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "inside package inputs"):
                    package_release(self.build, self.preview, output, self.repo)

    def test_duplicate_inventory_json_fields_are_rejected(self):
        first = self.package()
        inventory = Path(first["directory"]) / "web-release.json"
        inventory.write_text('{"id":"one","id":"two"}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            verify_package(first["directory"])


if __name__ == "__main__":
    unittest.main()
