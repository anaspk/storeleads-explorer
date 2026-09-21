#!/usr/bin/env python3
"""Profile a Store Leads CSV without modifying the source file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from app.services.csv_profiler import (
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_SAMPLE_ROWS,
    DEFAULT_TOP_VALUES,
    ProfileError,
    profile_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile a complete Store Leads CSV using read-only source access."
    )
    parser.add_argument("source", type=Path, help="CSV file to profile")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../data/profiles"),
        help="report and scratch directory (default: ../data/profiles)",
    )
    parser.add_argument(
        "--memory-limit",
        default=DEFAULT_MEMORY_LIMIT,
        help=f"DuckDB memory limit (default: {DEFAULT_MEMORY_LIMIT})",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=f"rows used for candidate inference (default: {DEFAULT_SAMPLE_ROWS})",
    )
    parser.add_argument(
        "--top-values",
        type=int,
        default=DEFAULT_TOP_VALUES,
        help=(
            "frequent values retained per categorical column "
            f"(default: {DEFAULT_TOP_VALUES})"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        json_path, markdown_path, profile = profile_csv(
            args.source,
            args.output_dir,
            memory_limit=args.memory_limit,
            sample_rows=args.sample_rows,
            top_values=args.top_values,
        )
    except (OSError, ProfileError, duckdb.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"Profiled {profile['parser']['accepted_row_count']:,} rows across "
        f"{profile['parser']['column_count']} columns in "
        f"{profile['run']['duration_seconds']:.2f}s."
    )
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
