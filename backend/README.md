# Backend

FastAPI application and, in later phases, DuckDB ingestion and query services.

Run locally with:

```bash
uv sync --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Profile a CSV

The Phase 1 profiler reads the source CSV without modifying it. It stages data
in a temporary DuckDB database and writes timestamped JSON and Markdown reports:

```bash
uv run python scripts/profile_csv.py /path/to/storeleads.csv \
  --output-dir ../data/profiles \
  --memory-limit 4GB
```

Scratch data is removed after the run. Reports under `data/` are intentionally
ignored by Git because they can contain source-derived values.

## Import the profiled CSV

The Phase 2 importer validates the exact profiled schema, stages and transforms
the source, creates normalized collection tables, records import metadata, and
only then atomically activates the new database:

```bash
uv run python -m app.cli import-csv \
  /Users/muhammadanas/projects/storeleads-clone-misc/storeleads-woo-all-WORKING.csv \
  --database ../data/storeleads.duckdb \
  --memory-limit 4GB
```

The source CSV is read-only. Malformed rows are not skipped. Failed type or
money conversions, schema drift, a domain invariant failure, or a formerly
empty omitted column becoming populated aborts the import and leaves any
existing database untouched.

## Benchmark representative queries

The Phase 3 suite chooses predicates from the imported data, measures one cold
connection run plus three warm runs of eleven representative workloads, uses
`EXPLAIN ANALYZE` for slow cases, and writes dated JSON and Markdown reports:

```bash
uv run python -m app.cli benchmark \
  --database ../data/storeleads.duckdb \
  --output-dir ../data/benchmarks \
  --memory-limit 4GB
```

"Cold" means the first execution on a new DuckDB connection; the suite does
not require privileged operating-system cache eviction. Export benchmarks use
DuckDB `COPY` into temporary files, record file size and the process peak-RSS
delta, and delete the files after each measurement.

## Query the imported data

Phase 4 adds `GET /api/schema`, `POST /api/query`, and `POST /api/facets`. Start
the server after importing the database, then inspect the schema registry at
<http://127.0.0.1:8000/api/schema> or the interactive API documentation at
<http://127.0.0.1:8000/docs>.

The server reads `../data/storeleads.duckdb` by default. Override it when
needed:

```bash
STORELEADS_DATABASE=/absolute/path/to/storeleads.duckdb \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Example query:

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  --data '{
    "columns": ["domain", "country_code", "estimated_monthly_visits"],
    "filters": [
      {"column": "country_code", "operator": "in", "value": ["US", "CA"]}
    ],
    "sort": [
      {"column": "estimated_monthly_visits", "direction": "desc"}
    ],
    "limit": 100
  }'
```

Treat `next_cursor` as opaque and resend it with the same sort. The complete
contract is documented in [Query API](../docs/implementation/query-api.md).
