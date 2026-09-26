"""The 4CP: ERCOT's four coincident summer peaks, the zones at those intervals, how many dispatch days catch
them, where scarcity moved, and the transmission rate that prices them (contract §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.common import Meta


class FourCpInterval(BaseModel):
    year: int
    month: int
    interval_end_local: str = Field(description="Interval end, Central time")
    interval_end_utc: datetime | None = None
    mw: float
    source: str = Field(description="de_15min, or hourly_provisional for months not yet in the D&E report")
    final: bool


class FourCpZone(BaseModel):
    region_type: str = Field(description="weather_zone or load_zone")
    region_id: str
    year: int
    cp_avg_mw: float | None = None
    ncp_summer_mw: float | None = None
    cf_summer: float | None = Field(None, description="Load at the CPs ÷ the zone's own summer peak")
    share_4cp: float | None = None
    share_energy: float | None = None
    intensity: float | None = Field(None, description="share_4cp ÷ share_energy")
    ncp_end_hour: float | None = Field(None, description="The zone's own peak, local hour as a decimal")
    energy_mwh: float | None = None


class DispatchPoint(BaseModel):
    forecast: str
    param: float
    window: str
    dispatch_days: float = Field(description="Dispatch days per summer")
    all4_rate: float = Field(description="Share of summers with all four CPs caught")
    month_rate: float | None = None
    day_rate: float | None = None


class Scarcity(BaseModel):
    year: int
    load_peak_mean_he: float | None = None
    net_load_peak_mean_he: float | None = None
    cp_net_load_rank_median: float | None = None
    cp_price_rank_median: float | None = None
    cp_in_top20_price_share: float | None = None
    top20_price_after_18h_share: float | None = None
    wind_solar_share: float | None = None


class TransmissionRate(BaseModel):
    charges_for_year: int
    docket: str
    postage_stamp_usd_per_kw_yr: float
    status: Literal["final", "pending"]
    billed_year: int = Field(description="Year Y's 4CP is billed in year Y+1")
    source_url: str | None = None


class FourCpData(BaseModel):
    window_start_local: str | None = Field(None, description="The dispatch window the offer uses")
    window_end_local: str | None = None
    intervals: list[FourCpInterval]
    zones: list[FourCpZone]
    dispatch_curve: list[DispatchPoint]
    scarcity: list[Scarcity]
    rates: list[TransmissionRate]


class FourCpResponse(BaseModel):
    meta: Meta
    data: FourCpData
