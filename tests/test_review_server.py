"""Review state transitions, image integrity and restricted asset access."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jobs import Ledger, sha256
from review_server import ReviewHandler, ReviewService, import_index


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "jobs.sqlite"
        image = self.root / "tile.png"
        Image.new("RGB", (8, 8), "navy").save(image)
        self.index = self.root / "render-index.json"
        record = {
            "path": "tile.png",
            "width": 8,
            "height": 8,
            "bytes": image.stat().st_size,
            "sha256": sha256(image),
        }
        self.index.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "complete",
                    "config_sha256": "a" * 64,
                    "bounds_px": [0, 0, 8, 8],
                    "layers": ["textured"],
                    "chunks": [
                        {
                            "id": "x0_y0",
                            "bounds_px": [0, 0, 8, 8],
                            "outputs": {"textured": record},
                        }
                    ],
                }
            )
        )
        self.service = ReviewService(self.db, self.root)

    def imported(self):
        return import_index(self.index, self.db, self.root)["jobs"][0]

    def test_import_is_idempotent_and_not_approved(self):
        ident = self.imported()
        self.assertEqual(ident, self.imported())
        job = self.service.jobs()["jobs"][0]
        self.assertEqual(job["state"], "needs_review")
        self.assertNotIn("lease_token", job)
        self.assertFalse(job["payload"]["source_coverage_verified"])
        self.assertEqual(job["artifacts"][0]["url"], f"/api/artifacts/{ident}/0")

    def test_review_note_approval_rejection_and_retry(self):
        ident = self.imported()
        with self.assertRaises(ValueError):
            self.service.mutate(ident, "approve", {"note": " "})
        self.assertEqual(
            self.service.mutate(ident, "approve", {"note": "Source checked"})["job"][
                "state"
            ],
            "approved",
        )
        self.assertEqual(
            self.service.mutate(ident, "reject", {"note": "Boundary defect"})["job"][
                "state"
            ],
            "failed",
        )
        self.assertEqual(
            self.service.mutate(ident, "retry", {})["job"]["state"], "pending"
        )
        with self.assertRaises(ValueError):
            self.service.mutate(
                ident, "approve", {"note": "Cannot approve queued work"}
            )

    def test_artifacts_verified_before_display_or_approval(self):
        ident = self.imported()
        blob, content_type = self.service.artifact(ident, 0)
        self.assertEqual(content_type, "image/png")
        self.assertTrue(blob.startswith(b"\x89PNG"))
        (self.root / "tile.png").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.service.artifact(ident, 0)
        with self.assertRaises(ValueError):
            self.service.mutate(ident, "approve", {"note": "stale"})

    def test_import_rejects_corruption_without_partial_jobs(self):
        data = json.loads(self.index.read_text())
        data["chunks"][0]["outputs"]["textured"]["sha256"] = "b" * 64
        self.index.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.imported()
        self.assertEqual(self.service.jobs()["jobs"], [])

    def test_import_rejects_wrong_core_dimensions(self):
        data = json.loads(self.index.read_text())
        data["bounds_px"] = [0, 0, 16, 16]
        data["chunks"][0]["bounds_px"] = [0, 0, 16, 16]
        self.index.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "chunk bounds"):
            self.imported()
        self.assertEqual(self.service.jobs()["jobs"], [])

    def test_import_does_not_reset_rejection(self):
        ident = self.imported()
        self.service.mutate(ident, "reject", {"note": "Needs repair"})
        self.assertEqual(ident, self.imported())
        self.assertEqual(self.service.jobs()["jobs"][0]["state"], "failed")

    def test_nonimage_not_served(self):
        (self.root / "example.html").write_text("<script>bad()</script>")
        ledger = Ledger(self.db)
        try:
            ident = ledger.import_completed(
                "test", "fixture", {}, self.root, ["example.html"]
            )
        finally:
            ledger.close()
        with self.assertRaises(OSError):
            self.service.artifact(ident, 0)

    def test_host_origin_and_token_guards_without_socket(self):
        handler = object.__new__(ReviewHandler)
        handler.server = SimpleNamespace(server_port=8910, token="secret")
        handler.send = lambda status, body: None
        handler.headers = {"Host": "127.0.0.1:8910", "Authorization": "Bearer secret"}
        self.assertTrue(handler.allowed(api=True))
        handler.headers["Origin"] = "https://unrelated.example"
        self.assertFalse(handler.allowed(api=True))
        del handler.headers["Origin"]
        handler.headers["Host"] = "unrelated.example:8910"
        self.assertFalse(handler.allowed(api=True))
        handler.headers["Host"] = "127.0.0.1:8910"
        handler.headers["Authorization"] = "Bearer wrong"
        self.assertFalse(handler.allowed(api=True))

    def test_http_mutation_requires_json_and_bounded_body(self):
        handler = object.__new__(ReviewHandler)
        handler.server = SimpleNamespace(
            server_port=8910, token="secret", service=self.service
        )
        results = []
        handler.send = lambda status, body: results.append((status, body))
        handler.path = "/api/jobs/" + "a" * 64 + "/approve"
        handler.rfile = io.BytesIO(b"{}")
        handler.headers = {
            "Host": "127.0.0.1:8910",
            "Authorization": "Bearer secret",
            "Content-Length": "9000",
            "Content-Type": "application/json",
        }
        handler.do_POST()
        self.assertEqual(results[-1][0], 400)
        handler.headers["Content-Length"] = "2"
        handler.headers["Content-Type"] = "text/plain"
        handler.do_POST()
        self.assertEqual(results[-1][0], 400)


if __name__ == "__main__":
    unittest.main()
