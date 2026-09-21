import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.services.query_service import DEFAULT_TIMEOUT_SECONDS, QueryAPIError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own application-scoped resources as later phases add them."""
    yield


def _error_body(code: str, message: str, details: Any = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def create_app(
    *,
    database_path: Path | None = None,
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
