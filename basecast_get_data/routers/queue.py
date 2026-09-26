"""The adjusted generation queue, project by project (docs/data-contract.md §1)."""

from __future__ import annotations

import polars as pl
from fastapi import APIRouter, Depends, Query

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import product_meta
from basecast_get_data.schemas.common import PRODUCT_ERRORS
from basecast_get_data.schemas.queue import (
    ProjectSort,
    ProjectStratum,
    QueueProject,
    QueueProjectsData,
    QueueProjectsResponse,
    Stage,
)

router = APIRouter(prefix="/queue", tags=["explorer"], dependencies=[Depends(require_token)])

PROJECTS = "mart_queue_project_scores"


@router.get("/projects", response_model=QueueProjectsResponse, responses=PRODUCT_ERRORS)
def projects(
    county: list[str] | None = Query(None, description="County FIPS"),
    stratum: list[ProjectStratum] | None = Query(None),
    stage: Stage | None = Query(None),
    zone: list[str] | None = Query(None, description="Weather zone"),
    q: str | None = Query(None, max_length=100, description="Project name or INR contains"),
    sort: ProjectSort = Query("mw_2028"),
    desc: bool = Query(True),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> QueueProjectsResponse:
    """Active projects of the latest GIS report with their chance of reaching COD by December 2027 and
    2028."""
    df = store.frame(PROJECTS)
    if df.height:
        df = df.filter(pl.col("as_of_month") == pl.col("as_of_month").max())
    month = df["as_of_month"].max() if df.height else None
    for column, values in (("county_fips", county), ("stratum", stratum), ("weather_zone", zone)):
        if values:
            df = df.filter(pl.col(column).is_in(values))
    if stage:
        df = df.filter(pl.col("stage") == stage)
    if q:
        needle = q.strip().lower()
        df = df.filter(
            pl.col("project_name").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("inr").str.to_lowercase().str.contains(needle, literal=True)
        )
    df = df.sort([sort, "inr"], descending=[desc, False], nulls_last=True)
    return QueueProjectsResponse(
        meta=product_meta(PROJECTS, rows=df, caveats=["beyond_backtested_window"]),
        data=QueueProjectsData(
            items=[QueueProject(**r) for r in df.slice(offset, limit).to_dicts()],
            total=df.height,
            offset=offset,
            limit=limit,
            as_of_month=month,
        ),
    )
