from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import duckdb
import pytest

from app.services.csv_importer import (
    DROPPED_EMPTY_COLUMNS,
    EXPECTED_COLUMNS,
    ImportError,
    import_csv,
)


def _write_source(path: Path, *, populate_dropped: bool = False) -> None:
    rows = []
    for domain in ("alpha.example", "beta.example"):
        row = dict.fromkeys(EXPECTED_COLUMNS, "")
        row.update(
            {
                "domain": domain,
                "combined_avgrating": "4.25",
                "combined_followers": "123",
                "created": "2026/01/02",
                "has_cms": "true",
                "headless": "false",
                "estimated_monthly_sales": "USD $1,234.50",
                "estimated_yearly_sales": "USD $14,814.00",
                "aliases": "one:two",
                "categories": "Clothing:Shoes",
                "installed_apps": (
                    "https://apps.example/one/:https://apps.example/two/"
                ),
                "technologies": "WooCommerce:Klaviyo",
            }
        )
        rows.append(row)
    if populate_dropped:
        rows[0]["notes"] = "unexpected"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_import_is_typed_normalized_atomic_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    database = tmp_path / "storeleads.duckdb"
    _write_source(source)
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    before_stat = source.stat()

    first = import_csv(source, database, memory_limit="256MB")
    assert first.row_count == 2
    assert source.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash

    with duckdb.connect(str(database), read_only=True) as connection:
        columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info('stores')").fetchall()
        }
        assert not set(DROPPED_EMPTY_COLUMNS) & columns.keys()
        assert columns["store_id"] == "UUID"
        assert columns["combined_followers"] == "BIGINT"
        assert columns["created"] == "DATE"
        assert columns["estimated_monthly_sales_amount"] == "DECIMAL(20,2)"
        first_ids = connection.execute(
            "SELECT domain, store_id FROM stores ORDER BY domain"
        ).fetchall()
        apps = connection.execute(
            "SELECT value, ordinal FROM store_installed_apps ORDER BY store_id, ordinal"
        ).fetchall()
        assert apps[:2] == [
            ("https://apps.example/one/", 1),
            ("https://apps.example/two/", 2),
        ]
        assert connection.execute(
            "SELECT sum(failure_count) FROM import_conversion_failures"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT malformed_row_policy FROM import_metadata"
        ).fetchone()[0] == "fail_import"

    second = import_csv(source, database, memory_limit="256MB")
    assert second.row_count == 2
    with duckdb.connect(str(database), read_only=True) as connection:
        second_ids = connection.execute(
            "SELECT domain, store_id FROM stores ORDER BY domain"
        ).fetchall()
    assert second_ids == first_ids
    assert not list(tmp_path.glob("*.partial.duckdb"))


def test_populated_omitted_column_does_not_replace_database(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    database = tmp_path / "storeleads.duckdb"
    _write_source(source, populate_dropped=True)
    database.write_bytes(b"existing database placeholder")
    existing = database.read_bytes()

    with pytest.raises(ImportError, match="no longer empty"):
        import_csv(source, database, memory_limit="256MB")

    assert database.read_bytes() == existing
    assert not list(tmp_path.glob("*.partial.duckdb"))


def test_rejects_schema_drift_before_creating_database(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("domain,title\nexample.com,Example\n", encoding="utf-8")
    database = tmp_path / "storeleads.duckdb"

    with pytest.raises(ImportError, match="header does not match"):
        import_csv(source, database)

    assert not database.exists()


def test_missing_source_has_a_clean_import_error(tmp_path: Path) -> None:
    with pytest.raises(ImportError, match="No such file or directory"):
        import_csv(tmp_path / "missing.csv", tmp_path / "storeleads.duckdb")
