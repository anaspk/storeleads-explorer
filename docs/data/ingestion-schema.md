# Ingestion Schema Decisions

Last updated: 2026-09-22

## Empty source columns are omitted

Phase 1 found 17 columns with no non-empty value in any of the 4,053,656 source
rows. Phase 2 deliberately omits these columns from the `stores` table instead
of creating evidence-free `VARCHAR` fields:

- `average_product_price`
- `average_product_price_usd`
- `average_product_weight`
- `maximum_product_price`
- `maximum_product_weight`
- `minimum_product_price`
- `minimum_product_weight`
- `monthly_app_spend`
- `most_recent_product_image_url`
- `most_recent_product_title`
- `most_recent_product_url`
- `notes`
- `plan`
- `platform_domain`
- `ships_to_countries`
- `theme_spend`
- `vendor_count`

This is an explicit schema decision, not silent data loss. Every import scans
these source fields and fails before activation if any has become non-empty. A
future source version that populates one of them therefore requires profiling,
a deliberate type choice, a schema-version change, and an importer update.

The complete source header is still validated in its original order. Import
metadata records both the 162 source columns and the 145 imported source
columns, as well as the omitted-column list.

## Other Phase 2 rules

- `store_id` is a deterministic UUID derived from the exact, unique `domain`.
- Original money strings remain in `stores`; USD currency and
  `DECIMAL(20,2)` amount fields are added alongside them.
- Typed conversion failures, non-USD money values, malformed rows, empty or
  duplicate domains, and schema drift fail the import.
- The 12 confirmed collection fields retain their raw values in `stores` and
  also receive `store_<column>` child tables with `store_id`, `ordinal`, and
  `value`.
- Imports build a separate temporary DuckDB file and atomically replace the
  configured database only after validation. The CSV is opened only for
  reading, and its inode, size, and modification time are checked again before
  activation.

## Verified full import

The complete profiled source was imported twice on 2026-09-22. Both runs
produced 4,053,656 `stores` rows, the same store-ID fingerprint, zero conversion
failures, and the same source SHA-256 recorded in Phase 1. The activated DuckDB
file is approximately 6.0 GiB and has 150 `stores` columns: one generated ID,
145 retained source columns, and four derived money columns.

| Child table | Rows |
| --- | ---: |
| `store_aliases` | 2,918,581 |
| `store_categories` | 3,931,349 |
| `store_cluster_domains` | 6,016,600 |
| `store_emails` | 2,757,180 |
| `store_features` | 10,347,270 |
| `store_installed_apps` | 871,843 |
| `store_installed_apps_names` | 871,843 |
| `store_phones` | 3,607,278 |
| `store_sales_channels` | 29,488 |
| `store_shipping_carriers` | 304,748 |
| `store_tags` | 4,486 |
| `store_technologies` | 19,784,575 |
