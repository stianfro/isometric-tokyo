"""Worker interruption, stale completion, dependencies and bounded retries."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jobs import Ledger, artifact_path


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.ledger = Ledger(self.root / "jobs.sqlite", clock=lambda: self.now)
        self.addCleanup(self.ledger.close)
        (self.root / "tile.png").write_bytes(b"example")

    def enqueue(self, name="render", dependencies=(), max_attempts=3):
        return self.ledger.enqueue(
            name, "fixture", {"config_hash": "test"}, dependencies, max_attempts
        )

    def complete(self, job):
        self.ledger.finish(job["id"], job["lease_token"], self.root, ["tile.png"])
        self.ledger.approve(job["id"], self.root, "Fixture bytes checked")

    def test_idempotent_enqueue_and_dependencies(self):
        parent = self.enqueue()
        self.assertEqual(parent, self.enqueue())
        child = self.enqueue("tiles", [parent])
        job = self.ledger.claim("a")
        self.assertEqual(job["id"], parent)
        self.assertIsNone(self.ledger.claim("b"))
        self.complete(job)
        self.assertEqual(self.ledger.claim("b")["id"], child)

    def test_expiry_stale_finish_and_recovery(self):
        ident = self.enqueue()
        old = self.ledger.claim("a", 10)
        self.now += 11
        replacement = self.ledger.claim("b", 10)
        self.assertEqual(replacement["id"], ident)
        self.assertEqual(replacement["attempts"], 2)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.complete(old)
        self.complete(replacement)
        self.assertIsNone(self.ledger.claim("c"))

    def test_heartbeat_and_attempt_cap(self):
        ident = self.enqueue(max_attempts=1)
        job = self.ledger.claim("a", 10)
        self.now += 9
        self.ledger.heartbeat(ident, job["lease_token"], 10)
        self.now += 2
        self.assertIsNone(self.ledger.claim("b"))
        self.now += 10
        self.assertIsNone(self.ledger.claim("b"))
        self.assertEqual(self.ledger.get(ident)["state"], "failed")
        with self.assertRaises(ValueError):
            self.ledger.retry(ident)

    def test_failure_retry_and_approval_integrity(self):
        ident = self.enqueue()
        job = self.ledger.claim("a")
        self.ledger.fail(ident, job["lease_token"], "Fixture failure")
        self.ledger.retry(ident)
        job = self.ledger.claim("a")
        self.ledger.finish(ident, job["lease_token"], self.root, ["tile.png"])
        (self.root / "tile.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.ledger.approve(ident, self.root, "Reviewed")
        self.assertEqual(self.ledger.get(ident)["state"], "needs_review")

    def test_invalidation_only_dependency_closure(self):
        parent = self.enqueue()
        child = self.enqueue("style", [parent])
        grandchild = self.enqueue("tiles", [child])
        unrelated = self.enqueue("other")
        affected = self.ledger.invalidate(parent, "Source correction")
        self.assertEqual(set(affected), {parent, child, grandchild})
        self.assertEqual(self.ledger.get(unrelated)["state"], "pending")

    def test_database_reopen_preserves_jobs(self):
        ident = self.enqueue()
        second = Ledger(self.root / "jobs.sqlite", clock=lambda: self.now)
        try:
            self.assertEqual(second.claim("other")["id"], ident)
            self.assertIsNone(self.ledger.claim("original"))
        finally:
            second.close()

    def test_import_repair_atomically_invalidates_only_old_descendants(self):
        source = self.enqueue()
        self.complete(self.ledger.claim("source"))
        old = self.enqueue("old-tiles", [source])
        worker = self.ledger.claim("old-worker")
        dependent = self.enqueue("old-preview", [old])
        unrelated = self.enqueue("unrelated")
        (self.root / "repair.png").write_bytes(b"new image")
        args = (
            "repair",
            "fixture",
            {"note": "Correct join"},
            self.root,
            ["repair.png"],
        )
        repair = self.ledger.import_completed(
            *args,
            dependencies=[source],
            invalidate_dependents_of=[source],
            invalidation_note="Replaced image",
        )
        self.assertEqual(self.ledger.get(source)["state"], "approved")
        self.assertEqual(self.ledger.get(repair)["state"], "needs_review")
        self.assertEqual(self.ledger.get(unrelated)["state"], "pending")
        for ident in (old, dependent):
            self.assertEqual(self.ledger.get(ident)["state"], "failed")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.ledger.finish(old, worker["lease_token"], self.root, ["tile.png"])
        self.ledger.approve(repair, self.root, "Replacement inspected")
        rebuilt = self.enqueue("new-tiles", [repair])
        self.assertEqual(
            repair,
            self.ledger.import_completed(
                *args,
                dependencies=[source],
                invalidate_dependents_of=[source],
                invalidation_note="Replaced image",
            ),
        )
        self.assertEqual(self.ledger.get(rebuilt)["state"], "pending")

    def test_import_dependencies_and_approval_integrity(self):
        source = self.ledger.import_completed(
            "source", "fixture", {}, self.root, ["tile.png"]
        )
        (self.root / "repair.png").write_bytes(b"repair")
        repair = self.ledger.import_completed(
            "repair", "fixture", {}, self.root, ["repair.png"], dependencies=[source]
        )
        with self.assertRaisesRegex(ValueError, "dependencies"):
            self.ledger.approve(repair, self.root, "Inspected")
        self.ledger.approve(source, self.root, "Source inspected")
        (self.root / "tile.png").write_bytes(b"mutated dependency")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.ledger.approve(repair, self.root, "Inspected")
        self.assertEqual(self.ledger.get(repair)["state"], "needs_review")

    def test_import_bad_dependencies_roll_back(self):
        for kwargs in (
            {"dependencies": ["missing"]},
            {"invalidate_dependents_of": ["missing"], "invalidation_note": "Changed"},
        ):
            with self.assertRaises(ValueError):
                self.ledger.import_completed(
                    "repair", "fixture", {}, self.root, ["tile.png"], **kwargs
                )
        self.assertEqual(self.ledger.list_jobs(), [])

    def test_input_validation_and_path_escape(self):
        with self.assertRaises(ValueError):
            self.ledger.enqueue("render", "sample", {"bad": float("nan")})
        with self.assertRaises(ValueError):
            self.enqueue(dependencies=["missing"])
        for path in ("../escape", "/absolute", "..\\escape"):
            with self.assertRaises(ValueError):
                artifact_path(self.root, path)
        with self.assertRaises(ValueError):
            self.ledger.claim("worker", float("inf"))
        self.assertEqual(self.ledger.list_jobs(), [])


if __name__ == "__main__":
    unittest.main()
