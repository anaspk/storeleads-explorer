# Store Leads Explorer — Project Notes

This directory records the current understanding of the dataset and the proposed
architecture for a personal application that can search, filter, sort, and export
records from the full Store Leads CSV.

## Documents

- [Data profile](data/sample-profile.md) — facts observed in the supplied
  100-row sample, limitations of that sample, and schema implications.
- [Complete CSV profile](data/full-profile.md) — Phase 1 results from all
  4,053,656 source rows, including confirmed types, identity rules, money
  parsing, and collection delimiters.
- [Ingestion schema decisions](data/ingestion-schema.md) — Phase 2 schema,
  validation, and atomic-activation rules, including why 17 empty source fields
  are omitted.
- [Architecture recommendation](architecture/recommendation.md) — recommended
  stack, database choice, query model, and alternatives.
- [Implementation roadmap](implementation/roadmap.md) — phased plan, suggested
  project layout, API outline, and acceptance criteria.
- [Query benchmark](implementation/query-benchmark.md) — Phase 3 cold/warm
  query and direct-export timings from the complete imported dataset.
- [Query API](implementation/query-api.md) — Phase 4 endpoint contract, filter
  operators, cursor behavior, limits, errors, and verification.

## Current project state

- The workspace contains `data/first_100_rows.csv`.
- The sample has 100 data rows and 162 columns.
- The full source has 4,053,656 rows, 162 columns, and is 4,739,476,938 bytes.
- The full CSV is stored outside this workspace and remains unchanged.
- Phase 0 is complete: the FastAPI backend and React/Vite frontend are
  scaffolded, runnable, and covered by baseline quality checks.
- Phase 1 is complete: a repeatable profiler scanned the complete CSV and
  produced dated JSON and Markdown reports.
- Phase 2 provides repeatable, validated, atomic DuckDB ingestion.
- Phase 3 provides a repeatable representative-query benchmark; all measured
  interactive cases met the latency target on the intended machine.
- Phase 4 provides schema, query, and facet endpoints with strict allowlists,
  bound values, deterministic cursor pagination, and structured errors.
- Phase 5 provides the schema-driven data explorer, server-side table controls,
  type-aware filters, saved views, and export preparation UI.

## Current recommendation in one sentence

Build a local React/TypeScript interface backed by a single-process FastAPI
service and a persistent DuckDB database, with all dataset-wide filtering,
sorting, pagination, faceting, and CSV generation performed on the server.

## Confirmed product decisions

- Free-form text in `title` and `description` requires token-based full-text
  search.
- `domain` requires substring matching rather than full-text tokenization.
- Search behavior for other columns will be decided later, column by column.
- Every column that is confirmed to contain a colon-delimited collection should
  be normalized into a child/bridge table for efficient filtering. A colon's
  mere presence is not sufficient evidence because URLs and prose also contain
  colons.
- Most CSV exports are expected to contain no more than 10,000 rows.
- Exports larger than 10,000 rows must remain available without warnings or a
  hard row cap. Longer completion times are acceptable for these uncommon cases.
- Interactive query latency below five seconds is acceptable.

## Confirmed by complete-file profiling

1. The complete source parses as UTF-8 with no rejected rows.
2. `domain` is non-null and exactly unique across all 4,053,656 rows.
3. All proposed typed conversions have zero failures in the current source.
4. Both sales fields are consistently parseable USD values.
5. Twelve fields have confirmed collection semantics and explicit split rules.

## Decisions still requiring usage validation

1. Which columns and filter combinations are used most frequently.
2. Search semantics for columns other than `title`, `description`, and `domain`.

The 17 completely empty source columns are omitted during ingestion. If a later
source populates one, import stops so its type can be decided from evidence.
