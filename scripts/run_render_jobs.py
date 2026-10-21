"""Run a bounded atlas batch through resumable, review-gated render jobs."""

import argparse
import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from atlas import load_config, plan_chunks
from jobs import Ledger, artifact_path, sha256
from render_context import context_assets, verify_context_assets
from render_geometry import finalize, relative_asset


def source_identity(manifest_path, ground_path, terrain_path, context_path=None):
    manifest = json.loads(manifest_path.read_text())
    inputs = {"buildings_manifest": sha256(manifest_path), "building_glbs": {}}
    for tile in manifest["tiles"]:
        name = tile["glb_path"]
        path = relative_asset(manifest_path.parent, name)
        inputs["building_glbs"][name] = sha256(path)
        if "path" in tile:
            inputs.setdefault("building_originals", {})[tile["path"]] = sha256(
                relative_asset(manifest_path.parent, tile["path"])
            )
    if ground_path:
        inputs["ground_manifest"] = sha256(ground_path)
        inputs["ground_image"] = sha256(ground_path.parent / "ground.png")
    if terrain_path:
        inputs["terrain_mesh"] = sha256(terrain_path)
    if context_path:
        _data, assets, _counts = context_assets(context_path)
        verify_context_assets(assets)
        inputs["context_manifest"] = sha256(context_path)
        inputs["context_assets"] = {
            path.relative_to(context_path.parent.resolve()).as_posix(): checksum
            for path, checksum in assets
        }
    return inputs


def renderer_identity():
    root = Path(__file__).resolve().parent
    names = [
        "atlas.py",
        "geo.py",
        "gltf_compat.py",
        "render_atlas.py",
        "render_sample.py",
        "render_geometry.py",
        "render_context.py",
        "render_guides.py",
        "verify_guides.py",
    ]
    return {name: sha256(root / name) for name in names}


