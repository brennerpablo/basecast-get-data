"""The peak forecast by layer and the large-load flow (contract §3).

Values read by machine from the large-load decks carry `verified = false` on their own row, so the app can put
the "machine-read" badge next to the number that has it.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.common import Meta

Region = Literal["ERCOT", "COAST", "EAST", "FWEST", "NCENT", "NORTH", "SCENT", "SOUTH", "WEST"]
Variant = Literal["deck_pre_batch_zero", "deck_latest", "approvals_pace"]
Layer = Literal["organic", "large_load", "unattributed"]
BandKind = Literal["p10_p90", "allocation_range"]
LargeLoadStatus = Literal[
    "approved_to_energize", "planning_studies_approved", "under_ercot_review", "no_studies_submitted"
]


class PeakPoint(BaseModel):
    target_year: int
    p10_mw: float | None = Field(None, description="Null when the variant has no band (approvals_pace)")
    p50_mw: float
    p90_mw: float | None = None
    band_kind: BandKind | None = Field(
        None,
        description="p10_p90: a probabilistic band; allocation_range: low/high across the candidate zone "
        "shares, not a P10–P90; null: no band",
    )
    band_basis: str | None = Field(
        None, description="What the band is made of; its text is in data.band_basis"
    )
    verified: bool = True


class PeakLayer(PeakPoint):
    layer: Layer


class OfficialLine(BaseModel):
    product: str = Field(description="LTLF or CDR")
    vintage: str
    vintage_date: date | None = None
    series: str = Field(description="ercot_adjusted, tsp_provided or cdr")
    label: str
    target_year: int
    mw: float


class PeakInputs(BaseModel):
    """How the large-load layer was built: the "how we got here" panel."""

    deck_vintage: date | None = Field(None, description="The large-load deck behind the layer")
    factor: float | None = Field(None, description="Observed simultaneous peak ÷ approved stock")
    ratio_p10: float | None = Field(None, description="Incremental realization ratio")
    ratio_p50: float | None = None
    ratio_p90: float | None = None
    approved_stock_mw: float | None = None
    share_of_ll_u: float | None = Field(
        None, description="Zones only: the fixed share of large load + unattributed"
    )
    verified: bool = True


class VariantOption(BaseModel):
    variant: Variant
    label: str
    is_default: bool
    has_band: bool
    regions: list[Region] = Field(
        description="Regions built for this variant (zones: the default variant only)"
    )


class PeakForecastData(BaseModel):
    region: Region
    region_type: Literal["ercot", "weather_zone"]
    variant: Variant
    available: bool = Field(description="False when this variant is not built for the region: empty series")
    variants: list[VariantOption]
    regions: list[Region]
    run_id: str | None = None
    as_of: date | None = None
    series: list[PeakPoint] = Field(description="The total, with the P10–P90 band")
    layers: list[PeakLayer] = Field(description="organic, large_load and unattributed per year")
    official: list[OfficialLine] = Field(description="ERCOT's own forecasts for the same region, as lines")
    inputs: PeakInputs
    band_basis: dict[str, str] = Field(default_factory=dict, description="band_basis code → its description")


class PeakForecastResponse(BaseModel):
    meta: Meta
    data: PeakForecastData


# --- large loads ------------------------------------------------------------------------------------------


class Realization(BaseModel):
    deck_vintage: date
    report_date: date | None = None
    target_year: int
    horizon_months: int
    promised_mw: float = Field(description="The deck's in-service bar for the year")
    promised_firm_mw: float | None = Field(None, description="Minus 'No Studies Submitted'")
    base_a2e_mw: float | None = Field(None, description="The deck's own approved-to-energize stock")
    realized_a2e_mw: float | None = Field(None, description="Approved stock in December of the target year")
    realized_energized_mw: float | None = None
    realized_month: date | None = Field(None, description="The month the realized stock was read")
    realized_partial: bool = Field(
        False, description="The target year is not over: the realized stock is partial"
    )
    known_from: date | None = Field(None, description="First deck that reports the December stock")
    gross_a2e: float | None = None
    gross_a2e_firm: float | None = None
    incremental_a2e: float | None = None
    incremental_a2e_firm: float | None = None
    gross_energized: float | None = None
    document: str | None = None
    page: int | None = None
    verified: bool = False


class InService(BaseModel):
    deck_vintage: date
    in_service_year: int
    status: LargeLoadStatus
    mw: float = Field(description="Cumulative MW in service by the end of the year")
    document: str | None = None
    page: int | None = None
    verified: bool = False


class MonthlyStock(BaseModel):
    month: date = Field(description="First of the month")
    a2e_mw: float | None = Field(None, description="Approved-to-energize stock; null = no reading that month")
    observed_simultaneous_mw: float | None = None
    observed_nonsimultaneous_mw: float | None = None
    a2e_lz_west_mw: float | None = None
    a2e_other_mw: float | None = None
    read_from_vintage: date | None = None
    document: str | None = None
    page: int | None = None
    verified: bool = False


class RatioBand(BaseModel):
    """The realization-ratio band the default forecast variant uses."""

    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    deck_vintage: date | None = None
    definition: str
    verified: bool = False


class Annotation(BaseModel):
    date: date
    title: str
    detail: str | None = None
    source_url: str | None = Field(None, description="Null = source not verified")
    verified: bool = False


class LargeLoadData(BaseModel):
    realization: list[Realization]
    ratio_band: RatioBand
    in_service: list[InService]
    monthly: list[MonthlyStock]
    annotations: list[Annotation] = Field(description="Empty until mart_annotations is built")
    deck_vintages: list[date]


class LargeLoadResponse(BaseModel):
    meta: Meta
    data: LargeLoadData
