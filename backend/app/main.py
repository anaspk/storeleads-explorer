import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.services.export_service import ExportManager
from app.services.query_service import DEFAULT_TIMEOUT_SECONDS, QueryAPIError


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.export_manager = ExportManager(
        application.state.database_path, application.state.export_dir
    )
    try:
        yield
    finally:
        application.state.export_manager.shutdown()


def _error_body(code: str, message: str, details: Any = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def create_app(
    *,
    database_path: Path | None = None,
    export_dir: Path | None = None,
    query_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> FastAPI:
    application = FastAPI(
        title="Store Leads Explorer API",
        version="0.1.0",
        lifespan=lifespan,
    )
    default_database = Path(__file__).resolve().parents[2] / "data/storeleads.duckdb"
    application.state.database_path = database_path or Path(
        os.environ.get("STORELEADS_DATABASE", default_database)
    )
    default_export_dir = Path(__file__).resolve().parents[2] / "exports"
    application.state.export_dir = export_dir or Path(
        os.environ.get("STORELEADS_EXPORT_DIR", default_export_dir)
    )
    application.state.query_timeout_seconds = query_timeout_seconds

    @application.exception_handler(QueryAPIError)
    async def query_error_handler(_: Request, error: QueryAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=_error_body(error.code, error.message, error.details),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "request_validation_error", "Request body is invalid", error.errors()
            ),
        )

    application.include_router(api_router, prefix="/api")
    return application


app = create_app()
