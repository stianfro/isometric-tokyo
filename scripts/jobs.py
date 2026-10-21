"""Resumable local production ledger with leases and explicit review gates.

Artifacts are identified by paths relative to the chosen workspace, never by
machine-specific absolute paths. This ledger does not execute model calls or
approve artwork automatically.
"""

import argparse
import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

STATES = {"pending", "running", "failed", "needs_review", "approved"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def job_id(kind, area, payload, dependencies):
    return hashlib.sha256(
        canonical([kind, area, payload, sorted(dependencies)]).encode()
    ).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def artifact_path(workspace, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Artifact needs a relative path")
    relative = Path(name)
    result = workspace / relative
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not result.resolve().is_relative_to(workspace.resolve())
    ):
        raise ValueError("Artifact escapes workspace")
    return result


class Ledger:
    def __init__(self, path, clock=time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, area TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                lease_token TEXT, lease_until REAL, worker TEXT,
                artifacts TEXT, error TEXT, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dependencies (
                job TEXT NOT NULL REFERENCES jobs(id),
                dependency TEXT NOT NULL REFERENCES jobs(id),
                PRIMARY KEY(job, dependency)
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job TEXT NOT NULL REFERENCES jobs(id),
                action TEXT NOT NULL, at REAL NOT NULL, note TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, ident, action, note=""):
        self.db.execute(
            "INSERT INTO events(job,action,at,note) VALUES(?,?,?,?)",
            (ident, action, self.clock(), note),
        )

    def get(self, ident):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise ValueError("Unknown job")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["artifacts"] = (
            json.loads(result["artifacts"]) if result["artifacts"] else []
        )
        return result

    def enqueue(self, kind, area, payload, dependencies=(), max_attempts=3):
        if not kind or not area or type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("Kind, area and a positive retry limit are required")
        if not isinstance(payload, dict):
            raise ValueError("Job payload must be an object")
        dependencies = sorted(set(dependencies))
        ident = job_id(kind, area, payload, dependencies)
        with self.transaction():
            for dependency in dependencies:
                self.get(dependency)
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO jobs(id,kind,area,payload,state,max_attempts,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    ident,
                    kind,
                    area,
                    canonical(payload),
                    "pending",
                    max_attempts,
                    self.clock(),
                ),
            )
            if cursor.rowcount:
                for dependency in dependencies:
                    self.db.execute(
                        "INSERT INTO dependencies VALUES(?,?)", (ident, dependency)
                    )
                self.event(ident, "enqueued")
            elif self.get(ident)["max_attempts"] != max_attempts:
                raise ValueError("Existing job has a different retry limit")
        return ident

    def claim(self, worker, lease_seconds=300, job_ids=None):
        if not worker or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Worker and positive finite lease duration required")
        now = self.clock()
        with self.transaction():
            expired = self.db.execute(
                "SELECT id,attempts,max_attempts FROM jobs WHERE state='running' AND lease_until<=?",
                (now,),
            ).fetchall()
            for row in expired:
                state = "pending" if row["attempts"] < row["max_attempts"] else "failed"
                self.db.execute(
                    "UPDATE jobs SET state=?,lease_token=NULL,lease_until=NULL,worker=NULL,error='Worker lease expired',updated=? WHERE id=?",
                    (state, now, row["id"]),
                )
                self.event(row["id"], "lease_expired", state)
            scope, parameters = "", []
            if job_ids is not None:
                if not job_ids:
                    return None
                scope = " AND j.id IN (" + ",".join("?" for _ in job_ids) + ")"
                parameters = list(job_ids)
            row = self.db.execute(
                """
                SELECT j.id FROM jobs j
                WHERE j.state='pending' AND j.attempts<j.max_attempts
                AND NOT EXISTS (
                    SELECT 1 FROM dependencies d JOIN jobs p ON p.id=d.dependency
                    WHERE d.job=j.id AND p.state!='approved'
                )
            """
                + scope
                + " ORDER BY j.updated,j.id LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                return None
            ident, token = row["id"], uuid.uuid4().hex
            self.db.execute(
                "UPDATE jobs SET state='running',attempts=attempts+1,lease_token=?,lease_until=?,worker=?,updated=?,error=NULL WHERE id=?",
                (token, now + lease_seconds, worker, now, ident),
            )
            self.event(ident, "claimed", worker)
            return self.get(ident)

    def owned(self, ident, token):
        job = self.get(ident)
        if (
            job["state"] != "running"
            or job["lease_token"] != token
            or job["lease_until"] <= self.clock()
        ):
            raise ValueError("Job lease is stale or not owned by this worker")
        return job

    def heartbeat(self, ident, token, lease_seconds=300):
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Positive finite lease duration required")
        with self.transaction():
            self.owned(ident, token)
            self.db.execute(
                "UPDATE jobs SET lease_until=?,updated=? WHERE id=?",
                (self.clock() + lease_seconds, self.clock(), ident),
            )

    def finish(self, ident, token, workspace, paths):
        if not paths or len(set(paths)) != len(paths):
            raise ValueError("Unique output artifacts are required")
        records = []
        for name in paths:
            path = artifact_path(workspace, name)
            if not path.is_file():
                raise ValueError("Artifact does not exist")
            records.append(
                {"path": name, "sha256": sha256(path), "bytes": path.stat().st_size}
            )
        with self.transaction():
            self.owned(ident, token)
            self.db.execute(
                "UPDATE jobs SET state='needs_review',artifacts=?,lease_token=NULL,lease_until=NULL,worker=NULL,updated=? WHERE id=?",
                (canonical(records), self.clock(), ident),
            )
            self.event(ident, "finished")

    def import_completed(self, kind, area, payload, workspace, paths):
        """Register verified external render outputs for review, never approval."""
        if not paths or len(set(paths)) != len(paths):
            raise ValueError("Unique artifacts are required")
        records = []
        for name in paths:
            path = artifact_path(workspace, name)
            if not path.is_file():
                raise ValueError("Artifact does not exist")
            records.append(
                {"path": name, "sha256": sha256(path), "bytes": path.stat().st_size}
            )
        if not kind or not area or not isinstance(payload, dict):
            raise ValueError("Kind, area and object payload are required")
        inputs = {**payload, "imported_artifacts": records}
        ident = job_id(kind, area, inputs, [])
        with self.transaction():
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO jobs(id,kind,area,payload,state,attempts,max_attempts,artifacts,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    kind,
                    area,
                    canonical(inputs),
                    "needs_review",
                    1,
                    3,
                    canonical(records),
                    self.clock(),
                ),
            )
            if cursor.rowcount:
                self.event(ident, "imported_for_review")
            elif self.get(ident)["artifacts"] != records:
                raise ValueError("Existing job does not match imported artifacts")
        return ident

    def fail(self, ident, token, reason):
        if not reason:
            raise ValueError("Failure reason required")
        with self.transaction():
            self.owned(ident, token)
            self.db.execute(
                "UPDATE jobs SET state='failed',error=?,lease_token=NULL,lease_until=NULL,worker=NULL,updated=? WHERE id=?",
                (reason, self.clock(), ident),
            )
            self.event(ident, "failed", reason)

    def approve(self, ident, workspace, note):
        if not note.strip():
            raise ValueError("Review note required")
        with self.transaction():
            job = self.get(ident)
            if job["state"] != "needs_review":
                raise ValueError("Only completed jobs can be approved")
            for record in job["artifacts"]:
                path = artifact_path(workspace, record["path"])
                if (
                    not path.is_file()
                    or path.stat().st_size != record["bytes"]
                    or sha256(path) != record["sha256"]
                ):
                    raise ValueError("Artifact changed after completion")
            self.db.execute(
                "UPDATE jobs SET state='approved',updated=? WHERE id=?",
                (self.clock(), ident),
            )
            self.event(ident, "approved", note)

    def invalidate(self, ident, reason):
        """Invalidate a region and its dependency closure, preserving audit history."""
        if not reason.strip():
            raise ValueError("Invalidation reason required")
        with self.transaction():
            self.get(ident)
            rows = self.db.execute(
                """
                WITH RECURSIVE affected(id) AS (
                    SELECT ? UNION SELECT d.job FROM dependencies d JOIN affected a ON d.dependency=a.id
                ) SELECT id FROM affected
            """,
                (ident,),
            ).fetchall()
            for row in rows:
                self.db.execute(
                    "UPDATE jobs SET state='failed',lease_token=NULL,lease_until=NULL,worker=NULL,error=?,updated=? WHERE id=?",
                    (reason, self.clock(), row["id"]),
                )
                self.event(row["id"], "invalidated", reason)
            return sorted(row["id"] for row in rows)

    def retry(self, ident):
        with self.transaction():
            job = self.get(ident)
            if job["state"] != "failed" or job["attempts"] >= job["max_attempts"]:
                raise ValueError("Job is not failed or its attempt budget is exhausted")
            self.db.execute(
                "UPDATE jobs SET state='pending',updated=? WHERE id=?",
                (self.clock(), ident),
            )
            self.event(ident, "retried")

    def list_jobs(self):
        return [
            self.get(row["id"])
            for row in self.db.execute("SELECT id FROM jobs ORDER BY updated,id")
        ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".cache/jobs.sqlite"))
    parser.add_argument("--workspace", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument(
        "spec",
        type=Path,
        help="JSON with kind, area, payload and optional dependencies/max_attempts",
    )
    claim = sub.add_parser("claim")
    claim.add_argument("--worker", required=True)
    claim.add_argument("--lease-seconds", type=float, default=300)
    for action in ("finish", "fail", "heartbeat", "approve", "invalidate", "retry"):
        command = sub.add_parser(action)
        command.add_argument("id")
        if action in {"finish", "fail", "heartbeat"}:
            command.add_argument("--token", required=True)
        if action == "finish":
            command.add_argument("paths", nargs="+")
        if action in {"fail", "approve", "invalidate"}:
            command.add_argument("--note", required=True)
        if action == "heartbeat":
            command.add_argument("--lease-seconds", type=float, default=300)
    args = parser.parse_args()
    ledger = Ledger(args.database)
    try:
        if args.action == "list":
            result = ledger.list_jobs()
        elif args.action == "enqueue":
            result = {"id": ledger.enqueue(**json.loads(args.spec.read_text()))}
        elif args.action == "claim":
            result = ledger.claim(args.worker, args.lease_seconds)
        elif args.action == "finish":
            ledger.finish(args.id, args.token, args.workspace, args.paths)
            result = ledger.get(args.id)
        elif args.action == "fail":
            ledger.fail(args.id, args.token, args.note)
            result = ledger.get(args.id)
        elif args.action == "heartbeat":
            ledger.heartbeat(args.id, args.token, args.lease_seconds)
            result = ledger.get(args.id)
        elif args.action == "approve":
            ledger.approve(args.id, args.workspace, args.note)
            result = ledger.get(args.id)
        elif args.action == "invalidate":
            result = {"invalidated": ledger.invalidate(args.id, args.note)}
        else:
            ledger.retry(args.id)
            result = ledger.get(args.id)
        print(json.dumps(result, indent=2))
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
