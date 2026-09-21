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
- The workspace was not a Git repository when these notes were written.
- No application code or database has been created yet.

## Current recommendation in one sentence

Build a local React/TypeScript interface backed by a single-process FastAPI
service and a persistent DuckDB database, with all dataset-wide filtering,
sorting, pagination, faceting, and CSV generation performed on the server.

## Decisions still requiring real-data validation

1. The explicit type of every column across the complete CSV.
2. Whether `domain` is unique enough to be a natural identifier.
3. Which columns and filter combinations are used most frequently.
4. Whether keyword "search" means token-based full-text search, substring
   matching, exact domain/handle lookup, or a combination of these.
5. Which colon-delimited columns need normalized child tables.
6. Expected maximum export size and acceptable query latency.

These are validation items, not blockers for scaffolding the ingestion profiler
or the initial application.

