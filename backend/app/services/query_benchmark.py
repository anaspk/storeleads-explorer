"""Repeatable Phase 3 benchmarks for the imported Store Leads database."""

from __future__ import annotations

import json
import os
import platform
import re
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
else:
    import resource

import duckdb

from app.services.csv_importer import DEFAULT_MEMORY_LIMIT, SCHEMA_VERSION

DEFAULT_WARM_RUNS = 3
DEFAULT_LARGE_EXPORT_ROWS = 1_000_000
DEFAULT_EXPLAIN_THRESHOLD_SECONDS = 5.0
POINT_LOOKUP_TARGET_SECONDS = 0.5
INTERACTIVE_TARGET_SECONDS = 5.0


if sys.platform == "win32":

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    _get_current_process = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetCurrentProcess
    _get_current_process.argtypes = []
    _get_current_process.restype = wintypes.HANDLE

    _get_process_memory_info = ctypes.WinDLL(
        "psapi", use_last_error=True
    ).GetProcessMemoryInfo
    _get_process_memory_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    _get_process_memory_info.restype = wintypes.BOOL


class BenchmarkError(RuntimeError):
    """Raised when a benchmark cannot be run safely or meaningfully."""


@dataclass(frozen=True)
class BenchmarkCase:
    key: str
    label: str
    category: str
    sql: str
    parameters: tuple[Any, ...] = ()
    export_rows: int | None = None


@dataclass(frozen=True)
class BenchmarkMeasurement:
    key: str
    label: str
    category: str
    cold_seconds: float
    warm_seconds: list[float]
    warm_median_seconds: float
    returned_rows: int
    export_bytes: int | None
    process_peak_rss_delta_bytes: int
    explain_analyze: str | None


@dataclass(frozen=True)
class BenchmarkResult:
    json_report: Path
    markdown_report: Path
    measurements: list[BenchmarkMeasurement]


def _quote_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _peak_rss_bytes() -> int:
    if sys.platform == "win32":
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not _get_process_memory_info(
            _get_current_process(), ctypes.byref(counters), counters.cb
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.PeakWorkingSetSize)

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux and most BSDs report KiB.
    return peak if platform.system() == "Darwin" else peak * 1024


def _connect(database: Path, memory_limit: str) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(database), read_only=True)
    connection.execute(f"SET memory_limit = {_quote_string(memory_limit.upper())}")
    return connection


def _validate_database(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall()
    }
    required = {"stores", "store_technologies", "import_metadata"}
    missing = required - tables
    if missing:
        raise BenchmarkError(f"Database is missing required tables: {sorted(missing)}")
    row = connection.execute(
        "SELECT schema_version, source_sha256, stores_row_count, duckdb_version "
        "FROM import_metadata"
    ).fetchone()
    if row is None:
        raise BenchmarkError("import_metadata is empty")
    if row[0] != SCHEMA_VERSION:
        raise BenchmarkError(
            f"Expected schema version {SCHEMA_VERSION}, found {row[0]}"
        )
    actual_count = connection.execute("SELECT count(*) FROM stores").fetchone()[0]
    if actual_count != row[2]:
        raise BenchmarkError(
            f"stores row count {actual_count} does not match import metadata {row[2]}"
        )
    return {
        "schema_version": row[0],
        "source_sha256": row[1],
        "row_count": row[2],
        "import_duckdb_version": row[3],
    }


def _choose_text_token(connection: duckdb.DuckDBPyConnection) -> str:
    rows = connection.execute(
        "SELECT title, description FROM stores "
        "WHERE title IS NOT NULL AND description IS NOT NULL LIMIT 100"
    ).fetchall()
    stop_words = {"about", "from", "have", "online", "store", "that", "this", "with"}
    for title, description in rows:
        for token in re.findall(r"[A-Za-z0-9]+", f"{title} {description}"):
            if len(token) >= 5 and token.lower() not in stop_words:
                return token.lower()
    raise BenchmarkError("Could not choose a token from title/description")


