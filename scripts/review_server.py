"""Local, token-protected review workbench for verified renderer artifacts."""

import argparse
import hmac
import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from jobs import Ledger, artifact_path, sha256
from PIL import Image
from render_geometry import relative_asset, validate_partition

UI_ROOT = Path(__file__).resolve().parents[1] / "review"
IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
PUBLIC_FIELDS = (
    "id",
    "kind",
    "area",
    "state",
    "attempts",
    "max_attempts",
    "error",
    "payload",
)


def import_index(index_path, database, workspace):
    workspace = workspace.resolve()
    index_path = index_path.resolve()
    if not index_path.is_relative_to(workspace):
        raise ValueError("Render index must be inside the workspace")
    index = json.loads(index_path.read_text())
    if index.get("schema_version") != 1 or index.get("status") != "complete":
        raise ValueError("Only a complete finalized render index can be reviewed")
    if not re.fullmatch(r"[a-f0-9]{64}", index.get("config_sha256", "")):
        raise ValueError("Render index has no valid configuration hash")
    validate_partition(index["bounds_px"], index["chunks"])
    layers = index["layers"]
    if not layers or len(set(layers)) != len(layers):
        raise ValueError("Render layers must be unique and nonempty")
    checked = []
    # Verify every artifact before making any ledger changes.
    for chunk in index["chunks"]:
        paths = []
        records = [chunk["outputs"][layer] for layer in layers] + list(
            chunk.get("guides", {}).values()
        )
        for record in records:
            left, top, right, bottom = chunk["bounds_px"]
            if (record["width"], record["height"]) != (right - left, bottom - top):
                raise ValueError("Render image dimensions disagree with chunk bounds")
            path = relative_asset(index_path.parent, record["path"])
            if not path.is_relative_to(workspace):
                raise ValueError("Render artifact escapes workspace")
            if (
                path.stat().st_size != record["bytes"]
                or sha256(path) != record["sha256"]
            ):
                raise ValueError("Render artifact checksum mismatch")
            if record.get("dtype") is not None:
                components = record.get("components")
                if (
                    record["dtype"] != "float32-little-endian"
                    or components not in (1, 3)
                    or record["bytes"]
                    != record["width"] * record["height"] * components * 4
                ):
                    raise ValueError("Unsupported numeric guide format")
            else:
                with Image.open(path) as image:
                    if (
                        image.size != (record["width"], record["height"])
                        or image.format not in IMAGE_TYPES
                    ):
                        raise ValueError(
                            "Unsupported render artifact dimensions or format"
                        )
                    image.verify()
            paths.append(path.relative_to(workspace).as_posix())
        checked.append((chunk, paths))
    ledger = Ledger(database)
    try:
        ids = []
        for chunk, paths in checked:
            payload = {
                "config_sha256": index["config_sha256"],
                "chunk_id": chunk["id"],
                "bounds_px": chunk["bounds_px"],
                "layers": layers,
                "guide_metadata": index.get("guide_metadata"),
                "source_coverage_verified": index.get(
                    "source_coverage_verified", False
                ),
                "render_index_sha256": sha256(index_path),
                "review_scope": "Source geometry and chunk joins, not final artwork approval",
            }
            ids.append(
                ledger.import_completed(
                    "source-render",
                    index.get("release_id", "source-atlas"),
                    payload,
                    workspace,
                    paths,
                )
            )
        return {"imported": len(ids), "jobs": ids}
    finally:
        ledger.close()


