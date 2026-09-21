# Complete CSV Profile

Last updated: 2026-09-22

## Source and run

Phase 1 profiled the complete source at:

```text
/Users/muhammadanas/projects/storeleads-clone-misc/storeleads-woo-all-WORKING.csv
```

The source was opened read-only. Its size, inode, and modification time were
unchanged across the run.

| Measurement | Result |
| --- | ---: |
| File size | 4,739,476,938 bytes |
| SHA-256 | `810c8809ee3dce6846b78d782a7ae24fd3cf77f4fc51b3d38766767c224888cf` |
| Accepted data rows | 4,053,656 |
| Columns | 162 |
| Parser rejections | 0 |
| UTF-8 valid | yes |
| UTF-8 BOM | no |
| Full profiling runtime | 99.66 seconds |
| DuckDB version | 1.5.5 |
| Configured memory limit | 4 GB |

The byte-level line-ending inspection counted 4,109,280 LF sequences, no CRLF
sequences, and 65 isolated CR bytes. The difference between physical line
sequences and data rows is expected for quoted fields containing embedded line
breaks.

The complete dated artifacts are local and intentionally ignored by Git because
they include representative source-derived values:

```text
data/profiles/storeleads-profile-20260921T222754874459Z.json
data/profiles/storeleads-profile-20260921T222754874459Z.md
```

## Domain identity

`domain` is populated and unique in the complete file:

| Measurement | Result |
| --- | ---: |
| Null or empty | 0 |
| Exact distinct | 4,053,656 |
| Duplicate rows | 0 |
| Case/whitespace-normalized distinct | 4,053,656 |

The application should still generate a stable `store_id`. The source-domain
result supports adding a unique constraint or index to `domain` during
ingestion, while `store_id` keeps internal relationships independent of a
source-data business rule.

## Proposed type summary

Every column has an explicit proposed type in the machine-readable report.

| Type | Columns |
| --- | ---: |
| `BIGINT` | 35 |
| `BOOLEAN` | 2 |
| `DATE` | 3 |
| `DOUBLE` | 7 |
| `VARCHAR` | 115 |

No value failed conversion to its proposed `BIGINT`, `BOOLEAN`, `DATE`, or
`DOUBLE` type.

Seventeen columns are entirely empty in this source. Phase 2 omits them from
the application database and fails an import if a later source populates any of
them. See [Ingestion Schema Decisions](ingestion-schema.md) for the rationale
and migration rule:

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

## Money fields

Both sales fields should retain their original string and produce derived
`currency VARCHAR` and `amount DECIMAL(20, 2)` values during ingestion.

| Column | Non-empty | Parse failures | Currencies | Amount range |
| --- | ---: | ---: | --- | ---: |
| `estimated_monthly_sales` | 4,053,656 | 0 | USD only | 50.00–173,661,929.01 |
| `estimated_yearly_sales` | 4,053,656 | 0 | USD only | 600.00–2,083,943,148.12 |

## Confirmed collection fields

The following fields are confirmed collections and should receive normalized
child tables while retaining their raw value on `stores`:

| Column | Split rule |
| --- | --- |
| `aliases` | literal `:` |
| `categories` | literal `:` |
| `cluster_domains` | literal `:` |
| `emails` | literal `:` |
| `features` | literal `:` |
| `installed_apps` | `:` only when followed by the next `http://` or `https://` URL |
| `installed_apps_names` | literal `:` |
| `phones` | literal `:` |
| `sales_channels` | literal `:` |
| `shipping_carriers` | literal `:` |
| `tags` | literal `:` |
| `technologies` | literal `:` |

`installed_apps` must not be split on every colon because each URL contains a
scheme colon. Its effective delimiter is `:(?=https?://)`.

## Colon-containing scalar fields

URL fields are scalar even though their values contain scheme or port colons.
The following additional fields were manually reviewed and must not be split on
colon:

- `company_location`
- `description`
- `last_platform` (`xt:Commerce` is a scalar platform name)
- `last_theme`
- `linkedin_account`
- `merchant_name`
- `meta_description`
- `street_address`
- `theme`
- `title`

`company_ids` contains a mixture of genuine multi-ID values and unrelated or
dirty colon-bearing text. A literal-colon split would corrupt data, so Phase 2
should retain it as raw scalar text unless a narrower validated parser is
defined.

## Phase 2 implications

- Load all source columns as raw strings before typed transformation.
- Generate `store_id`, while enforcing the observed non-null uniqueness of
  `domain` as a load invariant.
- Derive typed sales currency and amount columns without dropping the original
  strings.
- Build one child table for each confirmed collection above.
- Apply the URL-aware rule for `installed_apps`.
- Retain the eleven reviewed non-collection fields as scalar text.
- Do not create database fields for the 17 entirely empty source columns. Fail
  validation if any becomes populated so the new data can be profiled and a
  type can be chosen deliberately.
- Fail validation if a later source changes domain uniqueness, introduces a new
  currency, or causes a typed conversion failure.
