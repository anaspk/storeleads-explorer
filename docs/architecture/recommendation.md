# Architecture Recommendation

Last updated: 2026-09-22

## Context

The application is for one person's use, requires no authentication, and needs
to search, filter, sort, paginate, and export a mostly analytical dataset of more
than 4 million rows, 162 columns, and approximately 4.8 GB in CSV form.

## Decision summary

| Layer | Recommendation | Purpose |
| --- | --- | --- |
| Database | DuckDB, persistent local file | Columnar analytical queries and direct CSV export |
| Backend | Python 3.12+ and FastAPI | Validated query API and export orchestration |
| Database access | DuckDB Python client directly | Keep analytical SQL explicit; avoid ORM impedance |
| Frontend | React, TypeScript, and Vite | Flexible filter builder and wide-table UX |
| Table state | TanStack Table | Server-controlled sorting, filtering, and pagination |
| Data fetching | TanStack Query | Request lifecycle, caching, and stale-response handling |
| UI components | shadcn/ui or equivalent | Accessible controls without building primitives from scratch |
| Deployment | Local single process bound to `127.0.0.1` | Matches personal-use and DuckDB concurrency model |

## Why DuckDB

This workload is read-heavy, scan-heavy, and analytical. DuckDB provides:

- embedded operation without a separate database server;
- columnar storage for queries that touch only some of 162 columns;
- direct CSV and Parquet ingestion;
- direct query-result export to CSV;
- predicate and projection pushdown;
- automatically maintained min-max indexes (zonemaps);
- optional ART indexes for point and very highly selective lookups; and
- an optional full-text-search extension.

The primary application store should be a native DuckDB table in a file such as
`data/storeleads.duckdb`. Keep the source CSV unchanged. An additional Parquet
copy is useful for archival or interchange, but is not required for the first
version.

Relevant official documentation:

