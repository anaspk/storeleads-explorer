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
