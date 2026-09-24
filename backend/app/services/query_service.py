"""Validated SQL construction and execution for Store Leads queries."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb

from app.models.query import (
    ExportRequest,
    FacetRequest,
    FacetResponse,
    FacetValue,
    FilterCondition,
    FilterNode,
    QueryRequest,
    QueryResponse,
    SortSpec,
)
from app.services.schema_registry import DEFAULT_COLUMNS, SCHEMA_REGISTRY

MAX_FILTER_NODES = 100
MAX_FILTER_DEPTH = 5
MAX_SORT_COLUMNS = 5
MAX_LIST_VALUES = 100
# Cold queries over the full production dataset can take more than 20 seconds
# even though their warm executions are much faster. Keep a bounded execution
# time without cancelling known-valid interactive workloads prematurely.
DEFAULT_TIMEOUT_SECONDS = 30.0
COUNT_CACHE_SIZE = 128


class QueryAPIError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


@dataclass(frozen=True)
class CompiledWhere:
    sql: str
    parameters: list[Any]


def _identifier(value: str) -> str:
    # Identifiers only reach here after a registry lookup. Quoting is defense in depth.
    return '"' + value.replace('"', '""') + '"'


def _column(name: str):
    definition = SCHEMA_REGISTRY.get(name)
    if definition is None:
        raise QueryAPIError(
            "invalid_column", f"Unknown column: {name}", details={"column": name}
        )
    return definition


def _require_scalar(value: Any, column: str, operator: str) -> Any:
    if value is None or isinstance(value, (list, dict)):
        raise QueryAPIError(
            "invalid_filter_value",
            f"{operator} on {column} requires one non-null value",
            details={"column": column, "operator": operator},
        )
    return value


def _require_list(value: Any, column: str, operator: str, length: int | None = None):
    if not isinstance(value, list) or not value or (length and len(value) != length):
        requirement = f"exactly {length} values" if length else "a non-empty list"
        raise QueryAPIError(
            "invalid_filter_value",
            f"{operator} on {column} requires {requirement}",
            details={"column": column, "operator": operator},
        )
    if len(value) > MAX_LIST_VALUES:
        raise QueryAPIError(
            "invalid_filter_value",
            f"{operator} on {column} accepts at most {MAX_LIST_VALUES} values",
            details={"column": column, "operator": operator},
        )
    if any(item is None or isinstance(item, (list, dict)) for item in value):
        raise QueryAPIError(
            "invalid_filter_value",
            f"{operator} on {column} does not accept null or nested values",
            details={"column": column, "operator": operator},
        )
    return value


def _typed_value(definition, value: Any, column: str, operator: str) -> Any:
    data_type = definition.data_type
    invalid = False
    normalized = value
    if data_type in {"string", "collection"}:
        invalid = not isinstance(value, str)
    elif data_type == "integer":
        invalid = isinstance(value, bool) or not isinstance(value, int)
    elif data_type == "number":
        invalid = isinstance(value, bool) or not isinstance(value, (int, float))
        invalid = invalid or (isinstance(value, float) and not math.isfinite(value))
    elif data_type == "boolean":
        invalid = not isinstance(value, bool)
    elif data_type == "date":
        try:
            normalized = value if isinstance(value, date) else date.fromisoformat(value)
        except (TypeError, ValueError):
            invalid = True
    elif data_type == "decimal":
        try:
            normalized = Decimal(str(value))
            invalid = isinstance(value, bool) or not normalized.is_finite()
        except (ValueError, ArithmeticError):
            invalid = True
    elif data_type == "uuid":
        try:
            normalized = value if isinstance(value, UUID) else UUID(value)
        except (TypeError, ValueError, AttributeError):
            invalid = True
    if invalid:
        raise QueryAPIError(
            "invalid_filter_value",
            f"{operator} on {column} requires a {data_type} value",
            details={
                "column": column,
                "operator": operator,
                "expected_type": data_type,
            },
        )
    return normalized


def _typed_values(definition, values: list[Any], column: str, operator: str):
    return [_typed_value(definition, value, column, operator) for value in values]


def _escape_like(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _compile_condition(condition: FilterCondition) -> CompiledWhere:
    definition = _column(condition.column)
    operator = condition.operator
    if not definition.filterable or operator not in definition.filter_operators:
        raise QueryAPIError(
            "invalid_operator",
            f"Operator {operator!r} is not available for {condition.column}",
            details={"column": condition.column, "operator": operator},
        )
    identifier = f"s.{_identifier(condition.column)}"
    value = condition.value

    if operator in {"is_null", "is_not_null"}:
        if value is not None:
            raise QueryAPIError(
                "invalid_filter_value",
                f"{operator} on {condition.column} does not accept a value",
            )
        suffix = "IS NULL" if operator == "is_null" else "IS NOT NULL"
        return CompiledWhere(f"{identifier} {suffix}", [])

    if definition.collection_table:
        child = _identifier(definition.collection_table)
        if operator == "has":
            value = _typed_value(
                definition,
                _require_scalar(value, condition.column, operator),
                condition.column,
                operator,
            )
            return CompiledWhere(
                f"EXISTS (SELECT 1 FROM {child} c "
                "WHERE c.store_id = s.store_id AND c.value = ?)",
                [value],
            )
        values = _typed_values(
            definition,
            _require_list(value, condition.column, operator),
            condition.column,
            operator,
        )
        placeholders = ", ".join("?" for _ in values)
        if operator == "has_any":
            sql = (
                f"EXISTS (SELECT 1 FROM {child} c WHERE c.store_id = s.store_id "
                f"AND c.value IN ({placeholders}))"
            )
        else:
            # De-duplicate by value so parameters and required count agree.
            values = list(dict.fromkeys(values))
            placeholders = ", ".join("?" for _ in values)
            sql = (
                f"(SELECT count(DISTINCT c.value) FROM {child} c "
                f"WHERE c.store_id = s.store_id AND c.value IN ({placeholders})) "
                f"= {len(values)}"
            )
        return CompiledWhere(sql, values)

    if operator in {"in", "not_in"}:
        values = _typed_values(
            definition,
            _require_list(value, condition.column, operator),
            condition.column,
            operator,
        )
        placeholders = ", ".join("?" for _ in values)
        keyword = "IN" if operator == "in" else "NOT IN"
        return CompiledWhere(f"{identifier} {keyword} ({placeholders})", values)
    if operator == "between":
        values = _typed_values(
            definition,
            _require_list(value, condition.column, operator, length=2),
            condition.column,
            operator,
        )
        return CompiledWhere(f"{identifier} BETWEEN ? AND ?", values)

    value = _typed_value(
        definition,
        _require_scalar(value, condition.column, operator),
        condition.column,
        operator,
    )
    if operator == "matches_token":
        token = str(value).strip()
        if not token:
            raise QueryAPIError(
                "invalid_filter_value",
                f"{operator} on {condition.column} requires a non-empty token",
            )
        pattern = rf"(^|[^[:alnum:]_]){re.escape(token.lower())}([^[:alnum:]_]|$)"
        return CompiledWhere(
            f"regexp_matches(lower(coalesce({identifier}, '')), ?)", [pattern]
        )
    comparisons = {
        "eq": "=",
        "neq": "<>",
        "lt": "<",
        "lte": "<=",
        "gt": ">",
        "gte": ">=",
    }
    if operator in comparisons:
        return CompiledWhere(f"{identifier} {comparisons[operator]} ?", [value])
    escaped = _escape_like(value)
    patterns = {
        "contains": f"%{escaped}%",
        "not_contains": f"%{escaped}%",
        "starts_with": f"{escaped}%",
        "ends_with": f"%{escaped}",
    }
    sql = f"{identifier} ILIKE ? ESCAPE '\\'"
    if operator == "not_contains":
        sql = f"NOT ({sql})"
    return CompiledWhere(sql, [patterns[operator]])


def _compile_node(node: FilterNode, *, depth: int = 1) -> CompiledWhere:
    if depth > MAX_FILTER_DEPTH:
        raise QueryAPIError(
            "filter_too_complex", f"Filter nesting exceeds {MAX_FILTER_DEPTH} levels"
        )
    if isinstance(node, FilterCondition):
        return _compile_condition(node)
    compiled = [_compile_node(child, depth=depth + 1) for child in node.filters]
    joiner = " AND " if node.combinator == "and" else " OR "
    return CompiledWhere(
        "(" + joiner.join(item.sql for item in compiled) + ")",
        [parameter for item in compiled for parameter in item.parameters],
    )


def compile_filters(filters: list[FilterNode]) -> CompiledWhere:
    def count(node: FilterNode) -> int:
        if isinstance(node, FilterCondition):
            return 1
        return 1 + sum(count(child) for child in node.filters)

    if sum(count(node) for node in filters) > MAX_FILTER_NODES:
        raise QueryAPIError(
            "filter_too_complex", f"At most {MAX_FILTER_NODES} filter nodes are allowed"
        )
    if not filters:
        return CompiledWhere("TRUE", [])
    compiled = [_compile_node(node) for node in filters]
    return CompiledWhere(
        " AND ".join(f"({item.sql})" for item in compiled),
        [parameter for item in compiled for parameter in item.parameters],
    )


def _validate_columns(columns: list[str]) -> list[str]:
    selected = columns or [name for name in SCHEMA_REGISTRY if name in DEFAULT_COLUMNS]
    if len(selected) > len(SCHEMA_REGISTRY):
        raise QueryAPIError("too_many_columns", "Too many result columns requested")
    if len(selected) != len(set(selected)):
        raise QueryAPIError("duplicate_column", "Result columns must be unique")
    for name in selected:
        _column(name)
    return selected


def _validated_sort(sort: list[SortSpec]) -> list[SortSpec]:
    if len(sort) > MAX_SORT_COLUMNS:
        raise QueryAPIError(
            "too_many_sort_columns",
            f"At most {MAX_SORT_COLUMNS} sort fields are allowed",
        )
    seen: set[str] = set()
    result: list[SortSpec] = []
    for item in sort or [SortSpec(column="domain", direction="asc")]:
        definition = _column(item.column)
        if not definition.sortable:
            raise QueryAPIError(
                "invalid_sort", f"Column {item.column} cannot be sorted"
            )
        if item.column in seen:
            raise QueryAPIError("duplicate_sort", "Sort columns must be unique")
        seen.add(item.column)
        result.append(item)
    if "store_id" not in seen:
        result.append(SortSpec(column="store_id", direction="asc"))
    return result


def compile_export_query(request: ExportRequest) -> tuple[str, list[Any], list[str]]:
    """Build the validated, unbounded SELECT used by DuckDB COPY."""
    selected = _validate_columns(request.columns)
    sort = _validated_sort(request.sort)
    where = compile_filters(request.filters)
    projection = ", ".join(f's.{_identifier(column)}' for column in selected)
    order = ", ".join(
        f's.{_identifier(item.column)} {item.direction.upper()} NULLS LAST'
        for item in sort
    )
    sql = (
        f"SELECT {projection} FROM stores s WHERE ({where.sql}) ORDER BY {order}"
    )
    return sql, where.parameters, selected


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, Decimal, UUID)):
        return str(value)
    return value


def _encode_cursor(sort: list[SortSpec], row: dict[str, Any]) -> str:
    payload = {
        "v": 1,
        "sort": [[item.column, item.direction] for item in sort],
        "values": [_json_value(row[item.column]) for item in sort],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, sort: list[SortSpec]) -> list[Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise QueryAPIError("invalid_cursor", "Cursor is malformed") from error
    expected_sort = [[item.column, item.direction] for item in sort]
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or payload.get("sort") != expected_sort
        or not isinstance(payload.get("values"), list)
        or len(payload["values"]) != len(sort)
    ):
        raise QueryAPIError(
            "invalid_cursor", "Cursor does not match the requested ordering"
        )
    values: list[Any] = []
    try:
        for item, value in zip(sort, payload["values"], strict=True):
            values.append(
                None
                if value is None
                else _typed_value(_column(item.column), value, item.column, "cursor")
            )
    except QueryAPIError as error:
        raise QueryAPIError(
            "invalid_cursor", "Cursor contains invalid values"
        ) from error
    return values


def _cursor_predicate(sort: list[SortSpec], values: list[Any]) -> CompiledWhere:
    branches: list[str] = []
    parameters: list[Any] = []
    prefix: list[str] = []
    prefix_parameters: list[Any] = []
    for item, value in zip(sort, values, strict=True):
        identifier = f"s.{_identifier(item.column)}"
        if value is not None:
            comparison = ">" if item.direction == "asc" else "<"
            later = f"({identifier} {comparison} ? OR {identifier} IS NULL)"
            branches.append("(" + " AND ".join([*prefix, later]) + ")")
            parameters.extend([*prefix_parameters, value])
        prefix.append(f"{identifier} IS NOT DISTINCT FROM ?")
        prefix_parameters.append(value)
    return CompiledWhere("(" + " OR ".join(branches) + ")", parameters)


class QueryService:
    def __init__(
        self, database: Path, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.database = Path(database)
        self.timeout_seconds = timeout_seconds
        self._count_cache: OrderedDict[tuple[object, ...], int] = OrderedDict()
        self._count_cache_lock = threading.Lock()

    def _count(self, where: CompiledWhere) -> int:
        try:
            database_stat = self.database.stat()
        except OSError:
            database_signature = (0, 0)
        else:
            database_signature = (database_stat.st_mtime_ns, database_stat.st_size)
        cache_key = (*database_signature, where.sql, *where.parameters)
        with self._count_cache_lock:
            cached = self._count_cache.get(cache_key)
            if cached is not None:
                self._count_cache.move_to_end(cache_key)
                return cached

        count_sql = f"SELECT count(*) FROM stores s WHERE ({where.sql})"
        _, records = self._execute(count_sql, list(where.parameters))
        total_count = int(records[0][0])
        with self._count_cache_lock:
            self._count_cache[cache_key] = total_count
            self._count_cache.move_to_end(cache_key)
            while len(self._count_cache) > COUNT_CACHE_SIZE:
                self._count_cache.popitem(last=False)
        return total_count

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if not self.database.is_file():
            raise QueryAPIError(
                "database_unavailable",
                f"Store database is unavailable at {self.database}",
                status_code=503,
            )
        try:
            return duckdb.connect(str(self.database), read_only=True)
        except duckdb.Error as error:
            raise QueryAPIError(
                "database_unavailable", str(error), status_code=503
            ) from error

    def _execute(
        self, sql: str, parameters: list[Any]
    ) -> tuple[list[str], list[tuple]]:
        connection = self._connect()
        timed_out = threading.Event()
        finished = threading.Event()
        connection_lock = threading.Lock()

        def interrupt() -> None:
            with connection_lock:
                if finished.is_set():
                    return
                timed_out.set()
                connection.interrupt()

        timer = threading.Timer(self.timeout_seconds, interrupt)
        timer.daemon = True
        timer.start()
        try:
            result = connection.execute(sql, parameters)
            names = [item[0] for item in result.description]
            return names, result.fetchall()
        except duckdb.Error as error:
            if timed_out.is_set():
                raise QueryAPIError(
                    "query_timeout",
                    f"Query exceeded {self.timeout_seconds:g} seconds",
                    status_code=504,
                ) from error
            raise QueryAPIError("query_failed", str(error), status_code=500) from error
        finally:
            timer.cancel()
            with connection_lock:
                finished.set()
                connection.close()

    def query(self, request: QueryRequest) -> QueryResponse:
        selected = _validate_columns(request.columns)
        sort = _validated_sort(request.sort)
        where = compile_filters(request.filters)
        total_count = self._count(where)
        where_parts = [where.sql]
        parameters = list(where.parameters)
        if request.cursor:
            cursor = _cursor_predicate(sort, _decode_cursor(request.cursor, sort))
            where_parts.append(cursor.sql)
            parameters.extend(cursor.parameters)

        hidden_sort = [item.column for item in sort if item.column not in selected]
        query_columns = [*selected, *hidden_sort]
        projection = ", ".join(
            f"s.{_identifier(column)}" for column in query_columns
        )
        order = ", ".join(
            f"s.{_identifier(item.column)} {item.direction.upper()} NULLS LAST"
            for item in sort
        )
        sql = (
            f"SELECT {projection} FROM stores s WHERE "
            + " AND ".join(f"({part})" for part in where_parts)
            + f" ORDER BY {order} LIMIT ? OFFSET ?"
        )
        parameters.append(request.limit + 1)
        parameters.append(request.offset)
        names, records = self._execute(sql, parameters)
        materialized = [dict(zip(names, row, strict=True)) for row in records]
        has_more = len(materialized) > request.limit
        materialized = materialized[: request.limit]
        next_cursor = (
            _encode_cursor(sort, materialized[-1])
            if has_more and materialized
            else None
        )
        rows = [
            {column: row[column] for column in selected} for row in materialized
        ]
        return QueryResponse(
            rows=rows, next_cursor=next_cursor, total_count=total_count
        )

    def facets(self, request: FacetRequest) -> FacetResponse:
        definition = _column(request.column)
        if not definition.facet_enabled:
            raise QueryAPIError(
                "facet_not_enabled", f"Faceting is not enabled for {request.column}"
            )
        where = compile_filters(request.filters)
        parameters = list(where.parameters)
        search = request.search.strip() if request.search else ""
        search_pattern = f"%{_escape_like(search)}%" if search else None
        if definition.collection_table:
            child = _identifier(definition.collection_table)
            search_sql = " AND c.value ILIKE ? ESCAPE '\\'" if search_pattern else ""
            # Import guarantees one row per (store_id, value), so count(*) is
            # the distinct store count without an expensive hash-distinct over
            # multi-million-row collection tables.
            if request.filters:
                source = f"{child} c JOIN stores s ON s.store_id = c.store_id"
                where_sql = where.sql
            else:
                # The option picker requests global values. Avoid joining every
                # collection row back to the four-million-row stores table.
                source = f"{child} c"
                where_sql = "TRUE"
            sql = (
                f"SELECT c.value, count(*) AS count FROM {source} "
                f"WHERE {where_sql}{search_sql} GROUP BY c.value "
                "ORDER BY count DESC, c.value ASC LIMIT ?"
            )
        else:
            identifier = f"s.{_identifier(request.column)}"
            search_sql = (
                f" AND CAST({identifier} AS VARCHAR) ILIKE ? ESCAPE '\\'"
                if search_pattern
                else ""
            )
            sql = (
                f"SELECT {identifier}, count(*) AS count FROM stores s "
                f"WHERE {where.sql} AND {identifier} IS NOT NULL{search_sql} "
                f"GROUP BY {identifier} "
                f"ORDER BY count DESC, {identifier} ASC NULLS LAST LIMIT ?"
            )
        if search_pattern:
            parameters.append(search_pattern)
        parameters.append(request.limit)
        _, records = self._execute(sql, parameters)
        return FacetResponse(
            column=request.column,
            values=[FacetValue(value=value, count=count) for value, count in records],
        )
