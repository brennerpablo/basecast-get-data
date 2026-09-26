"""The envelope every response carries (docs/data-contract.md, "Convenções") and shared grid shapes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Meta(BaseModel):
    generated_at: datetime
    data_as_of: str | None = None
    model_run_id: str | None = None
    simulated: bool = False
    sources: list[str] = Field(default_factory=list)


def meta(*, sources: list[str] | None = None, data_as_of: str | None = None) -> Meta:
    return Meta(generated_at=datetime.now(UTC), sources=sources or [], data_as_of=data_as_of)


# A cell as it goes over the wire: dates and timestamps are ISO 8601 strings, nested values JSON text.
Cell = str | int | float | bool | None

ColumnType = Literal["text", "number", "date", "boolean", "json", "geometry"]


class GridColumn(BaseModel):
    name: str
    type: ColumnType
    # The type as the source names it (`double precision`, `TIMESTAMP`, `Float64`...).
    source_type: str | None = None


class RowsPage(BaseModel):
    """One block of rows. `total` comes only when the request asks for it (`with_summary=true`)."""

    columns: list[GridColumn]
    rows: list[list[Cell]]
    offset: int
    limit: int
    total: int | None = None
    total_estimated: bool = False


class RowsResponse(BaseModel):
    meta: Meta
    data: RowsPage
