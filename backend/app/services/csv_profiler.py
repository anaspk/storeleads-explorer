"""Repeatable, read-only profiling for large Store Leads CSV files."""

from __future__ import annotations

import codecs
import csv
import hashlib
import json
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

PROFILE_VERSION = 1
DEFAULT_MEMORY_LIMIT = "4GB"
DEFAULT_SAMPLE_ROWS = 5_000
DEFAULT_TOP_VALUES = 10
MAX_REJECT_DETAILS = 10_000

_MEMORY_LIMIT_RE = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?(?:KB|MB|GB|TB)$", re.I)
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_MONEY_RE = re.compile(
    r"^(?P<currency>[A-Z]{3})\s+(?:\$\s*)?"
    r"(?P<amount>[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?)$"
)
_DATE_FORMATS = ("%Y/%m/%d", "%Y-%m-%d")
_BOOLEAN_VALUES = {"true", "false", "t", "f", "yes", "no", "1", "0"}
_COLLECTION_NAME_MARKERS = (
    "technologies",
    "features",
    "installed_apps",
    "shipping_carriers",
    "sales_channels",
    "tags",
    "categories",
    "aliases",
    "cluster_domains",
    "emails",
    "phones",
)


class ProfileError(RuntimeError):
    """Raised when a reliable profile cannot be produced."""


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    size_bytes: int
    modified_ns: int


@dataclass(frozen=True)
class FileInspection:
    sha256: str
    utf8_valid: bool
    utf8_bom: bool
    line_endings: dict[str, int]


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _snapshot(path: Path) -> FileSnapshot:
    stat = path.stat()
    return FileSnapshot(
        device=stat.st_dev,
        inode=stat.st_ino,
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )


def inspect_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> FileInspection:
    """Hash and inspect a file using a read-only binary handle."""
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    utf8_valid = True
    utf8_bom = False
    crlf = 0
    bare_lf = 0
    bare_cr = 0
    previous_cr = False
    first_chunk = True

    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
            if first_chunk:
                utf8_bom = chunk.startswith(codecs.BOM_UTF8)
                first_chunk = False

            crlf += chunk.count(b"\r\n")
            bare_lf += chunk.count(b"\n")
            bare_cr += chunk.count(b"\r")
            if previous_cr and chunk.startswith(b"\n"):
                crlf += 1
            previous_cr = chunk.endswith(b"\r")

            if utf8_valid:
                try:
                    decoder.decode(chunk)
                except UnicodeDecodeError:
                    utf8_valid = False

    if utf8_valid:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            utf8_valid = False

    return FileInspection(
        sha256=digest.hexdigest(),
        utf8_valid=utf8_valid,
        utf8_bom=utf8_bom,
        line_endings={
            "crlf": crlf,
            "lf": max(0, bare_lf - crlf),
            "cr": max(0, bare_cr - crlf),
        },
    )


