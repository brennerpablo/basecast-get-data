"""Weather-normalized load by zone: what the load would have been under normal weather (contract §3)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from basecast_get_data.schemas.common import Meta
from basecast_get_data.schemas.forecasts import Region


class NormalizedMonth(BaseModel):
    month: date = Field(description="First day of the month")
    days: int
    complete: bool = Field(description="False for the running month")
    avg_mw: float | None = None
    avg_norm_mw: float | None = Field(None, description="Average load at normal weather")
    energy_gwh: float | None = None
    energy_norm_gwh: float | None = None
    peak_mw: float | None = None
    peak_norm_mw: float | None = Field(None, description="Weather-adjusted daily peak; not an expected peak")
    t_mean_c: float | None = None
    yoy_norm_pct: float | None = Field(None, description="12-month change of avg_norm_mw, in %")


class NormalizedYear(BaseModel):
    year: int
    energy_gwh: float | None = None
    energy_norm_gwh: float | None = None
    yoy_norm_pct: float | None = None
    summer_peak_mw: float | None = Field(None, description="Actual June–September daily max")
    summer_peak_norm_p10: float | None = Field(
        None, description="The summer peak under the normal weather years"
    )
    summer_peak_norm_p50: float | None = None
    summer_peak_norm_p90: float | None = None
    complete_through: date | None = Field(None, description="Last day included")


class NormalizedLoadData(BaseModel):
    region: Region
    regions: list[Region]
    normal_period: str | None = None
    weather_source: str | None = None
    monthly: list[NormalizedMonth]
    annual: list[NormalizedYear]


class NormalizedLoadResponse(BaseModel):
    meta: Meta
    data: NormalizedLoadData