- [CSV import](https://duckdb.org/docs/stable/data/csv/overview)
- [Parquet filter and projection pushdown](https://duckdb.org/docs/lts/data/parquet/overview)
- [Indexes](https://duckdb.org/docs/current/sql/indexes)
- [Concurrency](https://duckdb.org/docs/current/connect/concurrency)
- [Full-text search](https://duckdb.org/docs/stable/guides/sql_features/full_text_search)
- [COPY/export](https://duckdb.org/docs/lts/sql/statements/copy)

## Database design

### Main table

Use a wide `stores` table containing typed scalar fields and original string
representations needed for faithful export. Add a generated `store_id`; do not
make `domain` the primary key until uniqueness has been tested on the full file.

### Bridge tables

Normalize every column that is confirmed to encode a colon-delimited collection.
Likely examples include technologies, features, installed applications,
shipping carriers, sales channels, categories, aliases, and cluster domains.
The full-file profiler must confirm this list and the delimiter semantics before
ingestion code is finalized.

Use a separate child table for each collection so values can be typed, indexed,
faceted, and joined without substring scans. Preserve the original scalar text
on `stores` for provenance and faithful exports. A value containing a colon is
not automatically a collection: URLs and free-form prose are counterexamples.

### Indexes

Start with the automatic zonemaps. Add a unique index on `store_id` and consider
an index on `domain` after profiling. Create additional ART indexes only after a
representative benchmark demonstrates a benefit. They are meant for point or
very selective predicates and do not generally accelerate sorting or aggregate
queries.

### Physical ordering

If testing reveals a dominant filter pattern, a physically ordered table can
improve zonemap pruning. A possible order might begin with a common categorical
field and a common range field, but it must be chosen from real usage rather
than guessed. No single physical order will optimize all 162 columns.

### Text search

The initial search contract is:

- `title` and `description`: token-based full-text search;
- `domain`: substring matching; and
- other columns: no global-search behavior until decided individually.

Do not implement an unqualified substring search across all 162 columns. It
would be expensive and produce low-quality results.

The initial full-text index should cover `title` and `description`. Domain search
should use substring semantics independently of that index. DuckDB full-text
indexes do not update automatically, so rebuild the index after each dataset
refresh. That is acceptable for a batch-loaded, mostly static personal dataset.

## Backend design

Keep DuckDB access in the FastAPI process. This fits DuckDB's single-process
read/write model and avoids cross-process file-locking complications.

The API should accept a structured filter abstract syntax tree, not raw SQL.
For example:

```json
{
  "columns": ["domain", "country_code", "estimated_monthly_visits"],
  "filters": [
    {"column": "country_code", "operator": "in", "value": ["US", "CA"]},
    {"column": "estimated_monthly_visits", "operator": "gte", "value": 100000}
  ],
  "sort": [
    {"column": "estimated_monthly_visits", "direction": "desc"}
  ],
  "limit": 100,
  "cursor": null
}
```

The backend must allowlist column names, sort directions, and the operators
available for each data type. Values should be bound parameters. Since SQL
identifiers cannot be ordinary bound parameters, identifiers must come only
from the server-side allowlist.

Suggested endpoints:

```text
GET  /api/health
GET  /api/schema
POST /api/query
POST /api/facets
POST /api/exports
GET  /api/exports/{export_id}
GET  /api/exports/{export_id}/download
```

### Pagination and counts

Use server-side pagination. Prefer cursor/keyset pagination for deep traversal
when the requested order can be made deterministic with `store_id` as a final
tie-breaker. Offset pagination is acceptable for an initial shallow browsing
experience but should not be the only approach for deep result sets.

Exact `COUNT(*)` and facet calculations can be more expensive than returning
the first result page. Make counts cancellable, cached, or explicitly requested
if benchmarks show noticeable latency.

The target for an interactive page query, including its filters and sorting, is
under five seconds on the owner's machine. Faster responses are desirable, but
sub-five-second performance is the acceptance threshold for the first release.

### Export path

Let DuckDB serialize exports directly:

```sql
COPY (
    SELECT domain, country_code, estimated_monthly_visits
    FROM stores
    WHERE country_code = $country
    ORDER BY estimated_monthly_visits DESC
)
TO 'exports/result.partial.csv'
WITH (HEADER, DELIMITER ',');
```

The final implementation must use validated query construction and parameter
binding. Write to a temporary filename, rename after success, and expose job
status for exports large enough to outlive an HTTP request. Do not materialize
the complete result as Python objects.

The expected common case is no more than 10,000 exported rows. Optimize and test
that path first. A synchronous export may be acceptable if benchmarks show it
comfortably completes within normal HTTP timeouts; retain the job abstraction so
larger or slower exports can run asynchronously.

Exports must not have a hard row cap and must not show a warning merely because
they exceed 10,000 rows. The 10,000-row figure is a common-case performance
target, not a product limit. Longer completion times are acceptable for larger
exports, and the UI should report ordinary progress/status without presenting
the export size as an error condition.

## Frontend design

All dataset-wide operations belong on the server. The browser should receive a
small page, usually 50–200 rows, plus the metadata required to render it.

The initial interface should provide:

- type-aware filter controls;
- multi-column sorting;
- a searchable column picker;
- column visibility and ordering;
- saved views stored locally;
- export-column selection;
- clear active-filter summaries; and
- progress/status for long exports.

With 162 columns, a curated default view is essential. The interface should not
render all columns by default.

TanStack's guidance supports keeping sorting, filtering, pagination, and
faceting consistently server-side when the browser receives only a subset of
the complete data:

- [Client-side vs. server-side guide](https://tanstack.com/table/latest/docs/guide/client-side-vs-server-side)

## Alternatives and triggers to reconsider

### Streamlit

Good for a quick proof of concept. Prefer it only if speed of prototyping matters
more than a polished filter builder, flexible wide-table behavior, and durable
export workflows.

### PostgreSQL

Prefer PostgreSQL if the system gains multiple concurrent application workers,
frequent online mutations, multiple users, or continuously updated text-search
requirements. PostgreSQL offers mature B-tree, GIN, full-text, and extension
indexing, but adds server administration and is not necessary for the current
single-user batch-loaded workload.

### ClickHouse

Consider ClickHouse if the data grows by one or two orders of magnitude, query
concurrency becomes high, or continuous high-volume ingestion is introduced.
It would be operational overkill for the stated local workload.

### SQLite

SQLite is excellent for transactional embedded applications, but its row-store
layout is a less natural fit for repeated analytical scans over a wide table.
DuckDB is the better default here.

## Operational notes

- Keep the application bound to localhost even though it has no authentication.
- Keep the database on a local SSD rather than a network-mounted filesystem.
- Budget roughly 15–25 GB of free space during development for the source CSV,
  database, temporary files, optional raw staging data, indexes, and exports.
- Benchmark interactive queries against a five-second acceptance threshold and
  the normal export path at 10,000 rows.
- Allow exports of any result size; route long-running work through the export
  job path instead of rejecting or warning about it.
- Record the source checksum, import timestamp, row count, rejection count, and
  schema version for every dataset load.
- Preserve the original CSV until the typed import is verified.
