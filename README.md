# Store Leads Explorer

A local, single-user application for exploring, filtering, and exporting a
large Store Leads dataset. The project uses FastAPI and DuckDB on the backend,
with React, TypeScript, and Vite on the frontend.

The implementation plan and architecture decisions live in [`docs/`](docs/README.md).

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer
- [pnpm](https://pnpm.io/)

## Set up the backend

```bash
cd backend
uv sync --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The health check is available at <http://127.0.0.1:8000/api/health>. The
data endpoints include `GET /api/schema`, `POST /api/query`, `POST /api/facets`,
and the `/api/exports` job lifecycle; their contracts are documented in
[`docs/implementation/query-api.md`](docs/implementation/query-api.md) and
[`docs/implementation/export-jobs.md`](docs/implementation/export-jobs.md).

## Set up the frontend

In a second terminal:

```bash
cd frontend
pnpm install
pnpm dev
```

Open <http://127.0.0.1:5173>. During development, Vite proxies `/api` requests
to the backend on port 8000. The explorer restores the last workspace and named
saved views from browser local storage.

## Quality checks

```bash
cd backend
uv run pytest
uv run ruff check .

cd ../frontend
pnpm typecheck
pnpm build
```

## Data and exports

`data/first_100_rows.csv` is the small profiling sample documented in the
project notes. Other CSV files, DuckDB databases, Parquet files, temporary
files, and generated exports are intentionally ignored by Git. Keep the full
source dataset outside version control.

Build the local database from the profiled source with:

```bash
cd backend
uv run python -m app.cli import-csv \
  /path/to/full.csv \
  --database ../data/storeleads.duckdb
```

The importer only reads the CSV and atomically activates a separately built and
validated DuckDB database.

Run the Phase 3 query and export benchmarks with:

```bash
cd backend
uv run python -m app.cli benchmark \
  --database ../data/storeleads.duckdb \
  --output-dir ../data/benchmarks
```

Completed, failed, and cancelled export jobs are retained for seven days by
default. Remove older jobs and their CSV files with:

```bash
cd backend
uv run python -m app.cli cleanup-exports --export-dir ../exports --retention-days 7
```