def infer_sample_type(column_name: str, values: list[str]) -> dict[str, Any]:
    """Infer a conservative candidate type from representative non-empty values."""
    cleaned = [value.strip() for value in values if value and value.strip()]
    lower_name = column_name.lower()
    result: dict[str, Any] = {
        "sample_non_empty": len(cleaned),
        "candidate_type": "VARCHAR",
        "reason": "free-form or mixed text",
    }
    if not cleaned:
        result["reason"] = "no non-empty sampled values"
        return result

    money_matches = [_MONEY_RE.fullmatch(value) for value in cleaned]
    if all(money_matches):
        currencies = sorted(
            {match.group("currency") for match in money_matches if match}
        )
        return {
            **result,
            "candidate_type": "VARCHAR",
            "derived_types": {"currency": "VARCHAR", "amount": "DECIMAL(20, 2)"},
            "sample_currencies": currencies,
            "reason": "money text retained verbatim with derived typed fields",
        }

    if _identifier_like_name(lower_name):
        result["reason"] = "identifier-like column name; preserve as text"
        return result

    lowered = [value.lower() for value in cleaned]
    textual_boolean = any(value not in {"0", "1"} for value in lowered)
    boolean_name = lower_name.startswith(("has_", "is_")) or lower_name in {
        "headless"
    }
    if all(value in _BOOLEAN_VALUES for value in lowered) and (
        textual_boolean or boolean_name
    ):
        return {**result, "candidate_type": "BOOLEAN", "reason": "boolean tokens"}

    if all(_INTEGER_RE.fullmatch(value) for value in cleaned):
        has_leading_zero = any(
            value.lstrip("+-").startswith("0") and len(value.lstrip("+-")) > 1
            for value in cleaned
        )
        if has_leading_zero:
            result["reason"] = "numeric-looking values include leading zeros"
            return result
        return {**result, "candidate_type": "BIGINT", "reason": "whole numbers"}

    if all(_NUMBER_RE.fullmatch(value) for value in cleaned):
        return {**result, "candidate_type": "DOUBLE", "reason": "numeric values"}

    date_matches = sum(_parse_date(value) for value in cleaned)
    date_named = any(marker in lower_name for marker in ("date", "created", "changed"))
    if date_matches == len(cleaned) or (
        date_named and date_matches > len(cleaned) / 2
    ):
        return {
            **result,
            "candidate_type": "DATE",
            "accepted_formats": list(_DATE_FORMATS),
            "reason": "ISO-like calendar dates",
        }

    return result


def _identifier_like_name(lower_name: str) -> bool:
    if lower_name in {"domain", "domain_tld1"}:
        return True
    if lower_name.endswith(
        ("_domain", "_url", "_code", "_id", "_ids", "_uuid", "_identifier")
    ):
        return True
    return any(
        marker in lower_name
        for marker in ("email", "phone", "postal", "zipcode", "zip_code", "handle")
    )


def _parse_date(value: str) -> bool:
    for date_format in _DATE_FORMATS:
        try:
            datetime.strptime(value, date_format)
        except ValueError:
            continue
        return True
    return False


def _cast_expression(identifier: str, proposed_type: str) -> str | None:
    trimmed = f"trim({identifier})"
    if proposed_type == "BOOLEAN":
        return (
            f"CASE WHEN lower({trimmed}) IN "
            "('true','t','yes','1') THEN true "
            f"WHEN lower({trimmed}) IN ('false','f','no','0') THEN false END"
        )
    if proposed_type == "BIGINT":
        return f"try_cast({trimmed} AS BIGINT)"
    if proposed_type == "DOUBLE":
        return f"try_cast({trimmed} AS DOUBLE)"
    if proposed_type == "DATE":
        return (
            f"coalesce(try_strptime({trimmed}, '%Y/%m/%d'), "
            f"try_strptime({trimmed}, '%Y-%m-%d'))::DATE"
        )
    return None


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path)):
        return value.isoformat()
    if hasattr(value, "as_integer_ratio"):
        return float(value)
    return str(value)


def _fetch_dicts(
    connection: duckdb.DuckDBPyConnection, query: str
) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _load_raw_table(
    connection: duckdb.DuckDBPyConnection,
    source: Path,
    reject_limit: int,
) -> None:
    source_literal = _sql_string(str(source))
    connection.execute(
        f"""
        CREATE TABLE raw_profile AS
        SELECT *
        FROM read_csv(
            {source_literal},
            header = true,
            all_varchar = true,
            auto_detect = true,
            ignore_errors = true,
            store_rejects = true,
            rejects_table = 'profile_csv_rejects',
            rejects_scan = 'profile_csv_scans',
            rejects_limit = {reject_limit},
            strict_mode = true,
            null_padding = false,
            allow_quoted_nulls = false
        )
        """
    )


def _column_names(connection: duckdb.DuckDBPyConnection) -> list[str]:
    rows = connection.execute("PRAGMA table_info('raw_profile')").fetchall()
    return [row[1] for row in rows]


