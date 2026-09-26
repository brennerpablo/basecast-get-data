"""The envelope every response carries (docs/data-contract.md, "Convenções") and shared grid shapes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.caveats import CATALOG, Caveat


def caveat(code: str) -> Caveat:
    label, text = CATALOG[code]
    return Caveat(code=code, label=label, text=text)  # type: ignore[arg-type]


class Meta(BaseModel):
    generated_at: datetime
    data_as_of: str | None = None
    # Short commit sha plus the mart's version, as the mart rows carry it.
    model_version: str | None = None
    # True when any value in the response comes from a fixture or a simulated private-data adapter.
    simulated: bool = False
    # False when any value in the response was machine-read and not checked by a person.
    verified: bool = True
    sources: list[str] = Field(default_factory=list)
    caveats: list[Caveat] = Field(default_factory=list)


def meta(
    *,
    sources: list[str] | None = None,
    data_as_of: str | None = None,
    model_version: str | None = None,
    simulated: bool = False,
    verified: bool = True,
    caveats: Iterable[str] = (),
) -> Meta:
    return Meta(
        generated_at=datetime.now(UTC),
        sources=sources or [],
        data_as_of=data_as_of,
        model_version=model_version,
        simulated=simulated,
        verified=verified,
        caveats=[caveat(c) for c in dict.fromkeys(caveats)],
    )


class MartNotBuilt(BaseModel):
    """503 body when a resource's mart does not exist yet. The app shows it as an empty state."""

    detail: Literal["mart_not_built"]
    mart: str


class ErrorDetail(BaseModel):
    detail: str


# The error responses every product endpoint declares, so the generated client knows their bodies.
PRODUCT_ERRORS: dict[int | str, dict[str, Any]] = {
    503: {"model": MartNotBuilt, "description": "The mart behind this resource is not built yet"},
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorDetail, "description": "`not_found`: the same body for every unknown id"},
}


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
