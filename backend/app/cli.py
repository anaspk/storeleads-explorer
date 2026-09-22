"""Command-line entry points for Store Leads maintenance tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.csv_importer import DEFAULT_MEMORY_LIMIT, ImportError, import_csv
from app.services.export_service import DEFAULT_RETENTION_DAYS, cleanup_exports
from app.services.query_benchmark import (
    DEFAULT_EXPLAIN_THRESHOLD_SECONDS,
    DEFAULT_LARGE_EXPORT_ROWS,
    DEFAULT_WARM_RUNS,
    BenchmarkError,
    benchmark_queries,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser(
        "import-csv", description="Build and atomically activate a DuckDB database"
    )
    importer.add_argument("source", type=Path, help="profiled Store Leads CSV")
    importer.add_argument("--database", type=Path, required=True)
    importer.add_argument(
        "--memory-limit",
        default=DEFAULT_MEMORY_LIMIT,
        help=f"DuckDB memory limit (default: {DEFAULT_MEMORY_LIMIT})",
    )
    benchmark = commands.add_parser(
        "benchmark", description="Benchmark representative Store Leads queries"
    )
    benchmark.add_argument("--database", type=Path, required=True)
    benchmark.add_argument(
        "--output-dir", type=Path, default=Path("../data/benchmarks")
    )
    benchmark.add_argument("--memory-limit", default=DEFAULT_MEMORY_LIMIT)
    benchmark.add_argument("--warm-runs", type=int, default=DEFAULT_WARM_RUNS)
    benchmark.add_argument(
        "--large-export-rows", type=int, default=DEFAULT_LARGE_EXPORT_ROWS
    )
    benchmark.add_argument(
        "--explain-threshold-seconds",
        type=float,
        default=DEFAULT_EXPLAIN_THRESHOLD_SECONDS,
    )
    cleanup = commands.add_parser(
        "cleanup-exports", description="Delete terminal export jobs past retention"
    )
    cleanup.add_argument("--export-dir", type=Path, default=Path("../exports"))
    cleanup.add_argument(
        "--retention-days", type=int, default=DEFAULT_RETENTION_DAYS
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "cleanup-exports":
        try:
            jobs, byte_size = cleanup_exports(args.export_dir, args.retention_days)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"Removed {jobs} export job(s), freeing {byte_size:,} bytes.")
        return 0
    if args.command == "import-csv":
        try:
            result = import_csv(
                args.source, args.database, memory_limit=args.memory_limit
            )
        except ImportError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(
            f"Imported {result.row_count:,} stores into {result.database} "
            f"in {result.duration_seconds:.2f}s."
        )
        print(f"Source SHA-256: {result.source_sha256}")
        for column, count in result.collection_row_counts.items():
            print(f"store_{column}: {count:,} rows")
        return 0

    try:
        result = benchmark_queries(
            args.database,
            args.output_dir,
            memory_limit=args.memory_limit,
            warm_runs=args.warm_runs,
            large_export_rows=args.large_export_rows,
            explain_threshold_seconds=args.explain_threshold_seconds,
        )
    except (BenchmarkError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for measurement in result.measurements:
        print(
            f"{measurement.label}: cold={measurement.cold_seconds:.3f}s, "
            f"warm median={measurement.warm_median_seconds:.3f}s"
        )
    print(f"JSON report: {result.json_report}")
    print(f"Markdown report: {result.markdown_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
