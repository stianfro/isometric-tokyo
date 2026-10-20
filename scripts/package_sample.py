"""Package original samples, derived assets and the reproduction scripts."""

import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile("README.txt", args.output / "README.txt")
    files = [
        Path(name)
        for name in (
            "README.txt",
            "README.md",
            "THIRD_PARTY_NOTICES.md",
            "justfile",
            "ruff.toml",
            "requirements.txt",
            "LICENSE",
        )
        if Path(name).exists()
    ]
    for directory in ("scripts", "tests", "data", "docs", "previews"):
        files.extend(
            path
            for path in Path(directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    archive = args.output / "shinjuku-source-kit.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(files):
            zf.write(path, Path("shinjuku-source-kit") / path)
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise ValueError("Source archive CRC check failed")
    deliverables = sorted(
        path
        for path in args.output.iterdir()
        if path.is_file() and path.suffix != ".blend1" and path.name != "SHA256SUMS.txt"
    )
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in deliverables
    ]
    (args.output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print(
        f"Packaged and CRC-verified {len(files)} files: {archive} ({archive.stat().st_size:,} bytes)"
    )


if __name__ == "__main__":
    main()
