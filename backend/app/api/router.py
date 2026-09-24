from typing import Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.models.query import (
    ExportJobResponse,
    ExportRequest,
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
    return request.app.state.query_service


@api_router.get("/schema", response_model=SchemaResponse, tags=["data"])
def schema() -> dict[str, object]:
    return PUBLIC_SCHEMA


@api_router.post("/query", response_model=QueryResponse, tags=["data"])
def query(payload: QueryRequest, request: Request) -> QueryResponse:
    return _service(request).query(payload)


@api_router.post("/facets", response_model=FacetResponse, tags=["data"])
def facets(payload: FacetRequest, request: Request) -> FacetResponse:
    return _service(request).facets(payload)


@api_router.post(
    "/exports",
    response_model=ExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["exports"],
)
def create_export(payload: ExportRequest, request: Request) -> ExportJobResponse:
    return request.app.state.export_manager.create(payload)


@api_router.get(
    "/exports/{export_id}", response_model=ExportJobResponse, tags=["exports"]
)
def get_export(export_id: str, request: Request) -> ExportJobResponse:
    return request.app.state.export_manager.get(export_id)


@api_router.post(
    "/exports/{export_id}/cancel",
    response_model=ExportJobResponse,
    tags=["exports"],
)
def cancel_export(export_id: str, request: Request) -> ExportJobResponse:
    return request.app.state.export_manager.cancel(export_id)


@api_router.get("/exports/{export_id}/download", tags=["exports"])
def download_export(export_id: str, request: Request) -> FileResponse:
    path = request.app.state.export_manager.download_path(export_id)
    return FileResponse(
        path,
        media_type="text/csv",
        filename=f"storeleads-{export_id}.csv",
    )