def _sample_rows(
    connection: duckdb.DuckDBPyConnection,
    row_count: int,
    sample_rows: int,
) -> list[tuple[Any, ...]]:
    if row_count <= sample_rows:
        return connection.execute("SELECT * FROM raw_profile").fetchall()
    query = (
        f"SELECT * FROM raw_profile USING SAMPLE {int(sample_rows)} "
        "ROWS (reservoir, 8675309)"
    )
    return connection.execute(query).fetchall()


def _base_column_stats(
    connection: duckdb.DuckDBPyConnection,
    columns: list[str],
) -> list[dict[str, Any]]:
    expressions: list[str] = []
    for index, column in enumerate(columns):
        identifier = _quote_identifier(column)
        prefix = f"c{index}"
        expressions.extend(
            [
                f"count({identifier}) AS {prefix}_non_null",
                f"count(*) FILTER (WHERE {identifier} IS NULL "
                f"OR trim({identifier}) = '') "
                f"AS {prefix}_empty",
                f"approx_count_distinct({identifier}) AS {prefix}_distinct",
                f"min(length({identifier})) AS {prefix}_min_length",
                f"max(length({identifier})) AS {prefix}_max_length",
                f"avg(length({identifier})) AS {prefix}_avg_length",
                f"count(*) FILTER (WHERE contains({identifier}, ':')) "
                f"AS {prefix}_colon",
            ]
        )
    query = "SELECT " + ", ".join(expressions) + " FROM raw_profile"
    row = connection.execute(query).fetchone()
    assert row is not None
    result: list[dict[str, Any]] = []
    stride = 7
    for index, column in enumerate(columns):
        values = row[index * stride : (index + 1) * stride]
        result.append(
            {
                "name": column,
                "non_null_count": values[0],
                "null_or_empty_count": values[1],
                "approx_distinct_count": values[2],
                "string_length": {
                    "min": values[3],
                    "max": values[4],
                    "average": round(values[5], 3) if values[5] is not None else None,
                },
                "colon_value_count": values[6],
            }
        )
    return result


def _validate_casts(
    connection: duckdb.DuckDBPyConnection,
    columns: list[dict[str, Any]],
) -> None:
    candidates: list[tuple[dict[str, Any], str]] = []
    expressions: list[str] = []
    for column in columns:
        identifier = _quote_identifier(column["name"])
        cast = _cast_expression(identifier, column["proposed_type"])
        if cast is None:
            column["cast_validation"] = None
            continue
        candidates.append((column, cast))
        non_empty = f"{identifier} IS NOT NULL AND trim({identifier}) <> ''"
        expressions.extend(
            [
                f"count(*) FILTER (WHERE {non_empty})",
                f"count(*) FILTER (WHERE {non_empty} AND ({cast}) IS NULL)",
                f"min({cast})",
                f"max({cast})",
            ]
        )

    if not expressions:
        return
    query = "SELECT " + ", ".join(expressions) + " FROM raw_profile"
    row = connection.execute(query).fetchone()
    assert row is not None
    for index, (column, cast) in enumerate(candidates):
        non_empty, failures, minimum, maximum = row[index * 4 : (index + 1) * 4]
        validation = {
            "non_empty_count": non_empty,
            "failed_count": failures,
            "minimum": minimum,
            "maximum": maximum,
            "failed_examples": [],
        }
        if failures:
            identifier = _quote_identifier(column["name"])
            validation["failed_examples"] = [
                value[0]
                for value in connection.execute(
                    f"""
                    SELECT {identifier}
                    FROM raw_profile
                    WHERE {identifier} IS NOT NULL
                      AND trim({identifier}) <> ''
                      AND ({cast}) IS NULL
                    GROUP BY {identifier}
                    LIMIT 10
                    """
                ).fetchall()
            ]
        column["cast_validation"] = validation


