"""Pipeline runs from `etl_run` (docs/data-contract.md §6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from basecast_get_data.auth import require_token
from basecast_get_data.db import pg
from basecast_get_data.schemas.common import meta
from basecast_get_data.schemas.pipeline import Run, RunsPage, RunsResponse

router = APIRouter(prefix="/pipeline", tags=["pipeline"], dependencies=[Depends(require_token)])

COLUMNS = (
    "run_id, source, stage, dag_id, task_id, started_at, finished_at, duration_s, status, rows, files,"
    " files_skipped, bytes, error"
)


@router.get("/runs", response_model=RunsResponse)
def runs(
    source: str | None = Query(None),
    stage: str | None = Query(None, description="raw, process or model"),
    status: str | None = Query(None, description="running, success, partial, failed or abandoned"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> RunsResponse:
    if not pg.db_available():
        raise HTTPException(503, "database not configured")
    where, params = [], []
    for column, value in (("source", source), ("stage", stage), ("status", status)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = pg.fetch_all(
        f"SELECT {COLUMNS}, count(*) OVER () AS total FROM etl_run{clause}"
        " ORDER BY started_at DESC LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    total = rows[0]["total"] if rows else 0
    items = [Run(**{k: v for k, v in r.items() if k != "total"}) for r in rows]
    return RunsResponse(
        meta=meta(sources=[source] if source else []),
        data=RunsPage(items=items, total=int(total), offset=offset, limit=limit),
    )
