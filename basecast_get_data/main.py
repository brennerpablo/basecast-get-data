"""basecast-get-data: the API between the data (lake, Postgres, BigQuery) and basecast-app.

Run locally with `uv run uvicorn basecast_get_data.main:app --reload --port 8000`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from basecast_get_data.config import get_settings
from basecast_get_data.lake.service import get_lake
from basecast_get_data.routers import lake, pipeline, tables

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("basecast_get_data")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Build the manifest index in the background, so the first /data page does not wait for it.
    def warm() -> None:
        try:
            get_lake().index.snapshot()
        except Exception:
            log.exception("could not build the lake index at startup")

    threading.Thread(target=warm, daemon=True).start()
    yield


class Health(BaseModel):
    status: str
    lake: str
    database: bool


def create_app() -> FastAPI:
    app = FastAPI(
        title="basecast-get-data",
        version="0.1.0",
        description="Serves the basecast lake, the processed tables and the pipeline runs to basecast-app. "
        "Every route but /health needs `Authorization: Bearer <API_TOKEN>`.",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.include_router(lake.router)
    app.include_router(tables.router)
    app.include_router(pipeline.router)

    @app.get("/health", response_model=Health, tags=["health"])
    def health() -> Health:
        s = get_settings()
        return Health(status="ok", lake=s.lake_root, database=s.db_configured)

    return app


app = create_app()
