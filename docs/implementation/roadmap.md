# Implementation Roadmap

Last updated: 2026-09-22

## Guiding principle

Prove the typed data model and representative query performance before investing
heavily in the interface. The UI will be straightforward once the filtering and
export semantics are correct and fast.

## Phase 0 — Establish the project

**Status: complete (2026-09-22).** The repository now follows the target layout,
with a runnable FastAPI health endpoint, a React/TypeScript/Vite application
shell, dependency lockfiles, baseline tests and static checks, documented local
startup commands, and ignore rules for full datasets, DuckDB files, temporary
files, and generated exports.

Suggested eventual layout:

```text
storeleads-clone/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── db/
│   │   ├── models/
│   │   ├── services/
│   │   └── main.py
│   ├── scripts/
│   │   ├── profile_csv.py
│   │   └── import_csv.py
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── data/
│   ├── first_100_rows.csv
│   └── .gitkeep
├── docs/
├── exports/
│   └── .gitkeep
└── README.md
```

When version control is initialized, ignore the full CSV, DuckDB files,
temporary files, and generated exports. Keep the 100-row sample only if its
license and sensitivity permit committing it.

## Phase 1 — Profile the complete CSV

Deliver a repeatable profiler rather than relying on manual inspection.

Outputs should include:

- row and column counts;
- source size and SHA-256 checksum;
- inferred and proposed types;
- null and distinct counts;
- failed-cast examples and counts;
- frequent values for categorical columns;
- length statistics for strings;
- candidate delimiter behavior; and
- domain uniqueness statistics.

Write the report to a dated JSON file and a readable Markdown summary. Do not
silently ignore parse errors.

### Exit criteria

- The complete file can be scanned end to end.
- Every column has a proposed explicit type.
- Problematic values are enumerated.
- Identifier and multi-value rules are documented.

## Phase 2 — Implement repeatable ingestion

Create a command resembling:

```text
python -m app.cli import-csv /path/to/full.csv \
  --database data/storeleads.duckdb
```

Responsibilities:

1. Validate input path and available disk space.
2. Create a new temporary database rather than overwriting the working database.
3. Load raw or explicitly typed data.
4. Transform money, dates, booleans, and numeric measurements.
5. Generate `store_id` values.
6. Build bridge tables for every column confirmed to contain a
   colon-delimited collection.
7. Create justified indexes.
8. Validate row counts and important invariants.
9. Record import metadata.
10. Atomically replace or activate the database only after validation succeeds.

Use `TRY_CAST` for dirty values and separately count failures. Skipping malformed
rows should be an explicit, reported policy rather than a default.

### Exit criteria

- The full dataset imports without unexplained row loss.
- A second run is deterministic and safe.
- Type-conversion failures are visible.
- Every confirmed colon-delimited collection has a populated, validated child
  table while its original source value remains available.
- Representative SQL queries return correct results.

## Phase 3 — Benchmark representative queries

Create a small benchmark suite covering likely user behavior:

1. Exact domain lookup.
2. Country/status equality filter.
3. Numeric range filter on visits or sales.
4. Boolean plus numeric filter.
5. Technology membership filter.
6. Full-text search over `title` and `description`.
7. Substring search over `domain`.
8. Sort a broad result by rank or visits and return the first 100 rows.
9. Multi-filter facet counts.
10. Export a representative 10,000-row result.
11. Export a larger result to identify the point at which background execution
    becomes necessary.

Capture cold and warm timings and use `EXPLAIN ANALYZE` for slow queries. Only
then decide whether physical ordering, bridge tables, or ART indexes are needed.

Acceptance targets on the intended machine:

- point lookup: well below 500 ms;
- ordinary first-page filters: ideally around 1 second or less;
- all supported interactive queries: under 5 seconds;
- export: streaming/background work with no application memory spike.

## Phase 4 — Build the query API

Implement a schema registry describing each exposed column:

```text
name
label
data_type
filter_operators
sortable
filterable
default_visible
facet_enabled
```

Implement and test:

- validation of the filter AST;
- allowlisted SQL generation;
- bound values;
- deterministic ordering;
- pagination cursors;
- query cancellation/timeouts where practical;
- result serialization without loading unnecessary columns; and
- structured error responses.

### Exit criteria

- No endpoint accepts arbitrary SQL.
- Invalid fields and operators are rejected.
- Filtering, sorting, and pagination are consistent across the full dataset.
- Unit tests cover SQL injection attempts and null semantics.

## Phase 5 — Build the frontend

Start with one data-explorer page:

1. Curated default columns.
2. Searchable column chooser.
3. Type-aware filter rows with AND semantics.
4. Optional nested AND/OR groups after basic filters are stable.
5. Server-side sorting and pagination.
6. Active-filter chips and a clear-all action.
7. URL or local-storage persistence for views.
8. Export dialog with result and column summary.

Debounce text filters and ensure stale responses cannot replace newer results.

### Exit criteria

- The page remains responsive regardless of total dataset size.
- All visible results reflect server-side filtering and sorting.
- Wide rows are usable without displaying all 162 columns.
- A saved view can be restored reliably.

## Phase 6 — Add export jobs

The export request should persist the validated query specification, selected
columns, creation time, status, output path, row count, byte size, and any error.

Most exports are expected to contain at most 10,000 rows. Implement and optimize
that path first. Keep the job model even if the initial 10,000-row path is served
synchronously, because larger results or expensive filters may still require
background execution. Do not impose a row cap or display a size warning for
larger exports; slower completion is acceptable. Normal job progress and status
remain useful for all asynchronous exports.

Suggested lifecycle:

```text
queued -> running -> completed
                  -> failed
                  -> cancelled
```

For the first single-user release, an in-process worker is sufficient. Do not
introduce Redis or a distributed queue unless application requirements change.

### Exit criteria

- DuckDB writes CSV directly without materializing all rows in Python.
- Results of any size can be exported without a row-count cap or size warning.
- Partial files are not offered for download.
- Failures leave actionable diagnostics.
- Old exports can be cleaned using a documented retention rule.

## Phase 7 — Hardening

- Add import, query-builder, API, and end-to-end tests.
- Add structured logs and query timing.
- Set maximum page size and export concurrency.
- Document backup and refresh procedures.
- Add a health check that verifies database availability and schema version.
- Bind to `127.0.0.1` by default.
- Package startup behind one documented command.

## Open questions for the owner

These should be answered through usage or a brief product pass before advanced
UI work:

1. Which 10–20 fields should appear in the default table?
2. Which filters are used most often?
3. What search behavior should apply to columns other than `title`,
   `description`, and `domain`?
4. Is the source replaced periodically, and if so, how often?
5. Must exports reproduce original raw strings or use cleaned typed values?
6. Are nested OR conditions required, or will a flat list of AND filters suffice
   initially?

## First executable milestone

The first meaningful deliverable should be:

- a full-file profiling command;
- an explicit schema definition;
- a repeatable DuckDB import command;
- a benchmark script; and
- a short report of actual query timings on the target computer.

That milestone will confirm or invalidate the architecture with evidence before
the application surface grows.
