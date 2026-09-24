"""Validated, atomic ingestion of the profiled Store Leads CSV."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

SCHEMA_VERSION = 1
DEFAULT_MEMORY_LIMIT = "4GB"
MINIMUM_DISK_HEADROOM = 256 * 1024 * 1024
DISK_SPACE_MULTIPLIER = 2.5

EXPECTED_COLUMNS = (
    "domain", "about_us_url", "aliases", "android_app_id",
    "average_product_price", "average_product_price_usd",
    "average_product_weight", "brands_page_url", "categories", "city",
    "cluster_domains", "combined_avgrating", "combined_followers",
    "combined_reviews", "common_crawl_centrality", "common_crawl_pagerank",
    "company_ids", "company_location", "contact_page_url", "country_code",
    "created", "currency", "description", "domain_count", "domain_tld1",
    "domain_url", "emails", "employee_count", "estimated_monthly_pageviews",
    "estimated_monthly_sales", "estimated_monthly_visits",
    "estimated_yearly_sales", "facebook", "facebook_group",
    "facebook_group_url", "facebook_url", "faq_page_url", "favicon_url",
    "features", "financing_page_url", "has_cms", "headless", "instagram",
    "instagram_url", "installed_apps", "installed_apps_count",
    "installed_apps_names", "ios_app_id", "judgeme_avgrating",
    "judgeme_reviews", "language_code", "last_plan", "last_plan_changed",
    "last_platform", "last_platform_changed", "last_theme",
    "last_theme_changed", "linkedin_account", "linkedin_url",
    "loox_avgrating", "loox_reviews", "maximum_product_price",
    "maximum_product_weight", "merchant_name", "meta_description",
    "meta_keywords", "minimum_product_price", "minimum_product_weight",
    "monthly_app_spend", "most_recent_product_image_url",
    "most_recent_product_title", "most_recent_product_url", "notes",
    "okendo_avgrating", "okendo_reviews", "open_graph_image_url", "phones",
    "pinterest", "pinterest_followers", "pinterest_followers_30d",
    "pinterest_followers_90d", "pinterest_posts", "pinterest_url", "plan",
    "platform", "platform_domain", "platform_rank",
    "platform_rank_percentile", "platform_version", "product_images",
    "product_images_created_30", "product_images_created_365",
    "product_images_created_90", "product_to_vendor", "product_variants",
    "products_created_30", "products_created_365", "products_created_90",
    "products_sold", "rank", "rank_percentile", "region", "retailer_url",
    "returns_page_url", "rio_avgrating", "rio_reviews",
    "sales_channel_abound", "sales_channel_amazon", "sales_channel_chownow",
    "sales_channel_doordash", "sales_channel_ebay", "sales_channel_etsy",
    "sales_channel_grubhub", "sales_channel_postmates",
    "sales_channel_ubereats", "sales_channels", "shipping_carriers",
    "ships_to_countries", "stamped_avgrating", "stamped_reviews", "state",
    "status", "store_locator_url", "street_address", "subregion", "tags",
    "technologies", "technologies_count", "theme", "theme_change_30",
    "theme_change_90", "theme_spend", "theme_style", "theme_vendor",
    "tiktok", "tiktok_followers", "tiktok_followers_30d",
    "tiktok_followers_90d", "tiktok_url", "title", "tracking_page_url",
    "trustpilot_avgrating", "trustpilot_reviews", "twitter",
    "twitter_followers", "twitter_followers_30d", "twitter_followers_90d",
    "twitter_posts", "twitter_url", "vendor_count", "warranty_page_url",
    "whatsapp", "whatsapp_url", "yotpo_avgrating", "yotpo_reviews",
    "youtube", "youtube_channel_url", "youtube_followers",
    "youtube_followers_30d", "youtube_followers_90d", "youtube_url", "zip",
)

DROPPED_EMPTY_COLUMNS = (
    "average_product_price",
    "average_product_price_usd",
    "average_product_weight",
    "maximum_product_price",
    "maximum_product_weight",
    "minimum_product_price",
    "minimum_product_weight",
    "monthly_app_spend",
    "most_recent_product_image_url",
    "most_recent_product_title",
    "most_recent_product_url",
    "notes",
    "plan",
    "platform_domain",
    "ships_to_countries",
    "theme_spend",
    "vendor_count",
)

TYPED_COLUMNS = {
    "combined_avgrating": "DOUBLE",
    "combined_followers": "BIGINT",
    "combined_reviews": "BIGINT",
    "common_crawl_centrality": "BIGINT",
    "common_crawl_pagerank": "BIGINT",
    "created": "DATE",
    "domain_count": "BIGINT",
    "employee_count": "BIGINT",
    "estimated_monthly_pageviews": "BIGINT",
    "estimated_monthly_visits": "BIGINT",
    "has_cms": "BOOLEAN",
    "headless": "BOOLEAN",
    "installed_apps_count": "BIGINT",
    "last_platform_changed": "DATE",
    "last_theme_changed": "DATE",
    "loox_avgrating": "DOUBLE",
    "loox_reviews": "BIGINT",
    "pinterest_followers": "BIGINT",
    "pinterest_posts": "BIGINT",
    "platform_rank": "BIGINT",
    "platform_rank_percentile": "DOUBLE",
    "product_images": "BIGINT",
    "product_images_created_30": "BIGINT",
    "product_images_created_365": "BIGINT",
    "product_images_created_90": "BIGINT",
    "product_to_vendor": "DOUBLE",
    "product_variants": "BIGINT",
    "products_created_30": "BIGINT",
    "products_created_365": "BIGINT",
    "products_created_90": "BIGINT",
    "products_sold": "BIGINT",
    "rank": "BIGINT",
    "rank_percentile": "DOUBLE",
    "rio_avgrating": "DOUBLE",
    "rio_reviews": "BIGINT",
    "technologies_count": "BIGINT",
    "theme_change_30": "BIGINT",
    "theme_change_90": "BIGINT",
    "tiktok_followers": "BIGINT",
    "tiktok_followers_90d": "BIGINT",
    "trustpilot_avgrating": "DOUBLE",
    "trustpilot_reviews": "BIGINT",
    "twitter_followers": "BIGINT",
    "twitter_followers_90d": "BIGINT",
    "twitter_posts": "BIGINT",
    "youtube_followers": "BIGINT",
    "youtube_followers_90d": "BIGINT",
}

COLLECTION_COLUMNS = {
    "aliases": "literal_colon",
    "categories": "literal_colon",
    "cluster_domains": "literal_colon",
    "emails": "literal_colon",
    "features": "literal_colon",
    "installed_apps": "url_aware_colon",
    "installed_apps_names": "literal_colon",
    "phones": "literal_colon",
    "sales_channels": "literal_colon",
    "shipping_carriers": "literal_colon",
    "tags": "literal_colon",
    "technologies": "literal_colon",
}

MONEY_COLUMNS = ("estimated_monthly_sales", "estimated_yearly_sales")
_MEMORY_LIMIT_RE = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?(?:KB|MB|GB|TB)$", re.I)


class ImportError(RuntimeError):
    """Raised when an import cannot be validated and activated."""


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class ImportResult:
    database: Path
    row_count: int
    source_sha256: str
    duration_seconds: float
    collection_row_counts: dict[str, int]


def _snapshot(path: Path) -> FileSnapshot:
    stat = path.stat()
    return FileSnapshot(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        try:
            return next(csv.reader(handle))
        except StopIteration as error:
            raise ImportError("Source CSV is empty") from error


def _cast_expression(column: str, target_type: str) -> str:
    identifier = _quote_identifier(column)
    trimmed = f"trim({identifier})"
    if target_type == "BOOLEAN":
        return (
            f"CASE WHEN lower({trimmed}) IN ('true','t','yes','1') THEN true "
            f"WHEN lower({trimmed}) IN ('false','f','no','0') THEN false END"
        )
    if target_type == "DATE":
        return (
            f"coalesce(try_strptime({trimmed}, '%Y/%m/%d'), "
            f"try_strptime({trimmed}, '%Y-%m-%d'))::DATE"
        )
    return f"try_cast({trimmed} AS {target_type})"


def _money_amount_expression(column: str) -> str:
    identifier = _quote_identifier(column)
    return (
        "try_cast(replace(regexp_extract(trim("
        f"{identifier}), '[+-]?[0-9][0-9,]*(\\.[0-9]+)?$', 0), ',', '') "
        "AS DECIMAL(20, 2))"
    )


def _money_currency_expression(column: str) -> str:
    identifier = _quote_identifier(column)
    return f"upper(substr(trim({identifier}), 1, 3))"


def _non_empty(column: str) -> str:
    identifier = _quote_identifier(column)
    return f"{identifier} IS NOT NULL AND trim({identifier}) <> ''"


def _validate_paths(source: Path, database: Path) -> tuple[Path, Path, FileSnapshot]:
    source = source.expanduser().resolve(strict=True)
    database = database.expanduser().resolve()
    if not source.is_file():
        raise ImportError(f"Input is not a regular file: {source}")
    if source == database:
        raise ImportError("Source CSV and destination database must differ")
    database.parent.mkdir(parents=True, exist_ok=True)
    if not database.parent.is_dir():
        raise ImportError(f"Database parent is not a directory: {database.parent}")

    header = _read_header(source)
    if header != list(EXPECTED_COLUMNS):
        missing = sorted(set(EXPECTED_COLUMNS) - set(header))
        extra = sorted(set(header) - set(EXPECTED_COLUMNS))
        raise ImportError(
            "CSV header does not match the profiled 162-column schema; "
            f"missing={missing}, extra={extra}, "
            f"order_matches={not missing and not extra}"
        )

    snapshot = _snapshot(source)
    free_bytes = shutil.disk_usage(database.parent).free
    required_bytes = max(
        MINIMUM_DISK_HEADROOM, int(snapshot.size * DISK_SPACE_MULTIPLIER)
    )
    if free_bytes < required_bytes:
        raise ImportError(
            f"Insufficient free space: {free_bytes:,} bytes available, "
            f"at least {required_bytes:,} required"
        )
    return source, database, snapshot


def _create_raw_table(connection: duckdb.DuckDBPyConnection, source: Path) -> None:
    connection.execute(
        f"""
        CREATE TEMP TABLE raw_import AS
        SELECT * FROM read_csv(
            {_sql_string(str(source))},
            header = true,
            all_varchar = true,
            auto_detect = true,
            ignore_errors = false,
            strict_mode = true,
            null_padding = false,
            allow_quoted_nulls = false
        )
        """
    )


def _scalar_validation(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[int, list[tuple[str, str, int]]]:
    expressions = ["count(*)"]
    for column in DROPPED_EMPTY_COLUMNS:
        expressions.append(f"count(*) FILTER (WHERE {_non_empty(column)})")
    for column, target_type in TYPED_COLUMNS.items():
        cast = _cast_expression(column, target_type)
        expressions.append(
            f"count(*) FILTER (WHERE {_non_empty(column)} AND ({cast}) IS NULL)"
        )
    for column in MONEY_COLUMNS:
        amount = _money_amount_expression(column)
        currency = _money_currency_expression(column)
        expressions.extend(
            [
                f"count(*) FILTER (WHERE {_non_empty(column)} AND ({amount}) IS NULL)",
                "count(*) FILTER "
                f"(WHERE {_non_empty(column)} AND ({currency}) <> 'USD')",
            ]
        )
    query = "SELECT " + ", ".join(expressions) + " FROM raw_import"
    row = connection.execute(query).fetchone()
    assert row is not None
    offset = 1
    populated_dropped = {
        column: row[offset + index]
        for index, column in enumerate(DROPPED_EMPTY_COLUMNS)
        if row[offset + index]
    }
    if populated_dropped:
        raise ImportError(
            "Columns intentionally omitted from the database are no longer empty: "
            + json.dumps(populated_dropped, sort_keys=True)
        )
    offset += len(DROPPED_EMPTY_COLUMNS)
    failures: list[tuple[str, str, int]] = []
    for index, (column, target_type) in enumerate(TYPED_COLUMNS.items()):
        failures.append((column, target_type, row[offset + index]))
    offset += len(TYPED_COLUMNS)
    for index, column in enumerate(MONEY_COLUMNS):
        amount_failures = row[offset + index * 2]
        currency_failures = row[offset + index * 2 + 1]
        failures.append((f"{column}_amount", "DECIMAL(20,2)", amount_failures))
        failures.append((f"{column}_currency", "USD", currency_failures))
    nonzero = [item for item in failures if item[2]]
    if nonzero:
        raise ImportError(f"Typed conversion validation failed: {nonzero}")
    return row[0], failures


def _validate_domain(connection: duckdb.DuckDBPyConnection, row_count: int) -> None:
    row = connection.execute(
        """
        SELECT
            count(*) FILTER (WHERE domain IS NULL OR trim(domain) = ''),
            count(DISTINCT domain),
            count(DISTINCT lower(trim(domain)))
        FROM raw_import
        """
    ).fetchone()
    assert row is not None
    if row != (0, row_count, row_count):
        raise ImportError(
            "Domain invariant failed: expected every exact and normalized domain "
            f"to be populated and unique; observed {row} for {row_count} rows"
        )


def _create_stores(connection: duckdb.DuckDBPyConnection) -> None:
    select_items = ["cast(md5(domain) AS UUID) AS store_id"]
    for column in EXPECTED_COLUMNS:
        if column in DROPPED_EMPTY_COLUMNS:
            continue
        target_type = TYPED_COLUMNS.get(column)
        expression = (
            _cast_expression(column, target_type)
            if target_type
            else _quote_identifier(column)
        )
        select_items.append(f"{expression} AS {_quote_identifier(column)}")
        if column in MONEY_COLUMNS:
            select_items.append(
                f"{_money_currency_expression(column)} AS "
                f"{_quote_identifier(column + '_currency')}"
            )
            select_items.append(
                f"{_money_amount_expression(column)} AS "
                f"{_quote_identifier(column + '_amount')}"
            )
    connection.execute(
        "CREATE TABLE stores AS SELECT " + ", ".join(select_items) + " FROM raw_import"
    )
    connection.execute("CREATE UNIQUE INDEX stores_store_id_uq ON stores(store_id)")
    connection.execute("CREATE UNIQUE INDEX stores_domain_uq ON stores(domain)")


def _collection_list_expression(column: str, rule: str) -> str:
    identifier = _quote_identifier(column)
    if rule == "literal_colon":
        return f"string_split({identifier}, ':')"
    # DuckDB's RE2 does not support the lookahead in :(?=https?://). Replace
    # only delimiter sequences and retain the URL scheme in each value.
    sentinel = "chr(31)"
    return (
        "string_split(replace(replace("
        f"{identifier}, ':https://', {sentinel} || 'https://'), "
        f"':http://', {sentinel} || 'http://'), {sentinel})"
    )


def _create_collections(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, int]:
    control_character_count = connection.execute(
        "SELECT count(*) FROM raw_import "
        "WHERE installed_apps IS NOT NULL AND contains(installed_apps, chr(31))"
    ).fetchone()[0]
    if control_character_count:
        raise ImportError("installed_apps contains the reserved split character U+001F")

    counts: dict[str, int] = {}
    for column, rule in COLLECTION_COLUMNS.items():
        table = f"store_{column}"
        list_expression = _collection_list_expression(column, rule)
        connection.execute(
            f"""
            CREATE TABLE {_quote_identifier(table)} AS
            SELECT
                stores.store_id,
                cast(parts.ordinal AS INTEGER) AS ordinal,
                trim(parts.value) AS value
            FROM stores
            CROSS JOIN UNNEST({list_expression})
                WITH ORDINALITY AS parts(value, ordinal)
            WHERE {_quote_identifier(column)} IS NOT NULL
              AND trim({_quote_identifier(column)}) <> ''
              AND trim(parts.value) <> ''
            QUALIFY row_number() OVER (
                PARTITION BY stores.store_id, trim(parts.value)
                ORDER BY parts.ordinal
            ) = 1
            """
        )
        actual = connection.execute(
            f"SELECT count(*) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        expected = connection.execute(
            f"""
            SELECT coalesce(sum(list_count(list_filter(
                {list_expression}, item -> trim(item) <> ''
            ))), 0)::BIGINT
            FROM stores
            WHERE {_quote_identifier(column)} IS NOT NULL
              AND trim({_quote_identifier(column)}) <> ''
            """
        ).fetchone()[0]
        orphan_count = connection.execute(
            f"""
            SELECT count(*)
            FROM {_quote_identifier(table)} child
            ANTI JOIN stores USING (store_id)
            """
        ).fetchone()[0]
        if actual != expected or orphan_count:
            raise ImportError(
                f"Collection validation failed for {column}: actual={actual}, "
                f"expected={expected}, orphans={orphan_count}"
            )
        counts[column] = actual
    return counts


def _record_metadata(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: Path,
    snapshot: FileSnapshot,
    source_hash: str,
    row_count: int,
    failures: list[tuple[str, str, int]],
    collection_counts: dict[str, int],
    started_at: datetime,
) -> None:
    connection.execute(
        """
        CREATE TABLE import_metadata (
            schema_version INTEGER NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL,
            source_path VARCHAR NOT NULL,
            source_size_bytes UBIGINT NOT NULL,
            source_modified_ns UBIGINT NOT NULL,
            source_sha256 VARCHAR NOT NULL,
            source_row_count BIGINT NOT NULL,
            stores_row_count BIGINT NOT NULL,
            source_column_count INTEGER NOT NULL,
            imported_source_column_count INTEGER NOT NULL,
            dropped_empty_columns VARCHAR[] NOT NULL,
            malformed_row_policy VARCHAR NOT NULL,
            duckdb_version VARCHAR NOT NULL,
            import_started_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO import_metadata VALUES (
            ?, current_timestamp, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            SCHEMA_VERSION,
            str(source),
            snapshot.size,
            snapshot.modified_ns,
            source_hash,
            row_count,
            row_count,
            len(EXPECTED_COLUMNS),
            len(EXPECTED_COLUMNS) - len(DROPPED_EMPTY_COLUMNS),
            list(DROPPED_EMPTY_COLUMNS),
            "fail_import",
            duckdb.__version__,
            started_at,
        ],
    )
    connection.execute(
        """
        CREATE TABLE import_conversion_failures (
            column_name VARCHAR PRIMARY KEY,
            target_type VARCHAR NOT NULL,
            failure_count BIGINT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO import_conversion_failures VALUES (?, ?, ?)", failures
    )
    connection.execute(
        """
        CREATE TABLE import_collection_stats (
            source_column VARCHAR PRIMARY KEY,
            table_name VARCHAR NOT NULL,
            delimiter_rule VARCHAR NOT NULL,
            row_count BIGINT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO import_collection_stats VALUES (?, ?, ?, ?)",
        [
            (column, f"store_{column}", COLLECTION_COLUMNS[column], count)
            for column, count in collection_counts.items()
        ],
    )


def import_csv(
    source: Path,
    database: Path,
    *,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
) -> ImportResult:
    """Import one profiled Store Leads CSV and atomically activate the database."""
    if not _MEMORY_LIMIT_RE.fullmatch(memory_limit):
        raise ImportError("Memory limit must look like 512MB, 4GB, or 1.5TB")
    try:
        source, database, before = _validate_paths(source, database)
        source_hash = _sha256(source)
    except OSError as error:
        raise ImportError(str(error)) from error
    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    temporary = database.with_name(
        f".{database.name}.{uuid.uuid4().hex}.partial.duckdb"
    )
    spill = database.with_name(f".{database.name}.{uuid.uuid4().hex}.spill")
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(temporary))
        connection.execute(f"SET memory_limit = {_sql_string(memory_limit.upper())}")
        connection.execute(f"SET temp_directory = {_sql_string(str(spill))}")
        _create_raw_table(connection, source)
        row_count, failures = _scalar_validation(connection)
        _validate_domain(connection, row_count)
        _create_stores(connection)
        collection_counts = _create_collections(connection)
        stores_count = connection.execute("SELECT count(*) FROM stores").fetchone()[0]
        if stores_count != row_count:
            raise ImportError(
                "Row-count validation failed: "
                f"source={row_count}, stores={stores_count}"
            )
        _record_metadata(
            connection,
            source=source,
            snapshot=before,
            source_hash=source_hash,
            row_count=row_count,
            failures=failures,
            collection_counts=collection_counts,
            started_at=started_at,
        )
        connection.execute("CHECKPOINT")
        connection.close()
        connection = None

        after = _snapshot(source)
        if after != before:
            raise ImportError(
                "Source metadata changed during import; database not activated"
            )
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())

        os.replace(temporary, database)

        if os.name != "nt":
            directory_fd = os.open(database.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return ImportResult(
            database=database,
            row_count=row_count,
            source_sha256=source_hash,
            duration_seconds=round(time.monotonic() - started_clock, 3),
            collection_row_counts=collection_counts,
        )
    except (duckdb.Error, OSError) as error:
        raise ImportError(str(error)) from error
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        shutil.rmtree(spill, ignore_errors=True)
