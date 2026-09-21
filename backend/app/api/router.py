from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.models.query import (
    FacetRequest,
    FacetResponse,
    QueryRequest,
    QueryResponse,
    SchemaResponse,
)
from app.services.query_service import QueryService
from app.services.schema_registry import PUBLIC_SCHEMA


class HealthResponse(BaseModel):
    status: Literal["ok"]


api_router = APIRouter()


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


def _service(request: Request) -> QueryService:
    return QueryService(
        Path(request.app.state.database_path),
        timeout_seconds=request.app.state.query_timeout_seconds,
    )


@api_router.get("/schema", response_model=SchemaResponse, tags=["data"])
def schema() -> dict[str, object]:
    return PUBLIC_SCHEMA


@api_router.post("/query", response_model=QueryResponse, tags=["data"])
def query(payload: QueryRequest, request: Request) -> QueryResponse:
    return _service(request).query(payload)


@api_router.post("/facets", response_model=FacetResponse, tags=["data"])
def facets(payload: FacetRequest, request: Request) -> FacetResponse:
    return _service(request).facets(payload)
