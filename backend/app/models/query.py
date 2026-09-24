"""Public request and response models for the data explorer API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterCondition(APIModel):
    column: str
    operator: str
    value: Any = None


class FilterGroup(APIModel):
    combinator: Literal["and", "or"] = "and"
    filters: list[FilterNode] = Field(min_length=1)


FilterNode = FilterCondition | FilterGroup
FilterGroup.model_rebuild()


class SortSpec(APIModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class QueryRequest(APIModel):
    columns: list[str] = Field(default_factory=list)
    filters: list[FilterNode] = Field(default_factory=list)
    sort: list[SortSpec] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None
    offset: int = Field(default=0, ge=0)


class QueryResponse(APIModel):
    rows: list[dict[str, Any]]
    next_cursor: str | None
    total_count: int


class FacetRequest(APIModel):
    column: str
    filters: list[FilterNode] = Field(default_factory=list)
    search: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


class FacetValue(APIModel):
    value: Any
    count: int


class FacetResponse(APIModel):
    column: str
    values: list[FacetValue]


class SchemaColumnResponse(APIModel):
    name: str
    label: str
    data_type: str
    filter_operators: list[str]
    sortable: bool
    filterable: bool
    default_visible: bool
    facet_enabled: bool


class SchemaResponse(APIModel):
    schema_version: int
    columns: list[SchemaColumnResponse]


class ExportRequest(APIModel):
    columns: list[str] = Field(default_factory=list)
    filters: list[FilterNode] = Field(default_factory=list)
    sort: list[SortSpec] = Field(default_factory=list)


class ExportJobResponse(APIModel):
    export_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    columns: list[str]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    row_count: int | None = None
    byte_size: int | None = None
    error: str | None = None
    download_url: str | None = None
