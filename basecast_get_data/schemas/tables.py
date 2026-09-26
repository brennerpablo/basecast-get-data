"""Processed tables in Postgres and BigQuery."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from basecast_get_data.schemas.common import GridColumn, Meta

Engine = Literal["postgres", "bigquery"]
TableKind = Literal["dataset", "system", "unregistered"]


class TableSummary(BaseModel):
    name: str
    engine: Engine
    # Postgres schema or BigQuery dataset.
    location: str
    kind: TableKind
    sources: list[str]
    mode: str | None = None
    description: str | None = None
    declared: bool
    loaded: bool
    rows: int | None = None
    rows_estimated: bool = False
    bytes: int | None = None


class TablesData(BaseModel):
    items: list[TableSummary]


class TablesResponse(BaseModel):
    meta: Meta
    data: TablesData


class TableColumn(GridColumn):
    position: int
    nullable: bool
    is_key: bool = False


class TableDetail(TableSummary):
    columns: list[TableColumn]
    key_columns: list[str] = []
    partition_field: str | None = None
    cluster_fields: list[str] = []
    derived: bool = False


class TableResponse(BaseModel):
    meta: Meta
    data: TableDetail


class LineageRow(BaseModel):
    raw_key: str
    sha256: str
    version: int
    rows: int | None = None
    processed_at: datetime


class LineagePage(BaseModel):
    items: list[LineageRow]
    total: int
    offset: int
    limit: int


class LineageResponse(BaseModel):
    meta: Meta
    data: LineagePage
