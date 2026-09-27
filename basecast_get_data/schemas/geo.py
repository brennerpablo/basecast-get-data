"""The Explorer map: one row per Texas county, and the county drill-down (contract §1).

The 50 counties outside ERCOT come with `acquisition = null`. The generation queue joins by `county_fips` and
is never copied into the acquisition mart.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.accounts import AccountType, NextAction, Tier
from basecast_get_data.schemas.common import Meta
from basecast_get_data.schemas.queue import Horizon, QueueProject, Stratum

Channel = Literal["retail_direct", "partnership", "mixed"]


class CountyAcquisition(BaseModel):
    priority: float = Field(description="0–1: market_score × grid_factor")
    rank: int = Field(description="1 = best of the ERCOT counties")
    priority_class: int = Field(description="1–5 quintile class (5 = top); breaks in data.legend")
    market_score: float
    grid_score: float
    grid_factor: float
    channel: Channel
    partner_type: AccountType | None = Field(None, description="The larger of co-op and muni land")
    retail_share: float = Field(description="By land area")
    coop_share: float
    muni_share: float
    outside_share: float
    partner_share: float
    addressable_share: float
    retail_rank: int | None = Field(None, description="Null = not in the retail-direct list (share < 25%)")
    partner_rank: int | None = Field(None, description="Null = not in the partnership list (share < 25%)")
    n_partners: int
    top_partner_share: float | None = None
    drivers: list[str] = Field(description="Top signals pushing the priority up")
    drags: list[str] = Field(description="Top signals pulling it down")


class CountyQueue(BaseModel):
    """The generation queue of the county for the requested horizon and stratum."""

    projects: int
    projects_ia: int
    raw_mw: float
    raw_mw_ia: float
    adj_mw: float = Field(description="Expected MW reaching COD by December of the horizon")
    ratio: float | None = Field(None, description="adj_mw ÷ raw_mw")
    rank_raw: int
    rank_adj: int
    rank_change: int = Field(description="rank_raw − rank_adj: positive = moves up once adjusted")
    large_gas_mw_2028: float | None = Field(None, description="Adjusted MW from gas/other projects ≥ 500 MW")


class CountyDataCenters(BaseModel):
    sites: int = Field(description="New data-center sites since 2025, every match")
    sites_naics_only: int = Field(description="Of which matched on NAICS 518210 only")


class CountyRow(BaseModel):
    county_fips: str
    county_name: str
    weather_zone: str | None = None
    in_ercot: bool
    acquisition: CountyAcquisition | None = None
    queue: CountyQueue | None = Field(None, description="Null = no active project in the stratum")
    data_centers: CountyDataCenters


class LegendClasses(BaseModel):
    breaks: list[float] = Field(description="The 4 priority breaks between the 5 classes")
    classes: int


class SignalWeight(BaseModel):
    signal: str
    label: str
    block: Literal["market", "grid"]
    unit: str | None = None
    weight: float = Field(description="Weight within its block")


class AcquisitionWeights(BaseModel):
    signals: list[SignalWeight]
    grid_tilt: float = Field(description="grid_factor = 1 − tilt + tilt × grid_score")


class CountiesData(BaseModel):
    items: list[CountyRow]
    horizon: Horizon
    stratum: Stratum
    horizons: list[int]
    strata: list[str]
    queue_as_of_month: date | None = None
    legend: LegendClasses
    weights: AcquisitionWeights


class CountiesResponse(BaseModel):
    meta: Meta
    data: CountiesData


# --- county drill-down ------------------------------------------------------------------------------------


class CountySignal(BaseModel):
    signal: str
    label: str
    block: Literal["market", "grid"]
    unit: str | None = None
    raw: float | None = None
    pct: float | None = None
    weight: float


class CountyQueueStratum(BaseModel):
    stratum: Stratum
    projects: int
    projects_ia: int
    raw_mw: float
    raw_mw_ia: float
    adj_mw_2027: float
    adj_mw_2028: float
    ratio_2027: float | None = None
    ratio_2028: float | None = None
    large_gas_mw_2028: float | None = None


class DataCenterSite(BaseModel):
    tceq_rn: str
    site_name: str
    county_fips: str
    county_name: str
    city: str | None = None
    first_permit_date: date | None = Field(
        None, description="Earliest TCEQ affiliation date (meaning not verified)"
    )
    matched_by: Literal["name", "naics"]
    has_undated_affiliation: bool = False
    largest_type: str | None = Field(None, description="coop, muni or iou, by county (not by point)")
    coop_share_w: float | None = None
    iso_class: str | None = None
    density_per_km2: float | None = None
    metro_legacy: bool = False
    metro_density: bool = False


class CountyAccount(BaseModel):
    account_id: str
    name: str
    account_type: AccountType
    county_share: float = Field(description="Share of the county's land the account covers (≥ 1%)")
    rank: int
    tier: Tier
    next_action: NextAction


class CountyDetail(BaseModel):
    county_fips: str
    county_name: str
    weather_zone: str | None = None
    in_ercot: bool
    acquisition: CountyAcquisition | None = None
    signals: list[CountySignal]
    queue: list[CountyQueueStratum]
    queue_as_of_month: date | None = None
    top_projects: list[QueueProject] = Field(description="The county's 10 largest by expected MW in Dec 2028")
    data_centers: list[DataCenterSite]
    accounts: list[CountyAccount] = Field(description="Co-ops and munis covering ≥ 1% of the county")


class CountyDetailResponse(BaseModel):
    meta: Meta
    data: CountyDetail


# --- zone layers (P1) ------------------------------------------------------------------------------------


class ZoneValue(BaseModel):
    weather_zone: str
    central: float | None = None
    low: float | None = Field(None, description="Low end of the allocation band, when there is one")
    high: float | None = None
    verified: bool = True


class ZoneLayer(BaseModel):
    measure: str = Field(
        description="excess_share, min_max_ratio_2019, min_max_ratio_2026 (X1); a2e_stock, pipeline_2032, "
        "u_share (X11)"
    )
    label: str
    unit: str
    method: str | None = Field(None, description="How the zone values were obtained (observed or allocated)")
    zones: list[ZoneValue]


class CountyLargeLoad(BaseModel):
    """A county's large load: the approved stock allocated to it, and ERCOT's figures where it names it."""

    county_fips: str
    county_name: str
    weather_zone: str | None = None
    allocated_a2e_mw: float | None = Field(
        None, description="Approved stock allocated by data-center signals"
    )
    named_by_ercot: bool = Field(description="ERCOT's deck names the county: draw it as an observed point")
    observed_base_mw: float | None = None
    observed_base_studied_mw: float | None = None
    verified: bool = False


class ZonesData(BaseModel):
    layers: list[ZoneLayer]
    counties: list[CountyLargeLoad]


class ZonesResponse(BaseModel):
    meta: Meta
    data: ZonesData
