# Query API

Last updated: 2026-09-22

## Result

Phase 4 exposes the imported DuckDB data through three strictly validated
endpoints. SQL identifiers come only from the server-owned schema registry and
all user values are bound parameters. Request models reject unknown fields, so
the API has no raw-SQL escape hatch.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/schema` | Return the 150 exposed fields and their capabilities |
| `POST /api/query` | Filter, project, sort, and cursor-paginate stores |
| `POST /api/facets` | Count enabled scalar or collection values under filters |

The default database is `data/storeleads.duckdb`. Set `STORELEADS_DATABASE` to
use another path. Queries use read-only connections and are interrupted after
five seconds by default. A request can return at most 500 rows, use five
explicit sort fields, contain 100 filter nodes, nest filters five levels, or
place 100 values in a list operator.

## Query shape

Top-level filters are combined with `AND`. A nested group can use `and` or `or`:

```json
{
  "columns": ["domain", "country_code", "estimated_monthly_visits"],
  "filters": [
    {"column": "country_code", "operator": "in", "value": ["US", "CA"]},
    {
      "combinator": "or",
      "filters": [
        {"column": "has_cms", "operator": "eq", "value": true},
        {"column": "technologies", "operator": "has", "value": "Klaviyo"}
      ]
    }
  ],
  "sort": [
    {"column": "estimated_monthly_visits", "direction": "desc"}
  ],
  "limit": 100,
  "cursor": null
}
```

The response contains only the requested columns:

```json
{
  "rows": [],
  "next_cursor": null
}
```

When `next_cursor` is present, resend the same filters and ordering with that
opaque value. The server appends `store_id ASC` as a final tie-breaker, includes
null placement in its keyset predicate, and rejects a cursor used with a
different ordering. All order fields use `NULLS LAST`.

If `columns` or `sort` is empty, the API uses the registry's curated default
columns and `domain ASC`, respectively.

## Operators and nulls

`GET /api/schema` is authoritative for the operators available on each field.
The type families currently provide:

- strings: `eq`, `neq`, `in`, `not_in`, `contains`, `not_contains`,
  `starts_with`, `ends_with`, `is_null`, and `is_not_null`;
- integers, numbers, decimals, and dates: equality, comparison, list, `between`,
  and null operators;
- booleans: `eq`, `neq`, and null operators;
- normalized collections: `has`, `has_any`, `has_all`, and null operators; and
- `title` and `description`: `matches_token` in addition to string operators.

Text pattern operators are case-insensitive and escape `%`, `_`, and `\` in
user values. `matches_token` is case-insensitive and boundary-aware. Collection
membership uses normalized child tables rather than searching raw
colon-delimited strings.

Null checks must use `is_null` or `is_not_null` and take no value. Other
operators reject a null operand. SQL three-valued logic is preserved: for
example, `neq` and `not_in` do not include null rows.

## Facets and errors

Facet requests use the same filter AST:

```json
{
  "column": "country_code",
  "filters": [
    {"column": "estimated_monthly_visits", "operator": "gte", "value": 100000}
  ],
  "limit": 20
}
```

Only fields marked `facet_enabled` in the registry are accepted. Collection
facets count distinct stores per normalized value.

Every application error uses one envelope:

```json
{
  "error": {
    "code": "invalid_column",
    "message": "Unknown column: example",
    "details": {"column": "example"}
  }
}
```

Validation errors use HTTP 422, an unavailable database uses 503, a timeout uses
504, and an unexpected database query failure uses 500.

## Verification

The automated suite covers registry completeness, nested `AND`/`OR` filters,
type validation, normalized collection membership, boundary-aware token search,
projection, deterministic cursor traversal, null semantics, facets, malformed
cursors, unavailable databases, and identifier/operator/value SQL-injection
attempts.

A full-dataset smoke test returned schema metadata, two consecutive filtered
cursor pages, and a filtered country facet successfully. The measured requests
completed in about 0.02 seconds each on the intended machine.
