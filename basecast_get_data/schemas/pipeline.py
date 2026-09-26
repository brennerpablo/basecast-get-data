"""Pipeline runs (`etl_run`), docs/data-contract.md §6."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from basecast_get_data.schemas.common import Meta


class Run(BaseModel):
    run_id: str
    source: str
    stage: str
    dag_id: str | None = None
    task_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_s: float | None = None
    status: str
    rows: int | None = None
    files: int | None = None
    files_skipped: int | None = None
    bytes: int | None = None
    error: str | None = None


class RunsPage(BaseModel):
    items: list[Run]
    total: int
    offset: int
    limit: int


class RunsResponse(BaseModel):
    meta: Meta
    data: RunsPage