class ReviewService:
    def __init__(self, database, workspace):
        self.database = database
        self.workspace = workspace.resolve()

    @staticmethod
    def public_job(job):
        result = {field: job[field] for field in PUBLIC_FIELDS}
        result["artifacts"] = [
            {**record, "url": f"/api/artifacts/{job['id']}/{index}"}
            for index, record in enumerate(job["artifacts"])
        ]
        return result

    def jobs(self):
        ledger = Ledger(self.database)
        try:
            return {"jobs": [self.public_job(job) for job in ledger.list_jobs()]}
        finally:
            ledger.close()

    def mutate(self, ident, action, data):
        if not isinstance(data, dict):
            raise ValueError("Request must be a JSON object")
        note = data.get("note", "")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Review note must be at most 2000 characters")
        ledger = Ledger(self.database)
        try:
            if action == "approve":
                ledger.approve(ident, self.workspace, note)
            elif action == "reject":
                if ledger.get(ident)["state"] not in {"needs_review", "approved"}:
                    raise ValueError("Only completed or approved work can be rejected")
                ledger.invalidate(ident, note)
            elif action == "retry":
                ledger.retry(ident)
            else:
                raise ValueError("Unknown review action")
            return {"job": self.public_job(ledger.get(ident))}
        finally:
            ledger.close()

    def artifact(self, ident, index):
        ledger = Ledger(self.database)
        try:
            records = ledger.get(ident)["artifacts"]
            if index < 0 or index >= len(records):
                raise ValueError("Unknown artifact")
            record = records[index]
            path = artifact_path(self.workspace, record["path"])
            if (
                path.stat().st_size != record["bytes"]
                or sha256(path) != record["sha256"]
            ):
                raise ValueError("Artifact changed after completion")
            with Image.open(path) as image:
                content_type = IMAGE_TYPES.get(image.format)
                if content_type is None:
                    raise ValueError("Artifact is not a supported review image")
                image.verify()
            return path.read_bytes(), content_type
        finally:
            ledger.close()


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "AtlasReview/1"

    def log_message(self, *_args):
        # Do not persist authorization tokens or filesystem data in access logs.
        pass

    def send(self, status, body, content_type="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, *, api=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            self.send(403, {"error": "Unexpected request host"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{expected}":
            self.send(403, {"error": "Cross-origin requests are not allowed"})
            return False
        if api and not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.token
        ):
            self.send(403, {"error": "Review authorization required"})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(api=path.startswith("/api/")):
            return
        try:
            if path == "/api/jobs":
                self.send(200, self.server.service.jobs())
            elif match := re.fullmatch(r"/api/artifacts/([a-f0-9]{64})/(\d+)", path):
                blob, content_type = self.server.service.artifact(
                    match[1], int(match[2])
                )
                self.send(200, blob, content_type)
            elif path in {
                "/",
                "/index.html",
                "/review.js",
                "/review.css",
                "/model.mjs",
            }:
                name = "index.html" if path == "/" else path[1:]
                blob = (UI_ROOT / name).read_bytes()
                if name == "index.html":
                    blob = blob.replace(b"{{REVIEW_TOKEN}}", self.server.token.encode())
                content_type = (
                    mimetypes.guess_type(name)[0] or "application/octet-stream"
                )
                self.send(200, blob, content_type + "; charset=utf-8")
            else:
                self.send(404, {"error": "Not found"})
        except (ValueError, KeyError, IndexError, OSError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Review artifact is missing or unreadable"
            )
            self.send(400, {"error": message})

    def do_POST(self):
        if not self.allowed(api=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= 8192
                or self.headers.get("Content-Type", "").split(";")[0]
                != "application/json"
            ):
                raise ValueError("A small JSON request body is required")
            match = re.fullmatch(
                r"/api/jobs/([a-f0-9]{64})/(approve|reject|retry)",
                urlsplit(self.path).path,
            )
            if match is None:
                self.send(404, {"error": "Not found"})
                return
            data = json.loads(self.rfile.read(length))
            self.send(200, self.server.service.mutate(match[1], match[2], data))
        except (ValueError, KeyError, OSError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Review artifact is missing or unreadable"
            )
            self.send(400, {"error": message})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--database", type=Path, default=Path(".cache/jobs.sqlite"))
    parser.add_argument("--import-index", type=Path)
    parser.add_argument("--port", type=int, default=8910)
    args = parser.parse_args()
    if args.import_index:
        print(
            json.dumps(
                import_index(args.import_index, args.database, args.workspace), indent=2
            )
        )
        return
    if not 0 <= args.port <= 65535:
        parser.error("Invalid port")
    service = ReviewService(args.database, args.workspace)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    server.service = service
    server.token = secrets.token_hex(32)
    print(f"Review workbench: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
