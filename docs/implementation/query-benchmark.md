# Query Benchmark Results

Last updated: 2026-09-22

## Result

Phase 3 is complete. All representative interactive queries met the five-second
acceptance target on the intended machine. The exact domain lookup was well
below 500 ms, and DuckDB streamed both the common 10,000-row export and the
complete 4,053,656-row dataset directly to CSV without materializing result rows
in Python.

No additional ART indexes or physical reordering are justified by the measured
latency. The current bridge-table model is retained. A dedicated full-text
index is also deferred: boundary-aware token matching over `title` and
`description` remained comfortably within the target. Revisit these decisions
if real usage introduces different predicates, ranking requirements, or higher
concurrency.

## Run context

- Apple silicon macOS machine with 10 logical CPUs
- Python 3.13.3 and DuckDB 1.5.5
- 4 GB DuckDB memory limit
- 6,420,443,136-byte database containing 4,053,656 stores
- One first-run timing on a new connection and three warm repetitions
- Operating-system file caches were not forcibly flushed; "cold" below means
  connection-cold, not disk-cache-cold

The suite selected predicates from the imported data so it remains useful for a
future refreshed dataset. The run used the most common country/status pair,
the median-to-upper-quartile visit range, the most common technology, a token
sampled from populated title/description text, and a substring sampled from an
existing domain.

## Timings

| Workload | Cold | Warm median | Result |
| --- | ---: | ---: | ---: |
| Exact domain lookup | 0.004 s | <0.001 s | 1 row |
| Country/status equality | 0.032 s | 0.016 s | 100 rows |
| Visits numeric range | 0.020 s | 0.010 s | 100 rows |
| Boolean plus numeric | 0.019 s | 0.009 s | 100 rows |
| Technology membership | 0.196 s | 0.142 s | 100 rows |
| Title/description token search | 0.614 s | 0.501 s | 100 rows |
| Domain substring search | 0.038 s | 0.034 s | 4 rows |
| Broad result sorted by visits | 0.015 s | 0.021 s | 100 rows |
| Multi-filter facet counts | 0.013 s | 0.010 s | 1 facet row |
| Direct 10,000-row CSV export | 0.026 s | 0.037 s | 973,105 bytes |
| Direct full-dataset CSV export | 1.634 s | 1.615 s | 353,031,142 bytes |

No query crossed the five-second slow-query threshold, so this run produced no
`EXPLAIN ANALYZE` captures. The suite automatically records a plan in both
reports whenever a cold or warm execution reaches that threshold.

## Memory and export decision

The 10,000-row export increased the process peak-RSS high-water mark by about
128 KiB. The full 4,053,656-row export increased it by about 28 MiB. This
supports using DuckDB `COPY` for exports instead of loading rows into Python.
The temporary benchmark CSVs were deleted after measurement.

The background-execution threshold was not reached by row count alone: even the
full-dataset export completed in under two seconds for the benchmark projection.
Phase 6 should still retain the export-job abstraction for progress, failure,
cancellation, and future expensive filters, but it does not need to route the
normal 10,000-row case to a background worker based on these results.

Technology membership had the largest observed query peak-RSS delta (about
1.33 GiB) despite low latency. It remains within the configured 4 GB limit, so
no index is added now, but memory should be rechecked when realistic concurrent
query behavior is tested.

## Repeat the benchmark

```bash
cd backend
uv run python -m app.cli benchmark \
  --database ../data/storeleads.duckdb \
  --output-dir ../data/benchmarks \
  --memory-limit 4GB \
  --large-export-rows 4053656
```

The dated JSON report preserves exact timings, selected predicates, environment
metadata, output sizes, RSS deltas, and any slow-query plans. The Markdown
report provides the corresponding readable summary. Reports under `data/` are
ignored because they include source-derived predicate values.
