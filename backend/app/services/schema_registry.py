"""The allowlist and UI metadata for fields exposed by the query API."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.services.csv_importer import (
    COLLECTION_COLUMNS,
    DROPPED_EMPTY_COLUMNS,
    EXPECTED_COLUMNS,
    MONEY_COLUMNS,
    SCHEMA_VERSION,
    TYPED_COLUMNS,
)

NULL_OPERATORS = ("is_null", "is_not_null")
STRING_OPERATORS = (
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    *NULL_OPERATORS,
)
ORDERED_OPERATORS = (
    "eq",
    "neq",
    "lt",
    "lte",
    "gt",
    "gte",
    "in",
    "not_in",
    "between",
    *NULL_OPERATORS,
)
BOOLEAN_OPERATORS = ("eq", "neq", *NULL_OPERATORS)
UUID_OPERATORS = ("eq", "neq", "in", "not_in")
COLLECTION_OPERATORS = ("has", "has_any", "has_all", *NULL_OPERATORS)

DEFAULT_COLUMNS = {
    "domain",
    "title",
    "country_code",
    "platform",
    "status",
    "estimated_monthly_visits",
    "estimated_monthly_sales_amount",
    "rank",
}
FACET_COLUMNS = {
    "country_code",
    "currency",
    "has_cms",
    "headless",
    "language_code",
    "last_platform",
    "platform",
    "region",
    "state",
    "status",
    *COLLECTION_COLUMNS,
}


@dataclass(frozen=True)
class ColumnDefinition:
    name: str
    label: str
    data_type: str
    filter_operators: tuple[str, ...]
    sortable: bool = True
    filterable: bool = True
    default_visible: bool = False
    facet_enabled: bool = False
    collection_table: str | None = None

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("collection_table")
        value["filter_operators"] = list(self.filter_operators)
        return value


def _label(name: str) -> str:
    return name.replace("_", " ").title().replace("Usd", "USD")


def _type_for(column: str) -> str:
    if column == "store_id":
        return "uuid"
    if column in COLLECTION_COLUMNS:
        return "collection"
    duckdb_type = TYPED_COLUMNS.get(column, "VARCHAR")
    return {
        "BIGINT": "integer",
        "BOOLEAN": "boolean",
        "DATE": "date",
        "DOUBLE": "number",
    }.get(duckdb_type, "string")


def _operators_for(data_type: str) -> tuple[str, ...]:
    if data_type == "collection":
        return COLLECTION_OPERATORS
    if data_type == "string":
        return STRING_OPERATORS
    if data_type == "boolean":
        return BOOLEAN_OPERATORS
    if data_type == "uuid":
        return UUID_OPERATORS
    return ORDERED_OPERATORS


def _build_registry() -> dict[str, ColumnDefinition]:
    names = ["store_id"]
    for column in EXPECTED_COLUMNS:
        if column in DROPPED_EMPTY_COLUMNS:
            continue
        names.append(column)
        if column in MONEY_COLUMNS:
            names.extend((f"{column}_currency", f"{column}_amount"))

    registry: dict[str, ColumnDefinition] = {}
    for name in names:
        if name.endswith("_amount") and name.removesuffix("_amount") in MONEY_COLUMNS:
            data_type = "decimal"
        elif (
            name.endswith("_currency")
            and name.removesuffix("_currency") in MONEY_COLUMNS
        ):
            data_type = "string"
        else:
            data_type = _type_for(name)
        operators = _operators_for(data_type)
        if name in {"title", "description"}:
            operators = (*operators, "matches_token")
        registry[name] = ColumnDefinition(
            name=name,
            label=_label(name),
            data_type=data_type,
            filter_operators=operators,
            sortable=data_type != "collection",
            default_visible=name in DEFAULT_COLUMNS,
            facet_enabled=name in FACET_COLUMNS,
            collection_table=(
                f"store_{name}" if name in COLLECTION_COLUMNS else None
            ),
        )
    return registry


SCHEMA_REGISTRY = _build_registry()
PUBLIC_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "columns": [column.public_dict() for column in SCHEMA_REGISTRY.values()],
}
