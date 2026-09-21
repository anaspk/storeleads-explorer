# Store Leads Explorer — Project Notes

This directory records the current understanding of the dataset and the proposed
architecture for a personal application that can search, filter, sort, and export
records from the full Store Leads CSV.

## Documents

- [Data profile](data/sample-profile.md) — facts observed in the supplied
  100-row sample, limitations of that sample, and schema implications.
- [Architecture recommendation](architecture/recommendation.md) — recommended
  stack, database choice, query model, and alternatives.
- [Implementation roadmap](implementation/roadmap.md) — phased plan, suggested
  project layout, API outline, and acceptance criteria.

## Current project state

- The workspace contains `data/first_100_rows.csv`.
- The sample has 100 data rows and 162 columns.
- The full source described by the owner has more than 4 million rows and is
  approximately 4.8 GB.
- The full CSV is not currently present in this workspace.
- No application code or database has been created yet.

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

## Decisions still requiring real-data validation

1. The explicit type of every column across the complete CSV.
2. Whether `domain` is unique enough to be a natural identifier.
3. Which columns and filter combinations are used most frequently.
4. Search semantics for columns other than `title`, `description`, and `domain`.
5. The complete set of columns that semantically contain colon-delimited lists,
   as opposed to scalar values that happen to contain colons.

These are validation items, not blockers for scaffolding the ingestion profiler
or the initial application.