def _validate_money(
    connection: duckdb.DuckDBPyConnection,
    columns: list[dict[str, Any]],
) -> None:
    pattern = r"^[A-Z]{3}\s+\$?[+-]?[0-9,]+(\.[0-9]+)?$"
    amount_pattern = r"[+-]?[0-9][0-9,]*(\.[0-9]+)?$"
    for column in columns:
        if "derived_types" not in column:
            column["derived_validation"] = None
            continue
        identifier = _quote_identifier(column["name"])
        trimmed = f"trim({identifier})"
        valid = f"regexp_matches({trimmed}, {_sql_string(pattern)})"
        amount = (
            "try_cast(replace(regexp_extract("
            f"{trimmed}, {_sql_string(amount_pattern)}, 0), ',', '') "
            "AS DECIMAL(20, 2))"
        )
        row = connection.execute(
            f"""
            SELECT
                count(*) FILTER (
                    WHERE {identifier} IS NOT NULL AND {trimmed} <> ''
                ),
                count(*) FILTER (
                    WHERE {identifier} IS NOT NULL AND {trimmed} <> ''
                      AND NOT {valid}
                ),
                min({amount}) FILTER (WHERE {valid}),
                max({amount}) FILTER (WHERE {valid})
            FROM raw_profile
            """
        ).fetchone()
        assert row is not None
        currencies = connection.execute(
            f"""
            SELECT upper(substr({trimmed}, 1, 3)) AS currency, count(*) AS count
            FROM raw_profile
            WHERE {identifier} IS NOT NULL AND {trimmed} <> '' AND {valid}
            GROUP BY 1
            ORDER BY count DESC, currency
            """
        ).fetchall()
        failed_examples = [
            value[0]
            for value in connection.execute(
                f"""
                SELECT {identifier}
                FROM raw_profile
                WHERE {identifier} IS NOT NULL AND {trimmed} <> ''
                  AND NOT {valid}
                GROUP BY {identifier}
                LIMIT 10
                """
            ).fetchall()
        ]
        column["derived_validation"] = {
            "non_empty_count": row[0],
            "failed_count": row[1],
            "amount_minimum": row[2],
            "amount_maximum": row[3],
            "currencies": [
                {"currency": currency, "count": count}
                for currency, count in currencies
            ],
            "failed_examples": failed_examples,
        }


def _frequent_values(
    connection: duckdb.DuckDBPyConnection,
    columns: list[dict[str, Any]],
    top_values: int,
) -> None:
    for column in columns:
        distinct = column["approx_distinct_count"]
        non_null = column["non_null_count"]
        if distinct is None or distinct > 100 or non_null == 0:
            column["frequent_values"] = []
            continue
        identifier = _quote_identifier(column["name"])
        rows = connection.execute(
            f"""
            SELECT {identifier} AS value, count(*) AS count
            FROM raw_profile
            WHERE {identifier} IS NOT NULL AND trim({identifier}) <> ''
            GROUP BY {identifier}
            ORDER BY count DESC, value
            LIMIT {int(top_values)}
            """
        ).fetchall()
        column["frequent_values"] = [
            {"value": value, "count": count} for value, count in rows
        ]


