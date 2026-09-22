from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from app.models.query import ExportRequest
from app.services.export_service import ExportManager, cleanup_exports


def test_cleanup_removes_only_expired_terminal_exports(tmp_path: Path) -> None:
    export_id = "00000000-0000-0000-0000-000000000001"
    output = (tmp_path / f"{export_id}.csv").resolve()
    output.write_text("domain\nexample.com\n")
    metadata = tmp_path / f"{export_id}.json"
    metadata.write_text(
        json.dumps(
            {
                "export_id": export_id,
                "status": "completed",
                "completed_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
                "output_path": str(output),
            }
        )
    )
    recent = tmp_path / "recent.json"
    recent.write_text(
        json.dumps(
            {
                "export_id": "recent",
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "output_path": str((tmp_path / "recent.csv").resolve()),
            }
        )
    )

    jobs, byte_size = cleanup_exports(tmp_path, 7)

    assert jobs == 1
    assert byte_size > 0
    assert not metadata.exists()
    assert not output.exists()
    assert recent.exists()


def test_queued_export_can_be_cancelled_without_publishing_a_file(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stores.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE stores (store_id UUID, domain VARCHAR)")
    manager = ExportManager(database, tmp_path / "exports")
    blocker = threading.Event()
    manager._executor.submit(blocker.wait)  # Keep the sole worker occupied.
    try:
        created = manager.create(ExportRequest(columns=["domain"]))
        cancelled = manager.cancel(created.export_id)
        assert cancelled.status == "cancelled"
        assert not (manager.export_dir / f"{created.export_id}.csv").exists()
        persisted = json.loads(
            (manager.export_dir / f"{created.export_id}.json").read_text()
        )
        assert persisted["status"] == "cancelled"
    finally:
        blocker.set()
        manager.shutdown()
