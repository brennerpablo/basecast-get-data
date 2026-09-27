"""basecast-get-data: the API between the data (lake, Postgres, BigQuery) and basecast-app.

Run locally with `uv run uvicorn basecast_get_data.main:app --reload --port 8000`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from basecast_get_data.config import get_settings
from basecast_get_data.db.pg import DatabaseUnavailable
from basecast_get_data.lake.service import get_lake
from basecast_get_data.products import store
from basecast_get_data.products.store import MartNotBuilt
from basecast_get_data.routers import (
    accounts,
    backtest,
    catalogs,
    forecasts,
    four_cp,
    geo,
    insights,
    lake,
    load,
    pipeline,
    queue,
    tables,
)

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
    # Load the marts the product resources read from Postgres before taking traffic, and keep them fresh.
    store.start()
    yield


class Health(BaseModel):
    status: str
    lake: str
    database: bool
    # Where the product resources read: "fixtures" or "marts", plus the groups already on marts.
    data_mode: str
    marts_live: list[str]
    marts_loaded: dict[str, int]


def create_app() -> FastAPI:
    app = FastAPI(
        title="basecast-get-data",
        version="0.2.0",
        description="Serves basecast-app: the accounts, Explorer, forecast and backtest resources "
        "(contract v2), and the lake, the processed tables and the pipeline runs for /data. "
        "Every route but /health needs `Authorization: Bearer <API_TOKEN>`.",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    for module in (
        accounts,
        geo,
        queue,
        forecasts,
        load,
        four_cp,
        backtest,
        insights,
        catalogs,
        lake,
        tables,
        pipeline,
    ):
        app.include_router(module.router)

    @app.exception_handler(MartNotBuilt)
    def mart_not_built(_: Request, exc: MartNotBuilt) -> JSONResponse:
        # The app shows this body as an empty state; keep it exactly (docs/data-contract.md, "Erros").
        return JSONResponse(status_code=503, content={"detail": "mart_not_built", "mart": exc.mart})

    @app.exception_handler(DatabaseUnavailable)
    @app.exception_handler(psycopg.OperationalError)
    def database_unavailable(_: Request, exc: Exception) -> JSONResponse:
        log.error("database unavailable: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})

    @app.get("/health", response_model=Health, tags=["health"])
    def health() -> Health:
        s = get_settings()
        return Health(
            status="ok",
            lake=s.lake_root,
            database=s.db_configured,
            data_mode=s.data_mode,
            marts_live=sorted(s.marts_live),
            marts_loaded=store.loaded(),
        )

    return app


app = create_app()
