"""Package a verified preview and prebuilt browser app without private inputs.

Only the browser entry point, generated browser assets, selected preview release
and software licences enter the immutable bundle. No build command is executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

VERSION = 1
INVENTORY = "web-release.json"
MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 100_000
SAFE_PART = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
RELEASE_ID = re.compile(r"^shinjuku-(?:preview|map)-[a-f0-9]{24}$")
APP_ID = re.compile(r"^web-[a-f0-9]{24}$")
ASSET_EXTENSIONS = {
    ".js",
    ".css",
    ".woff",
    ".woff2",
    ".svg",
    ".png",
    ".jpg",
    ".webp",
    ".ico",
}
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def read_json(path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("JSON input exceeds package limit")
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 1000:
        raise ValueError("Invalid package path")
    parts = name.split("/")
    if any(part in {".", ".."} or not SAFE_PART.fullmatch(part) for part in parts):
        raise ValueError("Package paths must be safe relative paths")
    return name


def files_in(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Package input must be a regular directory")
    files, total = {}, 0
    for path in sorted(directory.rglob("*")):
        name = safe_name(path.relative_to(directory).as_posix())
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Symlinks and special files are not package inputs")
        if path.is_file():
            files[name] = path
            total += path.stat().st_size
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise ValueError("Package input exceeds bounded file or byte limit")
    return files


def verify_preview(directory):
    manifest = read_json(Path(directory) / "manifest.json")
    ident = manifest.get("id")
    if isinstance(ident, str) and ident.startswith("shinjuku-map-"):
        from build_map_release import verify_release
    else:
        from build_preview_release import verify_release

    return verify_release(directory)


class EntryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "base":
            raise ValueError("Browser entry point cannot override its URL base")
        if tag == "script" and "src" not in attributes:
            raise ValueError("Only external built scripts are permitted")
        for key in ("src", "href"):
            if key in attributes:
                url = attributes[key]
                if not isinstance(url, str):
                    raise ValueError("Missing browser asset URL")
                name = url[2:] if url.startswith("./") else url
                safe_name(name)
                if not name.startswith("assets/"):
                    raise ValueError("Browser asset URLs must be relative assets paths")
                self.assets.append(name)


def build_files(directory):
    files = files_in(directory)
    if "index.html" not in files:
        raise ValueError("Built browser entry point is missing")
    for name in files:
        if name == "index.html":
            continue
        parts = name.split("/")
        if (
            len(parts) != 2
            or parts[0] != "assets"
            or Path(name).suffix not in ASSET_EXTENSIONS
        ):
            raise ValueError(
                "Unexpected browser build file; rebuild without public directories"
            )
    # Empty unexpected directories are rejected too, not silently ignored.
    for path in Path(directory).rglob("*"):
        if path.is_dir() and path.relative_to(directory).as_posix() != "assets":
            raise ValueError("Unexpected browser build directory")
    parser = EntryParser()
    parser.feed(files["index.html"].read_text())
    parser.close()
    if not parser.assets or any(name not in files for name in parser.assets):
        raise ValueError("Browser entry point refers to missing assets")
    scripts = [path for name, path in files.items() if name.endswith(".js")]
    if not scripts or not any(
        "preview/manifest.json" in path.read_text() for path in scripts
    ):
        raise ValueError("Browser build must select preview/manifest.json")
    return files


def software_licenses(repo_root):
    """Include the installed runtime dependency closure, not development tools."""
    viewer = repo_root / "viewer"
    package = read_json(viewer / "package.json")
    pending = sorted(package.get("dependencies", {}))
    result = {}
    while pending:
        name = pending.pop()
        if name in result:
            continue
        if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
            raise ValueError("Unsafe runtime dependency name")
        root = viewer / "node_modules" / name
        if root.is_symlink() or not root.is_dir():
            raise ValueError(
                "Runtime dependencies must be installed regular directories"
            )
        metadata = read_json(root / "package.json")
        if metadata.get("name") != name or not isinstance(metadata.get("version"), str):
            raise ValueError("Runtime dependency identity mismatch")
        license_paths = [
            root / filename
            for filename in ("LICENSE", "LICENSE.txt", "LICENSE.md")
            if (root / filename).is_file()
        ]
        if len(license_paths) != 1 or license_paths[0].is_symlink():
            raise ValueError("Expected one explicit runtime dependency licence")
        if license_paths[0].stat().st_size > 1_000_000:
            raise ValueError("Runtime dependency licence exceeds limit")
        result[name] = {
            "version": metadata["version"],
            "license": license_paths[0].read_text(),
        }
        dependencies = metadata.get("dependencies", {})
        if not isinstance(dependencies, dict):
            raise ValueError("Invalid runtime dependency metadata")
        # Refuse nested resolution rather than silently documenting a different copy.
        if (root / "node_modules").exists():
            raise ValueError(
                "Nested runtime dependency resolution requires explicit support"
            )
        pending.extend(dependencies)
        if len(result) + len(pending) > 100:
            raise ValueError("Runtime dependency closure exceeds limit")
    if not result:
        raise ValueError("Runtime software licence inventory is empty")
    lines = ["isometric.tokyo browser runtime software licences", ""]
    for name, record in sorted(result.items()):
        lines.extend(
            [f"## {name} {record['version']}", "", record["license"].rstrip(), ""]
        )
    return "\n".join(lines) + "\n"


def records_for(files):
    return [
        {"path": name, "bytes": path.stat().st_size, "sha256": digest(path)}
        for name, path in sorted(files.items())
    ]


def identifier(identity):
    return "web-" + hashlib.sha256(canonical(identity).encode()).hexdigest()[:24]


def verify_package(directory):
    directory = Path(directory)
    files = files_in(directory)
    metadata = read_json(directory / INVENTORY)
    if (
        set(metadata) != {"schema_version", "id", "identity", "assets"}
        or metadata["schema_version"] != VERSION
    ):
        raise ValueError("Unsupported web release inventory")
    identity = metadata["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "packager_version",
        "packager_sha256",
        "preview_release",
        "assets",
    }:
        raise ValueError("Invalid web release identity")
    ident = metadata["id"]
    if (
        not isinstance(ident, str)
        or not APP_ID.fullmatch(ident)
        or identifier(identity) != ident
    ):
        raise ValueError("Web release identity mismatch")
    if identity["packager_version"] != VERSION or not re.fullmatch(
        r"[a-f0-9]{64}", str(identity["packager_sha256"])
    ):
        raise ValueError("Invalid packager identity")
    release_id = identity["preview_release"]
    if not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
        raise ValueError("Invalid selected preview release")
    payload = {name: path for name, path in files.items() if name != INVENTORY}
    records = records_for(payload)
    if records != metadata["assets"] or records != identity["assets"]:
        raise ValueError("Web release asset checksum or inventory mismatch")
    required = {
        "index.html",
        "LICENSE",
        "SOFTWARE_LICENSES.txt",
        "preview/manifest.json",
    }
    if not required.issubset(payload):
        raise ValueError("Web release required files are missing")
    for name in payload:
        if name in required or name.startswith(f"preview/releases/{release_id}/"):
            continue
        if (
            len(name.split("/")) == 2
            and name.startswith("assets/")
            and Path(name).suffix in ASSET_EXTENSIONS
        ):
            continue
        raise ValueError("Unexpected web release file")
    preview = verify_preview(directory / "preview")
    if preview["id"] != release_id:
        raise ValueError("Selected preview release disagrees with bundle")
    return {
        "id": ident,
        "preview_id": release_id,
        "assets": len(files),
        "bytes": sum(path.stat().st_size for path in files.values()),
    }


def archive_package(directory):
    """Stable ZIP bytes: sorted files, fixed timestamps, no compression versions."""
    directory = Path(directory)
    verify_package(directory)
    destination = directory.with_suffix(".zip")
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError("Archive must be a regular file")
    with tempfile.NamedTemporaryFile(
        prefix=".web-archive-", suffix=".zip", dir=directory.parent, delete=False
    ) as stream:
        staging = Path(stream.name)
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, path in files_in(directory).items():
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, path.read_bytes())
        verify_package(directory)
        checksum = digest(staging)
        try:
            # Both files are on the same filesystem. Unlike rename, link cannot
            # overwrite a concurrently published immutable archive.
            destination.hardlink_to(staging)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("Archive must be a regular file") from None
            if digest(destination) != checksum:
                raise ValueError("Existing immutable archive is corrupt") from None
        return {
            "archive": str(destination.resolve()),
            "archive_sha256": checksum,
            "archive_bytes": destination.stat().st_size,
        }
    finally:
        staging.unlink(missing_ok=True)


def package_release(build, preview, output, repo_root=ROOT):
    build, preview, output, repo_root = map(Path, (build, preview, output, repo_root))
    browser = build_files(build)
    # Reject symlinks even in nonselected prior release directories.
    files_in(preview)
    checked = verify_preview(preview)
    release_id = checked["id"]
    if not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
        raise ValueError("Invalid selected preview release")
    source_files = dict(browser)
    source_files["preview/manifest.json"] = preview / "manifest.json"
    source_files.update(
        {
            f"preview/releases/{release_id}/{name}": path
            for name, path in files_in(preview / "releases" / release_id).items()
        }
    )
    license_path = repo_root / "LICENSE"
    if license_path.is_symlink() or not license_path.is_file():
        raise ValueError("Project licence must be a regular file")
    source_files["LICENSE"] = license_path
    source_records = records_for(source_files)
    licenses = software_licenses(repo_root).encode()
    records = sorted(
        [
            *source_records,
            {
                "path": "SOFTWARE_LICENSES.txt",
                "bytes": len(licenses),
                "sha256": hashlib.sha256(licenses).hexdigest(),
            },
        ],
        key=lambda record: record["path"],
    )
    if (
        len(records) > MAX_FILES
        or sum(record["bytes"] for record in records) > MAX_BYTES
    ):
        raise ValueError("Combined web release exceeds bounded package limit")
    identity = {
        "packager_version": VERSION,
        "packager_sha256": digest(Path(__file__)),
        "preview_release": release_id,
        "assets": records,
    }
    ident = identifier(identity)
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError("Web release output must be a regular directory")
    for source in (build, preview, repo_root / "viewer" / "node_modules"):
        if output.resolve().is_relative_to(source.resolve()):
            raise ValueError("Web release output must not be inside package inputs")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ident
    if destination.exists() or destination.is_symlink():
        result = verify_package(destination)
        if result["id"] != ident:
            raise ValueError("Existing immutable web release identity mismatch")
        return (
            result
            | {"directory": str(destination.resolve())}
            | archive_package(destination)
        )
    staging = Path(tempfile.mkdtemp(prefix=".web-stage-", dir=output))
    try:
        for name, path in source_files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (staging / "SOFTWARE_LICENSES.txt").write_bytes(licenses)
        (staging / INVENTORY).write_text(
            json.dumps(
                {
                    "schema_version": VERSION,
                    "id": ident,
                    "identity": identity,
                    "assets": records,
                },
                indent=2,
            )
            + "\n"
        )
        result = verify_package(staging)
        if source_records != records_for(source_files):
            raise ValueError("Package source changed during construction")
        verify_preview(preview)
        if destination.exists():
            raise ValueError("Immutable package appeared during construction; retry")
        staging.rename(destination)
        return (
            result
            | {"directory": str(destination.resolve())}
            | archive_package(destination)
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("viewer/dist"))
    parser.add_argument("--preview", type=Path, default=Path("viewer/public/preview"))
    parser.add_argument("--output", type=Path, default=Path("renders/web-releases"))
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = (
        verify_package(args.verify)
        if args.verify
        else package_release(args.build, args.preview, args.output)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
