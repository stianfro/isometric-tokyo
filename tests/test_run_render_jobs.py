"""Batch interruption and resume using a deterministic fake renderer."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import load_config
from jobs import Ledger, sha256
from run_render_jobs import run_batch


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        template = Path(__file__).resolve().parents[1] / "configs/shinjuku.json"
        config = json.loads(template.read_text())
        config.update(bounds_px=[0, 0, 16, 8], chunk_size_px=8, overscan_px=0)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(config))
        self.manifest = self.root / "source.json"
        self.manifest.write_text(json.dumps({"tiles": []}))
        self.output = self.root / "renders"
        self.database = self.root / "jobs.sqlite"
        self.calls = 0
        self.fail_next = False

    def render(self, config_path, destination, inputs, heartbeat):
        self.calls += 1
        heartbeat()
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("Synthetic render failure")
        config = load_config(config_path)
        width = config.bounds_px[2] - config.bounds_px[0]
        height = config.bounds_px[3] - config.bounds_px[1]
        path = destination / "textured.png"
        Image.new("RGB", (width, height), "green").save(path)
        index = {
            "schema_version": 1,
            "status": "complete",
            "layers": ["textured"],
            "chunks": [
                {
                    "id": str(config.bounds_px[0]),
                    "bounds_px": list(config.bounds_px),
                    "outputs": {
                        "textured": {
                            "path": "textured.png",
                            "width": width,
                            "height": height,
                            "bytes": path.stat().st_size,
                            "sha256": sha256(path),
                        }
                    },
                }
            ],
        }
        index_path = destination / "render-index.json"
        index_path.write_text(json.dumps(index))
        return index_path

    def run_batch(self, retry=False):
        return run_batch(
            self.config,
            self.manifest,
            self.output,
            self.database,
            self.root,
            retry_failed=retry,
            renderer_version="fixture-1",
            runner=self.render,
        )

    def test_complete_batch_does_not_repeat_work(self):
        result = self.run_batch()
        self.assertTrue(result["ready_for_review"])
        self.assertEqual(self.calls, 2)
        self.assertEqual(set(result["states"].values()), {"needs_review"})
        self.assertEqual(self.run_batch()["rendered_this_run"], 0)
        self.assertEqual(self.calls, 2)
        index = json.loads((self.output / "render-index.json").read_text())
        self.assertEqual(len(index["chunks"]), 2)
        self.assertFalse(index["artwork_approved"])

    def test_failed_job_resume_only_repeats_failure(self):
        self.fail_next = True
        first = self.run_batch()
        self.assertFalse(first["ready_for_review"])
        self.assertEqual(first["failed_this_run"], 1)
        self.assertFalse((self.output / "render-index.json").exists())
        self.assertEqual(self.run_batch()["rendered_this_run"], 0)
        self.assertTrue(self.run_batch(retry=True)["ready_for_review"])
        self.assertEqual(self.calls, 3)

    def test_does_not_claim_unrelated_jobs(self):
        ledger = Ledger(self.database)
        unrelated = ledger.enqueue("unrelated", "elsewhere", {})
        ledger.close()
        self.run_batch()
        ledger = Ledger(self.database)
        self.assertEqual(ledger.get(unrelated)["state"], "pending")
        ledger.close()

    def test_completed_artifact_mutation_is_rejected(self):
        self.run_batch()
        image = next(self.output.rglob("textured.png"))
        image.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_batch()

    def test_mid_render_source_change_is_rejected(self):
        def changing_runner(*args):
            path = self.render(*args)
            self.manifest.write_text(json.dumps({"tiles": [], "changed": True}))
            return path

        report = run_batch(
            self.config,
            self.manifest,
            self.output,
            self.database,
            self.root,
            renderer_version="fixture-1",
            runner=changing_runner,
        )
        self.assertFalse(report["ready_for_review"])
        self.assertEqual(report["failed_this_run"], 2)
        self.assertFalse((self.output / "render-index.json").exists())

    def test_invalidation_revokes_inflight_worker(self):
        def invalidating_runner(*args):
            path = self.render(*args)
            ledger = Ledger(self.database)
            running = next(
                job for job in ledger.list_jobs() if job["state"] == "running"
            )
            ledger.invalidate(running["id"], "Source changed")
            ledger.close()
            return path

        report = run_batch(
            self.config,
            self.manifest,
            self.output,
            self.database,
            self.root,
            renderer_version="fixture-1",
            runner=invalidating_runner,
        )
        self.assertFalse(report["ready_for_review"])
        self.assertEqual(report["failed_this_run"], 2)

    def test_context_identity_and_runner_inputs(self):
        context = self.root / "context.json"
        tile = {"context_type": "tran", "dependencies": []}
        for field, hash_field, name in (
            ("path", "sha256", "road.b3dm"),
            ("glb_path", "glb_sha256", "road.glb"),
            ("tables_path", "tables_sha256", "tables.json"),
        ):
            path = self.root / name
            path.write_bytes(name.encode())
            tile[field], tile[hash_field] = name, sha256(path)
        data = {
            "schema_version": 1,
            "kind": "plateau-context",
            "status": "complete",
            "tiles": [tile],
        }
        context.write_text(json.dumps(data))

        def runner(config, destination, inputs, heartbeat):
            self.assertEqual(inputs["context"], context.resolve())
            return self.render(config, destination, inputs, heartbeat)

        def run():
            return run_batch(
                self.config,
                self.manifest,
                self.output,
                self.database,
                self.root,
                context=context,
                renderer_version="fixture-1",
                runner=runner,
            )

        first = run()
        self.assertTrue(first["ready_for_review"])
        self.assertEqual(run()["rendered_this_run"], 0)
        (self.root / "road.glb").write_bytes(b"new mesh")
        with self.assertRaisesRegex(ValueError, "checksum"):
            run()
        tile["glb_sha256"] = sha256(self.root / "road.glb")
        context.write_text(json.dumps(data))
        updated = run()
        self.assertEqual(updated["rendered_this_run"], 2)
        self.assertFalse(set(first["states"]) & set(updated["states"]))

    def test_changed_sources_create_new_jobs(self):
        first = self.run_batch()
        self.manifest.write_text(json.dumps({"tiles": [], "edition": "new"}))
        second = self.run_batch()
        self.assertEqual(self.calls, 4)
        self.assertFalse(set(first["states"]) & set(second["states"]))


if __name__ == "__main__":
    unittest.main()
