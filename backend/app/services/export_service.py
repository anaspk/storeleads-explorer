"""Durable, single-process CSV export jobs backed by DuckDB COPY."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from app.models.query import ExportJobResponse, ExportRequest
from app.services.query_service import QueryAPIError, compile_export_query

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
DEFAULT_RETENTION_DAYS = 7


def _now() -> datetime:
    return datetime.now(UTC)


class ExportManager:
    """Own one export worker and JSON metadata that survives app restarts."""

    def __init__(self, database: Path, export_dir: Path) -> None:
        self.database = Path(database)
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export")
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[None]] = {}
        self._connections: dict[str, duckdb.DuckDBPyConnection] = {}
        self._load_jobs()

    def _metadata_path(self, export_id: str) -> Path:
        return self.export_dir / f"{export_id}.json"

    def _output_path(self, export_id: str) -> Path:
        return self.export_dir / f"{export_id}.csv"

    def _partial_path(self, export_id: str) -> Path:
        return self.export_dir / f"{export_id}.partial"

    def _write(self, job: dict[str, Any]) -> None:
        path = self._metadata_path(job["export_id"])
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(job, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)

    def _load_jobs(self) -> None:
        resumable: list[str] = []
        for path in self.export_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                export_id = job["export_id"]
                if path != self._metadata_path(export_id):
                    continue
                if job["status"] == "completed" and not Path(
                    job["output_path"]
                ).is_file():
                    job.update(
                        status="failed",
                        completed_at=_now().isoformat(),
                        error=(
                            "The completed export file is missing; create a new export."
                        ),
                    )
                    self._write(job)
                elif job["status"] in {"queued", "running"}:
                    job.update(status="queued", started_at=None, completed_at=None)
                    self._write(job)
                    resumable.append(export_id)
                self._jobs[export_id] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for export_id in resumable:
            self._submit(export_id)

    def create(self, request: ExportRequest) -> ExportJobResponse:
        _, _, selected = compile_export_query(request)
        if not self.database.is_file():
            raise QueryAPIError(
                "database_unavailable",
                f"Store database is unavailable at {self.database}",
                status_code=503,
            )
        export_id = str(uuid4())
        job: dict[str, Any] = {
            "export_id": export_id,
            "status": "queued",
            "query": request.model_dump(mode="json"),
            "columns": selected,
            "created_at": _now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "output_path": str(self._output_path(export_id).resolve()),
            "row_count": None,
            "byte_size": None,
            "error": None,
        }
        with self._lock:
            self._jobs[export_id] = job
            self._write(job)
            self._submit(export_id)
        return self._response(job)

    def _submit(self, export_id: str) -> None:
        self._futures[export_id] = self._executor.submit(self._run, export_id)

    def _run(self, export_id: str) -> None:
        partial = self._partial_path(export_id)
        output = self._output_path(export_id)
        connection: duckdb.DuckDBPyConnection | None = None
        try:
            with self._lock:
                job = self._jobs[export_id]
                if job["status"] == "cancelled":
                    return
                job.update(status="running", started_at=_now().isoformat())
                self._write(job)
                request = ExportRequest.model_validate(job["query"])
            select_sql, parameters, _ = compile_export_query(request)
            connection = duckdb.connect(str(self.database), read_only=True)
            with self._lock:
                self._connections[export_id] = connection
                if self._jobs[export_id]["status"] == "cancelled":
                    return
            destination = str(partial.resolve()).replace("'", "''")
            result = connection.execute(
                f"COPY ({select_sql}) TO '{destination}' (FORMAT CSV, HEADER)",
                parameters,
            )
            row_count = int(result.fetchone()[0])
            with self._lock:
                job = self._jobs[export_id]
                if job["status"] == "cancelled":
                    return
                os.replace(partial, output)
                job.update(
                    status="completed",
                    completed_at=_now().isoformat(),
                    row_count=row_count,
                    byte_size=output.stat().st_size,
                )
                self._write(job)
        except Exception as error:
            with self._lock:
                job = self._jobs[export_id]
                if job["status"] != "cancelled":
                    job.update(
                        status="failed",
                        completed_at=_now().isoformat(),
                        error=f"CSV export failed: {error}",
                    )
                    self._write(job)
        finally:
            if connection is not None:
                connection.close()
            with self._lock:
                self._connections.pop(export_id, None)
            partial.unlink(missing_ok=True)

    def _job(self, export_id: str) -> dict[str, Any]:
        try:
            return self._jobs[export_id]
        except KeyError as error:
            raise QueryAPIError(
                "export_not_found", "Export job was not found", status_code=404
            ) from error

    @staticmethod
    def _response(job: dict[str, Any]) -> ExportJobResponse:
        return ExportJobResponse(
            export_id=job["export_id"],
            status=job["status"],
            columns=job["columns"],
            created_at=job["created_at"],
            started_at=job["started_at"],
            completed_at=job["completed_at"],
            row_count=job["row_count"],
            byte_size=job["byte_size"],
            error=job["error"],
            download_url=(
                f'/api/exports/{job["export_id"]}/download'
                if job["status"] == "completed"
                else None
            ),
        )

    def get(self, export_id: str) -> ExportJobResponse:
        with self._lock:
            return self._response(self._job(export_id))

    def download_path(self, export_id: str) -> Path:
        with self._lock:
            job = self._job(export_id)
            if job["status"] != "completed":
                raise QueryAPIError(
                    "export_not_ready",
                    "Export is not ready for download",
                    status_code=409,
                )
            path = Path(job["output_path"])
            if not path.is_file():
                raise QueryAPIError(
                    "export_file_missing",
                    "Export file is missing; create a new export",
                    status_code=410,
                )
            return path

    def cancel(self, export_id: str) -> ExportJobResponse:
        with self._lock:
            job = self._job(export_id)
            if job["status"] in TERMINAL_STATUSES:
                if job["status"] == "cancelled":
                    return self._response(job)
                raise QueryAPIError(
                    "export_not_cancellable",
                    f'Export is already {job["status"]}',
                    status_code=409,
                )
            job.update(status="cancelled", completed_at=_now().isoformat())
            self._write(job)
            future = self._futures.get(export_id)
            if future is not None:
                future.cancel()
            connection = self._connections.get(export_id)
            if connection is not None:
                connection.interrupt()
            return self._response(job)

    def shutdown(self) -> None:
        with self._lock:
            for export_id, job in self._jobs.items():
                if job["status"] in {"queued", "running"}:
                    job.update(status="cancelled", completed_at=_now().isoformat())
                    self._write(job)
                    connection = self._connections.get(export_id)
                    if connection is not None:
                        connection.interrupt()
        self._executor.shutdown(wait=True, cancel_futures=True)


def cleanup_exports(export_dir: Path, retention_days: int) -> tuple[int, int]:
    """Remove terminal jobs older than the retention window and their outputs."""
    if retention_days < 0:
        raise ValueError("retention days must be zero or greater")
    export_dir = Path(export_dir)
    if not export_dir.exists():
        return 0, 0
    cutoff = _now() - timedelta(days=retention_days)
    removed_jobs = 0
    removed_bytes = 0
    for metadata in export_dir.glob("*.json"):
        try:
            job = json.loads(metadata.read_text(encoding="utf-8"))
            completed = job.get("completed_at")
            if job.get("status") not in TERMINAL_STATUSES or not completed:
                continue
            if datetime.fromisoformat(completed) > cutoff:
                continue
            output = Path(job.get("output_path", ""))
            if output.parent == export_dir.resolve() and output.is_file():
                removed_bytes += output.stat().st_size
                output.unlink()
            (export_dir / f'{job["export_id"]}.partial').unlink(missing_ok=True)
            metadata.unlink()
            removed_jobs += 1
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return removed_jobs, removed_bytes
