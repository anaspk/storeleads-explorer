from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own application-scoped resources as later phases add them."""
    yield


app = FastAPI(
    title="Store Leads Explorer API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router, prefix="/api")
