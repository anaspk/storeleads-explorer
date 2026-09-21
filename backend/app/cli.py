"""Command-line entry points for Store Leads maintenance tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.csv_importer import DEFAULT_MEMORY_LIMIT, ImportError, import_csv


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