def _colon_behavior(
    connection: duckdb.DuckDBPyConnection,
    column: dict[str, Any],
    row_count: int,
) -> dict[str, Any]:
    colon_count = column["colon_value_count"]
    if not colon_count:
        return {
            "classification": "no_colons",
            "delimiter_rule": None,
            "examples": [],
        }

    name = column["name"]
    identifier = _quote_identifier(name)
    examples = [
        row[0]
        for row in connection.execute(
            f"""
            SELECT {identifier}
            FROM raw_profile
            WHERE contains({identifier}, ':')
            GROUP BY {identifier}
            ORDER BY count(*) DESC
            LIMIT 10
            """
        ).fetchall()
    ]
    url_like = sum(
        1
        for value in examples
        if re.search(r"(?:https?|ftp):/{1,2}", value, flags=re.IGNORECASE)
    )
    lower_name = name.lower()
    collection_name = any(marker in lower_name for marker in _COLLECTION_NAME_MARKERS)
    if lower_name == "installed_apps":
        classification = "likely_collection"
        reason = "URL collection; separators occur immediately before the next URL"
        delimiter_rule = r":(?=https?://)"
    elif collection_name and url_like < max(1, len(examples) // 2):
        classification = "likely_collection"
        reason = "collection-like column name and predominantly non-URL examples"
        delimiter_rule = ":"
    elif url_like:
        classification = "likely_scalar"
        reason = "representative colon values contain URL schemes"
        delimiter_rule = None
    else:
        classification = "manual_review"
        reason = "colon presence alone does not establish collection semantics"
        delimiter_rule = None
    return {
        "classification": classification,
        "reason": reason,
        "delimiter_rule": delimiter_rule,
        "value_count": colon_count,
        "row_percentage": round((colon_count / row_count) * 100, 4) if row_count else 0,
        "examples": examples,
    }


def _domain_profile(
    connection: duckdb.DuckDBPyConnection,
    columns: list[str],
    row_count: int,
) -> dict[str, Any] | None:
    if "domain" not in columns:
        return None
    row = connection.execute(
        """
        SELECT
            count(*) FILTER (WHERE domain IS NULL OR trim(domain) = ''),
            count(DISTINCT domain),
            count(DISTINCT lower(trim(domain)))
        FROM raw_profile
        """
    ).fetchone()
    assert row is not None
    duplicates = connection.execute(
        """
        SELECT domain, count(*) AS count
        FROM raw_profile
        WHERE domain IS NOT NULL AND trim(domain) <> ''
        GROUP BY domain
        HAVING count(*) > 1
        ORDER BY count DESC, domain
        LIMIT 20
        """
    ).fetchall()
    null_or_empty, exact_distinct, normalized_distinct = row
    non_empty = row_count - null_or_empty
    return {
        "null_or_empty_count": null_or_empty,
        "non_empty_count": non_empty,
        "exact_distinct_count": exact_distinct,
        "duplicate_row_count": max(0, non_empty - exact_distinct),
        "case_whitespace_normalized_distinct_count": normalized_distinct,
        "is_unique_non_empty": exact_distinct == non_empty,
        "duplicate_examples": [
            {"domain": domain, "count": count} for domain, count in duplicates
        ],
    }


def _rejection_profile(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "profile_csv_rejects" not in tables:
        return {"captured_error_count": 0, "details": [], "truncated": False}
    count = connection.execute("SELECT count(*) FROM profile_csv_rejects").fetchone()[0]
    details = _fetch_dicts(
        connection,
        "SELECT * FROM profile_csv_rejects ORDER BY line LIMIT 100",
    )
    return {
        "captured_error_count": count,
        "details": details,
        "truncated": count >= MAX_REJECT_DETAILS,
        "capture_limit": MAX_REJECT_DETAILS,
    }


def _header_profile(source: Path) -> dict[str, Any]:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    counts = Counter(header)
    duplicates = {name: count for name, count in counts.items() if count > 1}
    return {"raw_columns": header, "duplicate_names": duplicates}


def _markdown_report(profile: dict[str, Any]) -> str:
    source = profile["source"]
    parser = profile["parser"]
    domain = profile.get("domain")
    columns = profile["columns"]
    type_counts = Counter(column["proposed_type"] for column in columns)
    cast_problems = [
        column
        for column in columns
        if column.get("cast_validation")
        and column["cast_validation"]["failed_count"]
    ]
    money_columns = [
        column for column in columns if column.get("derived_validation") is not None
    ]
    collections = [
        column
        for column in columns
        if column["delimiter_behavior"]["classification"] != "no_colons"
    ]
    lines = [
        "# Full CSV Profile",
        "",
        f"Generated: {profile['run']['completed_at']}",
        "",
        "## Source",
        "",
        f"- Path: `{source['path']}`",
        f"- Size: {source['size_bytes']:,} bytes",
        f"- SHA-256: `{source['sha256']}`",
        f"- UTF-8 valid: {source['utf8_valid']}",
        f"- Source unchanged during run: {source['unchanged_during_run']}",
        "",
        "## Scan result",
        "",
        f"- Accepted data rows: {parser['accepted_row_count']:,}",
        f"- Columns: {parser['column_count']}",
        f"- Captured parser errors: {parser['rejections']['captured_error_count']:,}",
        f"- Runtime: {profile['run']['duration_seconds']:.2f} seconds",
        "",
        "## Proposed types",
        "",
    ]
    for name, count in sorted(type_counts.items()):
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Domain uniqueness", ""])
    if domain is None:
        lines.append("No `domain` column was found.")
    else:
        lines.extend(
            [
                f"- Non-empty domains: {domain['non_empty_count']:,}",
                f"- Exact distinct domains: {domain['exact_distinct_count']:,}",
                f"- Duplicate rows: {domain['duplicate_row_count']:,}",
                f"- Unique among non-empty values: {domain['is_unique_non_empty']}",
            ]
        )
    lines.extend(["", "## Failed casts", ""])
    if not cast_problems:
        lines.append("No values failed their proposed typed conversion.")
    else:
        lines.append("| Column | Proposed type | Failures | Examples |")
        lines.append("| --- | --- | ---: | --- |")
        for column in cast_problems:
            validation = column["cast_validation"]
            examples = ", ".join(
                f"`{str(value)[:60]}`" for value in validation["failed_examples"][:3]
            )
            lines.append(
                f"| `{column['name']}` | {column['proposed_type']} | "
                f"{validation['failed_count']:,} | {examples} |"
            )
    lines.extend(["", "## Money-derived fields", ""])
    if not money_columns:
        lines.append("No money-formatted columns were identified.")
    else:
        lines.append("| Column | Parse failures | Currencies | Amount range |")
        lines.append("| --- | ---: | --- | --- |")
        for column in money_columns:
            validation = column["derived_validation"]
            currencies = ", ".join(
                f"{item['currency']} ({item['count']:,})"
                for item in validation["currencies"]
            )
            amount_range = (
                f"{validation['amount_minimum']} to {validation['amount_maximum']}"
            )
            lines.append(
                f"| `{column['name']}` | {validation['failed_count']:,} | "
                f"{currencies} | {amount_range} |"
            )
    lines.extend(["", "## Colon-delimited review", ""])
    if not collections:
        lines.append("No colon-containing values were found.")
    else:
        lines.append("| Column | Classification | Rows with colons | Reason |")
        lines.append("| --- | --- | ---: | --- |")
        for column in collections:
            behavior = column["delimiter_behavior"]
            lines.append(
                f"| `{column['name']}` | {behavior['classification']} | "
                f"{behavior['value_count']:,} | {behavior['reason']} |"
            )
    lines.extend(
        [
            "",
            "## Column schema",
            "",
            "| Column | Proposed type | Non-null | Empty/null | "
            "Approx. distinct | Max length |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for column in columns:
        maximum = column["string_length"]["max"]
        lines.append(
            f"| `{column['name']}` | {column['proposed_type']} | "
            f"{column['non_null_count']:,} | {column['null_or_empty_count']:,} | "
            f"{column['approx_distinct_count']:,} | "
            f"{maximum if maximum is not None else ''} |"
        )
    lines.append("")
    return "\n".join(lines)


def profile_csv(
    source: Path,
    output_dir: Path,
    *,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    top_values: int = DEFAULT_TOP_VALUES,
) -> tuple[Path, Path, dict[str, Any]]:
    """Profile ``source`` without writing to it and return report paths and data."""
    source = source.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve()
    if not source.is_file():
        raise ProfileError(f"Input is not a regular file: {source}")
    if source == output_dir or source in output_dir.parents:
        raise ProfileError("Output directory cannot be the source file or inside it")
    if not _MEMORY_LIMIT_RE.fullmatch(memory_limit):
        raise ProfileError("Memory limit must look like 512MB, 4GB, or 1.5TB")
    if sample_rows < 1 or top_values < 1:
        raise ProfileError("Sample rows and top-values limit must be positive")

    output_dir.mkdir(parents=True, exist_ok=True)
    before = _snapshot(source)
    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    inspection = inspect_file(source)
    if not inspection.utf8_valid:
        raise ProfileError("The source is not valid UTF-8; no profile was written")
    header = _header_profile(source)

    with tempfile.TemporaryDirectory(
        prefix="storeleads-profile-", dir=output_dir
    ) as scratch:
        scratch_path = Path(scratch)
        database_path = scratch_path / "profile.duckdb"
        connection = duckdb.connect(str(database_path))
        try:
            connection.execute(f"SET memory_limit = {_sql_string(memory_limit)}")
            connection.execute(
                f"SET temp_directory = {_sql_string(str(scratch_path / 'spill'))}"
            )
            _load_raw_table(connection, source, MAX_REJECT_DETAILS)
            columns = _column_names(connection)
            if not columns:
                raise ProfileError("No CSV columns were detected")
            count_row = connection.execute(
                "SELECT count(*) FROM raw_profile"
            ).fetchone()
            assert count_row is not None
            row_count = count_row[0]
            rows = _sample_rows(connection, row_count, sample_rows)
            samples_by_column = [
                [str(row[index]) for row in rows if row[index] is not None]
                for index in range(len(columns))
            ]
            column_profiles = _base_column_stats(connection, columns)
            for index, column in enumerate(column_profiles):
                inference = infer_sample_type(column["name"], samples_by_column[index])
                column.update(inference)
                column["proposed_type"] = inference["candidate_type"]
                column["delimiter_behavior"] = _colon_behavior(
                    connection, column, row_count
                )
            _validate_casts(connection, column_profiles)
            _validate_money(connection, column_profiles)
            _frequent_values(connection, column_profiles, top_values)
            domain = _domain_profile(connection, columns, row_count)
            rejections = _rejection_profile(connection)
        finally:
            connection.close()

    after = _snapshot(source)
    unchanged = before == after
    if not unchanged:
        raise ProfileError(
            "Source metadata changed during profiling; reports were not written"
        )

    completed_at = datetime.now(UTC)
    profile: dict[str, Any] = {
        "profile_version": PROFILE_VERSION,
        "run": {
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round(time.monotonic() - started_clock, 3),
            "duckdb_version": duckdb.__version__,
            "memory_limit": memory_limit.upper(),
            "sample_rows_requested": sample_rows,
            "sample_rows_used": len(rows),
        },
        "source": {
            "path": str(source),
            "size_bytes": before.size_bytes,
            "modified_at": datetime.fromtimestamp(
                before.modified_ns / 1_000_000_000, tz=UTC
            ).isoformat(),
            "sha256": inspection.sha256,
            "utf8_valid": inspection.utf8_valid,
            "utf8_bom": inspection.utf8_bom,
            "line_endings": inspection.line_endings,
            "unchanged_during_run": unchanged,
        },
        "parser": {
            "accepted_row_count": row_count,
            "column_count": len(columns),
            "columns": columns,
            "raw_header": header["raw_columns"],
            "duplicate_header_names": header["duplicate_names"],
            "rejections": rejections,
        },
        "domain": domain,
        "columns": column_profiles,
    }

    timestamp = completed_at.strftime("%Y%m%dT%H%M%S%fZ")
    basename = f"storeleads-profile-{timestamp}"
    json_path = output_dir / f"{basename}.json"
    markdown_path = output_dir / f"{basename}.md"
    json_path.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(profile), encoding="utf-8")
    return json_path, markdown_path, profile
