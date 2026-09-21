# Sample Data Profile

Last updated: 2026-09-22

## Source and scope

The observations below come from `data/first_100_rows.csv` only.

- File size: approximately 188 KB
- Data rows: 100
- Columns: 162
- Apparent subject: websites/stores and associated commerce, traffic, social,
  company, location, theme, and technology attributes

The sample appears ordered rather than randomly selected. For example, every
sample row has `platform = WooCommerce`, and several product-related fields are
constant zero. Those facts must not be generalized to the full dataset.

## Observed data families

### Identifiers and names

Examples include:

- `domain`
- `domain_tld1`
- `merchant_name`
- `title`
- social handles such as `facebook`, `instagram`, and `twitter`
- mobile application identifiers

All 100 sample values of `domain`, `domain_tld1`, `merchant_name`, and `title`
are distinct. This does **not** establish uniqueness in the complete dataset.
A generated `store_id` should be used until full-file uniqueness is verified.

### Numeric measurements

Examples include:

- ranks and percentiles
- estimated visits and page views
- employee counts
- follower, post, and review counts
- product and technology counts
- average ratings

Many numeric columns are nullable. Whole numbers should generally use `BIGINT`
because complete-file values may exceed 32-bit integer limits. Ratings and
percentiles can use `DOUBLE` unless exact decimal semantics are required.

### Boolean fields

`has_cms` and `headless` contain `true`/`false` values throughout the sample and
are good candidates for `BOOLEAN`.

### Dates

Observed date strings use `YYYY/MM/DD`, including:

- `created`
- `last_platform_changed`
- `last_theme_changed`

They should be parsed into `DATE` values during typed ingestion. Invalid values
should be reported rather than silently causing row loss.

### Money encoded as text

`estimated_monthly_sales` and `estimated_yearly_sales` contain strings such as:

```text
USD $51,707,048.53
```

The typed model should retain the raw value and, where possible, derive:

- currency code
- numeric amount as `DECIMAL(20, 2)`

Do not assume every row uses USD until the complete file is profiled.

### URLs and free text

There are numerous URL columns and several descriptive text columns, including
`description`, `meta_description`, and `meta_keywords`. URL-shaped values should
remain strings; database URL types or URL validation would add little value to
this analytical application.

### Multi-value strings

Several columns appear to encode multiple values separated by colons:

- `technologies`
- `features`
- `installed_apps_names`
- `shipping_carriers`
- `sales_channels`
- `emails`
- `phones`
- sometimes `categories`, `aliases`, and `cluster_domains`

Some individual values, especially URLs, also contain colons. Splitting every
column on `:` would therefore be unsafe. Each candidate column needs an explicit
delimiter rule validated against the complete file.

Frequently filtered categorical collections should eventually be normalized to
bridge tables such as:

```text
store_technologies(store_id, technology)
store_features(store_id, feature)
store_installed_apps(store_id, app)
store_shipping_carriers(store_id, carrier)
```

Retain the original string in the main table for provenance and export parity.

## Missingness observed in the sample

Missingness varies widely. Some fields are populated for nearly every row,
while others are empty in all 100 sample rows. Completely empty sample columns
include several product-price, product-weight, review-provider, plan, spend,
and sales-channel fields.

This does not prove those columns are empty in the complete CSV. The final
schema must not be inferred from only these 100 rows.

## Ingestion risks

1. Type inference from an early sample may choose a narrow or incorrect type.
2. Numeric-looking identifiers, such as ZIP codes or account handles, may lose
   leading zeros if treated as numbers.
3. Currency strings cannot be cast directly to numeric values.
4. A delimiter inside URLs or prose may be mistaken for a list separator.
5. Empty strings, quoted empty strings, and textual null markers may need
   separate handling.
6. A malformed row near the end of a 4.8 GB file could otherwise abort import.
7. The first 100 rows may not contain all date formats, currencies, or outliers.

## Recommended full-file profiling output

Before finalizing the schema, generate a machine-readable report containing,
for each column:

- total and non-null row counts
- distinct count, exact or approximate as appropriate
- minimum and maximum for numeric/date candidates
- minimum, maximum, and representative string lengths
- values that fail proposed casts
- representative frequent values for categorical fields
- delimiter statistics for candidate multi-value fields

Also verify:

- total CSV row count
- parser rejection count and rejected row details
- uniqueness and nullability of `domain`
- file checksum and byte size
- file encoding and line-ending convention

## Schema strategy

Use a two-stage import during schema discovery:

1. Load a raw representation safely, treating uncertain fields as `VARCHAR`.
2. Create the typed `stores` table with explicit conversions using `TRY_CAST`
   and transformation expressions.

Once the schema is stable, this may be consolidated into a direct typed import
to reduce temporary disk use. Preserve ingestion metadata and conversion-error
counts even after consolidation.