def _build_cases(
    connection: duckdb.DuckDBPyConnection, large_export_rows: int
) -> tuple[list[BenchmarkCase], dict[str, Any]]:
    domain = connection.execute(
        "SELECT domain FROM stores ORDER BY store_id LIMIT 1"
    ).fetchone()[0]
    country, status = connection.execute(
        "SELECT country_code, status FROM stores "
        "WHERE country_code IS NOT NULL AND status IS NOT NULL "
        "GROUP BY country_code, status ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()
    visit_low, visit_high = connection.execute(
        "SELECT quantile_cont(estimated_monthly_visits, 0.5)::BIGINT, "
        "quantile_cont(estimated_monthly_visits, 0.75)::BIGINT FROM stores "
        "WHERE estimated_monthly_visits IS NOT NULL"
    ).fetchone()
    technology = connection.execute(
        "SELECT value FROM store_technologies GROUP BY value "
        "ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()[0]
    text_token = _choose_text_token(connection)
    domain_part = max(re.findall(r"[A-Za-z0-9]+", domain), key=len)
    if len(domain_part) > 8:
        domain_part = domain_part[1:8]
    text_pattern = rf"(^|[^[:alnum:]_]){re.escape(text_token)}([^[:alnum:]_]|$)"

    projection = (
        "store_id, domain, title, country_code, status, estimated_monthly_visits"
    )
    broad_export = (
        "SELECT domain, title, country_code, status, estimated_monthly_visits, "
        "estimated_monthly_sales_amount FROM stores "
        "ORDER BY estimated_monthly_visits DESC NULLS LAST, store_id "
    )
    cases = [
        BenchmarkCase(
            "exact_domain", "Exact domain lookup", "point_lookup",
            f"SELECT {projection} FROM stores WHERE domain = ? LIMIT 1", (domain,),
        ),
        BenchmarkCase(
            "country_status", "Country/status equality filter", "interactive",
            f"SELECT {projection} FROM stores WHERE country_code = ? AND status = ? "
            "ORDER BY store_id LIMIT 100", (country, status),
        ),
        BenchmarkCase(
            "visits_range", "Numeric visits range", "interactive",
            f"SELECT {projection} FROM stores WHERE estimated_monthly_visits "
            "BETWEEN ? AND ? ORDER BY store_id LIMIT 100", (visit_low, visit_high),
        ),
        BenchmarkCase(
            "boolean_numeric", "Boolean plus numeric filter", "interactive",
            f"SELECT {projection} FROM stores WHERE has_cms = true "
            "AND estimated_monthly_visits >= ? ORDER BY store_id LIMIT 100",
            (visit_high,),
        ),
        BenchmarkCase(
            "technology_membership", "Technology membership filter", "interactive",
            f"SELECT {projection} FROM stores s WHERE EXISTS ("
            "SELECT 1 FROM store_technologies t WHERE t.store_id = s.store_id "
            "AND t.value = ?) ORDER BY s.store_id LIMIT 100", (technology,),
        ),
        BenchmarkCase(
            "title_description_token", "Title/description token search", "interactive",
            f"SELECT {projection} FROM stores WHERE regexp_matches("
            "lower(coalesce(title, '') || ' ' || coalesce(description, '')), ?) "
            "ORDER BY store_id LIMIT 100", (text_pattern,),
        ),
        BenchmarkCase(
            "domain_substring", "Domain substring search", "interactive",
            f"SELECT {projection} FROM stores WHERE contains(lower(domain), ?) "
            "ORDER BY store_id LIMIT 100", (domain_part.lower(),),
        ),
        BenchmarkCase(
            "broad_sort", "Broad result sorted by visits", "interactive",
            f"SELECT {projection} FROM stores ORDER BY "
            "estimated_monthly_visits DESC NULLS LAST, store_id LIMIT 100",
        ),
        BenchmarkCase(
            "multi_filter_facets", "Multi-filter facet counts", "interactive",
            "SELECT region, count(*) AS store_count FROM stores "
            "WHERE country_code = ? AND status = ? "
            "AND estimated_monthly_visits >= ? GROUP BY region "
            "ORDER BY store_count DESC, region", (country, status, visit_low),
        ),
        BenchmarkCase(
            "export_10000", "Export representative 10,000 rows", "export",
            broad_export + "LIMIT 10000", export_rows=10_000,
        ),
        BenchmarkCase(
            "export_large", f"Export larger {large_export_rows:,}-row result", "export",
            broad_export + f"LIMIT {large_export_rows}", export_rows=large_export_rows,
        ),
    ]
    predicates = {
        "domain": domain,
        "country_code": country,
        "status": status,
        "visit_range": [visit_low, visit_high],
        "technology": technology,
        "text_token": text_token,
        "domain_substring": domain_part.lower(),
    }
    return cases, predicates


def _execute_query(
    connection: duckdb.DuckDBPyConnection, case: BenchmarkCase, export_path: Path
) -> tuple[int, int | None]:
    if case.export_rows is None:
        rows = connection.execute(case.sql, list(case.parameters)).fetchall()
        return len(rows), None
    row = connection.execute(
        f"COPY ({case.sql}) TO {_quote_string(str(export_path))} "
        "(FORMAT CSV, HEADER true)"
    ).fetchone()
    size = export_path.stat().st_size
    export_path.unlink()
    return int(row[0]), size


def _time_execution(
    action: Callable[[], tuple[int, int | None]],
) -> tuple[float, int, int | None]:
    started = time.perf_counter()
    row_count, export_bytes = action()
    return time.perf_counter() - started, row_count, export_bytes


def _explain(
    connection: duckdb.DuckDBPyConnection, case: BenchmarkCase
) -> str:
    rows = connection.execute(
        "EXPLAIN ANALYZE " + case.sql, list(case.parameters)
    ).fetchall()
    return "\n".join(str(row[-1]) for row in rows)


def _measure_case(
    database: Path,
    memory_limit: str,
    warm_runs: int,
    explain_threshold_seconds: float,
    case: BenchmarkCase,
    export_directory: Path,
) -> BenchmarkMeasurement:
    connection = _connect(database, memory_limit)
    try:
        peak_before = _peak_rss_bytes()
        cold_path = export_directory / f"{case.key}-cold.csv"
        cold, returned_rows, export_bytes = _time_execution(
            lambda: _execute_query(connection, case, cold_path)
        )
        warm: list[float] = []
        for index in range(warm_runs):
            path = export_directory / f"{case.key}-warm-{index}.csv"
            duration, returned_rows, export_bytes = _time_execution(
                lambda path=path: _execute_query(connection, case, path)
            )
            warm.append(duration)
        peak_delta = max(0, _peak_rss_bytes() - peak_before)
        slowest = max([cold, *warm])
        plan = (
            _explain(connection, case)
            if slowest >= explain_threshold_seconds
            else None
        )
        return BenchmarkMeasurement(
            key=case.key,
            label=case.label,
            category=case.category,
            cold_seconds=cold,
            warm_seconds=warm,
            warm_median_seconds=statistics.median(warm),
            returned_rows=returned_rows,
            export_bytes=export_bytes,
            process_peak_rss_delta_bytes=peak_delta,
            explain_analyze=plan,
        )
    finally:
        connection.close()


def _recommendations(measurements: list[BenchmarkMeasurement]) -> list[str]:
    by_key = {measurement.key: measurement for measurement in measurements}
    recommendations: list[str] = []
    point = by_key["exact_domain"]
    if (
        max(point.cold_seconds, point.warm_median_seconds)
        >= POINT_LOOKUP_TARGET_SECONDS
    ):
        recommendations.append(
            "Investigate the existing unique domain ART index and its plan."
        )
    technology = by_key["technology_membership"]
    if (
        max(technology.cold_seconds, technology.warm_median_seconds)
        >= INTERACTIVE_TARGET_SECONDS
    ):
        recommendations.append(
            "Benchmark an ART index on store_technologies(value) for membership "
            "filters."
        )
    text = by_key["title_description_token"]
    if max(text.cold_seconds, text.warm_median_seconds) >= INTERACTIVE_TARGET_SECONDS:
        recommendations.append(
            "Benchmark DuckDB's FTS index for title/description token search."
        )
    sort = by_key["broad_sort"]
    if max(sort.cold_seconds, sort.warm_median_seconds) >= INTERACTIVE_TARGET_SECONDS:
        recommendations.append(
            "Test a physically ordered stores copy for the dominant rank/visits sort."
        )
    if not recommendations:
        recommendations.append(
            "Keep the current physical layout and indexes; no measured "
            "interactive case justifies another optimization."
        )
    return recommendations


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Store Leads Query Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Run context",
        "",
        f"- Database: `{report['database']}`",
        f"- Rows: {report['database_metadata']['row_count']:,}",
        f"- DuckDB: {report['environment']['duckdb_version']}",
        f"- Memory limit: {report['configuration']['memory_limit']}",
        f"- Warm repetitions: {report['configuration']['warm_runs']}",
        "- Cold means the first execution on a new DuckDB connection. It does not "
        "flush the operating-system file cache.",
        "",
        "## Results",
        "",
        "| Workload | Cold | Warm median | Rows | Output | Peak RSS delta | Target |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["measurements"]:
        target = "<0.5 s" if item["category"] == "point_lookup" else (
            "<5 s" if item["category"] == "interactive" else "observe"
        )
        output = f"{item['export_bytes']:,} B" if item["export_bytes"] else "—"
        lines.append(
            f"| {item['label']} | {item['cold_seconds']:.3f} s | "
            f"{item['warm_median_seconds']:.3f} s | {item['returned_rows']:,} | "
            f"{output} | {item['process_peak_rss_delta_bytes']:,} B | {target} |"
        )
    lines.extend(["", "## Decisions", ""])
    lines.extend(f"- {item}" for item in report["recommendations"])
    lines.extend(
        [
            "",
            "Exports use DuckDB `COPY` directly and are deleted after measurement; "
            "rows are never materialized as Python objects. Peak RSS is a process "
            "high-water-mark delta, so it is diagnostic rather than a precise "
            "allocation trace.",
        ]
    )
    slow = [item for item in report["measurements"] if item["explain_analyze"]]
    if slow:
        lines.extend(["", "## Slow-query plans", ""])
        for item in slow:
            lines.extend(
                [
                    f"### {item['label']}",
                    "",
                    "```text",
                    item["explain_analyze"],
                    "```",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def benchmark_queries(
    database: Path,
    output_directory: Path,
    *,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    warm_runs: int = DEFAULT_WARM_RUNS,
    large_export_rows: int = DEFAULT_LARGE_EXPORT_ROWS,
    explain_threshold_seconds: float = DEFAULT_EXPLAIN_THRESHOLD_SECONDS,
) -> BenchmarkResult:
    """Run representative cold/warm queries and write JSON and Markdown reports."""
    database = database.expanduser().resolve(strict=True)
    if not database.is_file():
        raise BenchmarkError(f"Database is not a regular file: {database}")
    if warm_runs < 1:
        raise BenchmarkError("warm_runs must be at least 1")
    if large_export_rows <= 10_000:
        raise BenchmarkError("large_export_rows must be greater than 10,000")
    if explain_threshold_seconds < 0:
        raise BenchmarkError("explain_threshold_seconds cannot be negative")
    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    setup: duckdb.DuckDBPyConnection | None = None
    try:
        setup = _connect(database, memory_limit)
        metadata = _validate_database(setup)
        cases, predicates = _build_cases(setup, large_export_rows)
    except duckdb.Error as error:
        raise BenchmarkError(str(error)) from error
    finally:
        if setup is not None:
            setup.close()

    measurements: list[BenchmarkMeasurement] = []
    try:
        with tempfile.TemporaryDirectory(prefix="storeleads-benchmark-") as temporary:
            export_directory = Path(temporary)
            for case in cases:
                measurements.append(
                    _measure_case(
                        database,
                        memory_limit,
                        warm_runs,
                        explain_threshold_seconds,
                        case,
                        export_directory,
                    )
                )
    except (duckdb.Error, OSError) as error:
        raise BenchmarkError(str(error)) from error

    generated_at = datetime.now(UTC)
    stamp = generated_at.strftime("%Y%m%dT%H%M%S%fZ")
    report = {
        "generated_at": generated_at.isoformat(),
        "database": str(database),
        "database_size_bytes": database.stat().st_size,
        "database_metadata": metadata,
        "environment": {
            "duckdb_version": duckdb.__version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
        },
        "configuration": {
            "memory_limit": memory_limit,
            "warm_runs": warm_runs,
            "large_export_rows": large_export_rows,
            "explain_threshold_seconds": explain_threshold_seconds,
            "cold_definition": (
                "first execution on a new DuckDB connection; OS file cache not flushed"
            ),
        },
        "predicates": predicates,
        "measurements": [asdict(item) for item in measurements],
        "recommendations": _recommendations(measurements),
    }
    json_path = output_directory / f"storeleads-benchmark-{stamp}.json"
    markdown_path = output_directory / f"storeleads-benchmark-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return BenchmarkResult(json_path, markdown_path, measurements)
