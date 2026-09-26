"""The adjusted generation queue at project grain (contract §1, `/queue/projects`)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.common import Meta

Stratum = Literal["all", "solar", "storage", "wind", "gas_other"]
ProjectStratum = Literal["solar", "storage", "wind", "gas_other"]
Stage = Literal["entry", "ia"]
Horizon = Literal[2027, 2028]
ProjectSort = Literal["mw_2028", "mw_2027", "capacity_mw", "p_cod_2028", "projected_cod", "project_name"]


class QueueProject(BaseModel):
    inr: str
    project_name: str
    county_fips: str
    county_name: str
    weather_zone: str | None = None
    cdr_reporting_zone: str | None = None
    fuel_type: str
    stratum: ProjectStratum
    stage: Stage = Field(
        description="entry (incl. FIS approved without an IA) or ia (IA signed, or synchronized)"
    )
    stage_date: date | None = None
    elapsed_months: float | None = Field(None, description="Time spent at the stage")
    capacity_mw: float
    projected_cod: date | None = Field(None, description="The developer's date, the baseline the model beats")
    curve: str = Field(description="The survival curve used: own stratum or pooled")
    p_cod_2027: float = Field(description="P(COD by Dec 2027 | stage, elapsed time)")
    p_cod_2028: float = Field(description="P(COD by Dec 2028 | stage, elapsed time)")
    mw_2027: float
    mw_2028: float
    clamped_2027: bool = Field(description="Clock clamped past the curve's support (fewer than 10 at risk)")
    clamped_2028: bool


class QueueProjectsData(BaseModel):
    items: list[QueueProject]
    total: int
    offset: int
    limit: int
    as_of_month: date | None = Field(None, description="The GIS report month")


class QueueProjectsResponse(BaseModel):
    meta: Meta
    data: QueueProjectsData
