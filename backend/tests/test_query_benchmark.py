from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from app.services.query_benchmark import (
    BenchmarkError,
    _peak_rss_bytes,
    benchmark_queries,
)


def _create_database(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE stores (
                store_id UUID,
                domain VARCHAR,
                title VARCHAR,
                description VARCHAR,
                country_code VARCHAR,
                status VARCHAR,
                region VARCHAR,
                estimated_monthly_visits BIGINT,
                estimated_monthly_sales_amount DECIMAL(20, 2),
                has_cms BOOLEAN
            );
            INSERT INTO stores VALUES
                (cast(md5('alpha.example') AS UUID), 'alpha.example',
                 'Alpha catalog', 'Durable outdoor equipment', 'US', 'Active',
                 'Americas', 100, 1000.00, true),
                (cast(md5('beta.example') AS UUID), 'beta.example',
                 'Beta catalog', 'Handmade outdoor supplies', 'US', 'Active',
                 'Americas', 200, 2000.00, true),
                (cast(md5('gamma.example') AS UUID), 'gamma.example',
                 'Gamma shop', 'Modern furniture collection', 'CA', 'Active',
                 'Americas', 300, 3000.00, false),
                (cast(md5('delta.example') AS UUID), 'delta.example',
                 'Delta shop', 'Independent book seller', 'GB', 'Inactive',
                 'Europe', 400, 4000.00, true);
            CREATE TABLE store_technologies (
                store_id UUID, ordinal INTEGER, value VARCHAR
            );
            INSERT INTO store_technologies
            SELECT store_id, 1, 'Wordpress' FROM stores;
            CREATE TABLE import_metadata (
                schema_version INTEGER,
                source_sha256 VARCHAR,
                stores_row_count BIGINT,
                duckdb_version VARCHAR
            );
            INSERT INTO import_metadata VALUES (1, 'test-sha', 4, version());
            """
        )


def test_benchmark_writes_reports_and_removes_exports(tmp_path: Path) -> None:
    database = tmp_path / "stores.duckdb"
    reports = tmp_path / "reports"
    _create_database(database)

    result = benchmark_queries(
        database,
        reports,
        memory_limit="256MB",
        warm_runs=1,
        large_export_rows=10_001,
        explain_threshold_seconds=0,
    )

    assert len(result.measurements) == 11
    assert result.json_report.exists()
    assert result.markdown_report.exists()
    payload = json.loads(result.json_report.read_text(encoding="utf-8"))
    assert payload["database_metadata"]["row_count"] == 4
    assert all(item["explain_analyze"] for item in payload["measurements"])
    exports = {item["key"]: item for item in payload["measurements"]}
    assert exports["export_10000"]["returned_rows"] == 4
    assert exports["export_large"]["returned_rows"] == 4
    assert not list(tmp_path.rglob("*.csv"))


def test_benchmark_rejects_invalid_run_options(tmp_path: Path) -> None:
    database = tmp_path / "stores.duckdb"
    _create_database(database)

    with pytest.raises(BenchmarkError, match="warm_runs"):
        benchmark_queries(database, tmp_path / "reports", warm_runs=0)
    with pytest.raises(BenchmarkError, match="greater than 10,000"):
        benchmark_queries(
            database, tmp_path / "reports", large_export_rows=10_000
        )


def test_peak_rss_is_available_on_supported_platforms() -> None:
    assert _peak_rss_bytes() > 0