def run_blender(config_path, destination, inputs, heartbeat, *, timeout=1800):
    script = Path(__file__).resolve().parent / "render_atlas.py"
    command = [
        "blender",
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(script),
        "--",
        "--config",
        str(config_path),
        "--manifest",
        str(inputs["manifest"]),
        "--output",
        str(destination),
        "--max-chunks",
        "1",
    ]
    for key in ("ground", "terrain", "context"):
        if inputs.get(key):
            command.extend(["--" + key, str(inputs[key])])
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "render.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        try:
            while True:
                try:
                    code = process.wait(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    heartbeat()
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(
                            "Render job exceeded its time limit"
                        ) from None
            if code:
                raise RuntimeError(
                    f"Blender failed with exit code {code}; inspect the job render log"
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    path = destination / "render-index.json"
    finalize(path)
    return path


def run_batch(
    config_path,
    manifest,
    output,
    database,
    workspace,
    *,
    ground=None,
    terrain=None,
    context=None,
    retry_failed=False,
    max_jobs=16,
    renderer_version="unspecified",
    runner=run_blender,
):
    workspace = workspace.resolve()
    output = output.resolve()
    if not output.is_relative_to(workspace):
        raise ValueError("Batch outputs must be inside the workspace")
    if renderer_version == "unspecified":
        raise ValueError("An actual renderer version is required for job identity")
    config = load_config(config_path)
    plan = plan_chunks(config, max_chunks=max_jobs)
    provenance = source_identity(manifest, ground, terrain, context)
    code = renderer_identity()
    output.mkdir(parents=True, exist_ok=True)
    inputs = {
        "manifest": manifest.resolve(),
        "ground": ground.resolve() if ground else None,
        "terrain": terrain.resolve() if terrain else None,
        "context": context.resolve() if context else None,
    }
    ledger = Ledger(database)
    jobs = {}
    try:
        for chunk in plan["chunks"]:
            payload = {
                "config": config.to_dict(),
                "bounds_px": chunk["bounds_px"],
                "source_hashes": provenance,
                "renderer_scripts": code,
                "renderer_version": renderer_version,
            }
            ident = ledger.enqueue("atlas-render", config.release_id, payload)
            jobs[ident] = chunk
            if retry_failed and ledger.get(ident)["state"] == "failed":
                ledger.retry(ident)
        completed, failed = 0, 0
        while job := ledger.claim("atlas-batch", 120, job_ids=jobs):
            ident, token = job["id"], job["lease_token"]
            chunk = jobs[ident]
            directory = output / ident / f"attempt-{job['attempts']}"
            directory.mkdir(parents=True, exist_ok=True)
            chunk_config = replace(config, bounds_px=tuple(chunk["bounds_px"]))
            job_config = directory / "config.json"
            job_config.write_text(json.dumps(chunk_config.to_dict(), indent=2) + "\n")
            try:
                index_path = runner(
                    job_config,
                    directory,
                    inputs,
                    lambda ident=ident, token=token: ledger.heartbeat(
                        ident, token, 120
                    ),
                )
                index = json.loads(index_path.read_text())
                if (
                    index.get("status") != "complete"
                    or len(index["chunks"]) != 1
                    or index["chunks"][0]["bounds_px"] != chunk["bounds_px"]
                ):
                    raise ValueError("Renderer returned an incomplete or wrong chunk")
                paths = [index_path.relative_to(workspace).as_posix()]
                for record in index["chunks"][0]["outputs"].values():
                    path = relative_asset(index_path.parent, record["path"])
                    if (
                        path.stat().st_size != record["bytes"]
                        or sha256(path) != record["sha256"]
                    ):
                        raise ValueError("Rendered chunk checksum mismatch")
                    paths.append(path.relative_to(workspace).as_posix())
                if (
                    source_identity(manifest, ground, terrain, context) != provenance
                    or renderer_identity() != code
                ):
                    raise ValueError(
                        "Source assets or renderer code changed during the batch"
                    )
                ledger.finish(ident, token, workspace, paths)
                completed += 1
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                # Keep failed outputs/logs for inspection, never mark them reviewed.
                try:
                    ledger.fail(ident, token, str(exc))
                except ValueError:
                    current = ledger.get(ident)
                    if (
                        current["state"] == "running"
                        and current["lease_token"] == token
                        and current["lease_until"] > ledger.clock()
                    ):
                        raise
                    # A replacement worker or an invalidation owns the new state.
                failed += 1
        states = {ident: ledger.get(ident)["state"] for ident in jobs}
        ready = all(state in {"needs_review", "approved"} for state in states.values())
        if ready:
            if (
                source_identity(manifest, ground, terrain, context) != provenance
                or renderer_identity() != code
            ):
                raise ValueError(
                    "Source assets or renderer code changed during the batch"
                )
            chunks = []
            layers = None
            for ident in jobs:
                job = ledger.get(ident)
                for record in job["artifacts"]:
                    path = artifact_path(workspace, record["path"])
                    if (
                        path.stat().st_size != record["bytes"]
                        or sha256(path) != record["sha256"]
                    ):
                        raise ValueError(
                            "A previously completed render artifact changed"
                        )
                index_path = artifact_path(workspace, job["artifacts"][0]["path"])
                index = json.loads(index_path.read_text())
                if layers is not None and layers != index["layers"]:
                    raise ValueError("Rendered layers differ between jobs")
                layers = index["layers"]
                chunk = index["chunks"][0]
                for record in chunk["outputs"].values():
                    record["path"] = (
                        relative_asset(index_path.parent, record["path"])
                        .relative_to(output)
                        .as_posix()
                    )
                chunks.append(chunk)
            combined = {
                "schema_version": 1,
                "status": "complete",
                "atlas_id": config.atlas_id,
                "release_id": config.release_id,
                "config_sha256": config.fingerprint,
                "bounds_px": list(config.bounds_px),
                "chunk_size_px": config.chunk_size_px,
                "layers": layers,
                "chunks": chunks,
                "source_coverage_verified": False,
                "artwork_approved": False,
                "ai_styling": False,
                "source_review_complete": all(
                    state == "approved" for state in states.values()
                ),
                "job_ids": list(jobs),
                "source_hashes": provenance,
            }
            temporary = output / "render-index.tmp"
            temporary.write_text(json.dumps(combined, indent=2) + "\n")
            temporary.replace(output / "render-index.json")
        elif (output / "render-index.json").exists():
            # A stale complete index must not stand in for a changed/incomplete batch.
            (output / "render-index.json").rename(
                output / f"incomplete-previous-{time.time_ns()}.json"
            )
        report = {
            "jobs": len(jobs),
            "rendered_this_run": completed,
            "failed_this_run": failed,
            "ready_for_review": ready,
            "states": states,
        }
        (output / "batch-report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        ledger.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/shinjuku.json"))
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/sample/manifest.json")
    )
    parser.add_argument("--ground", type=Path)
    parser.add_argument("--terrain", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path, default=Path("renders/batch-shinjuku"))
    parser.add_argument("--database", type=Path, default=Path(".cache/jobs.sqlite"))
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--max-jobs", type=int, default=16)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    version = subprocess.run(
        ["blender", "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    report = run_batch(
        args.config,
        args.manifest,
        args.output,
        args.database,
        args.workspace,
        ground=args.ground,
        terrain=args.terrain,
        context=args.context,
        retry_failed=args.retry_failed,
        max_jobs=args.max_jobs,
        renderer_version=version,
    )
    print(json.dumps(report, indent=2))
    if not report["ready_for_review"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
