# Export Jobs

Last updated: 2026-09-22

## Result

Phase 6 adds durable, asynchronous CSV export jobs. The explorer submits the
current validated filters, sort, and selected columns, polls ordinary job
status, supports cancellation, and offers a download only after completion.
There is no result-row cap or large-export warning.

DuckDB writes query results directly to an `exports/<export_id>.partial` file.
The worker atomically renames that file to `.csv` only after `COPY` succeeds,
so a partial result is never downloadable. One in-process worker serializes
exports for the single-user deployment. Redis or another external queue is not
required.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/exports` | Validate, persist, and queue an export |
| `GET /api/exports/{export_id}` | Read status, counts, size, or an error |
| `POST /api/exports/{export_id}/cancel` | Cancel a queued or running export |
| `GET /api/exports/{export_id}/download` | Download a completed CSV |

The create request accepts `columns`, `filters`, and `sort` with the same
allowlists and value validation as `POST /api/query`. Pagination is deliberately
absent: an export always covers the complete filtered result.

Every job persists an atomic JSON metadata file containing its validated query,
columns, timestamps, lifecycle status, output path, row count, byte size, and
failure message. Jobs left queued or running after an unclean process exit are
queued again at startup. Graceful shutdown cancels active jobs and removes
partial files.

## Storage and retention

The default directory is `exports/`. Override it with
`STORELEADS_EXPORT_DIR=/absolute/path`. Generated metadata and CSV files are
ignored by Git.

The retention rule is: keep terminal jobs for seven days by default, then
remove their metadata and output together. Cleanup is explicit so files do not
disappear while the application is being used:

```bash
cd backend
uv run python -m app.cli cleanup-exports \
  --export-dir ../exports \
  --retention-days 7
```

Only `completed`, `failed`, and `cancelled` jobs older than the cutoff are
removed. Queued and running jobs are never removed by cleanup.

## Verification

Automated tests cover query persistence, allowlist reuse, CSV content and
ordering, row and byte counts, download gating, actionable worker failures,
atomic partial-file handling, queued cancellation, and retention cleanup.
