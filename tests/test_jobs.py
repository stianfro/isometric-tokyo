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
