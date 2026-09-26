"""The backtest: our peak model and ERCOT's official forecasts against the actual, and the queue model
(contract §4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.common import Meta


class BacktestCell(BaseModel):
    as_of: date
    target_year: int
    horizon: int = Field(description="Summers ahead of the as-of date")
    source: str = Field(
        description="basecast, basecast_organic_only (the ablation), LTLF, CDR or LTLF-prelim"
    )
    product: str | None = None
    vintage: str | None = None
    vintage_date: date | None = None
    variant: str | None = Field(None, description="basecast rows only")
    era: str = Field(description="See data.eras")
    p10_mw: float | None = Field(None, description="Official rows fill p50_mw only")
    p50_mw: float
    p90_mw: float | None = None
    actual_mw: float | None = None
    actual_final: bool = True
    error_pct: float | None = Field(None, description="(forecast − actual) ÷ actual, in %")
    in_band: bool | None = None
    organic_p50: float | None = None
    ll_p50: float | None = None
    u_p50: float | None = None
    ll_realized: float | None = None
    u_realized: float | None = None
    leak_note: str | None = Field(
        None, description="Information from after the as-of date that the cell used"
    )
    verified: bool = True


class BacktestScore(BaseModel):
    """Scores over the cells of one era and source: mean |error|, mean error and band coverage."""

    era: str
    source: str
    n: int
    mape: float = Field(description="Mean absolute error_pct, in %")
    bias_pct: float = Field(description="Mean error_pct, in %")
    coverage: float | None = Field(None, description="Share of cells inside the P10–P90 band (basecast only)")


class SourceComparison(BaseModel):
    """basecast against one official source on the cells where both exist, paired by (as_of, target_year)."""

    era: str
    official_source: str
    n: int
    basecast_mape: float
    official_mape: float
    basecast_bias_pct: float
    official_bias_pct: float


class Era(BaseModel):
    era: str
    label: str
    description: str


class FanPoint(BaseModel):
    """One mark of the fan chart of the latest summer: every official figure, the actual and our model."""

    kind: Literal["official_preliminary", "official_range", "official", "actual", "model"]
    label: str
    product: str | None = None
    vintage: str | None = None
    vintage_date: date | None = None
    value_mw: float | None = Field(None, description="Null for a range")
    low_mw: float | None = None
    high_mw: float | None = None
    source: str
    method: Literal["file", "manual", "model"]
    final: bool = True
    verified: bool = True


class ActualPeak(BaseModel):
    year: int
    hourly_peak_mw: float
    peak_ts_utc: datetime | None = None
    hour_ending_local: int | None = None
    peak_15min_mw: float | None = None
    interval_end_local_15min: str | None = None
    final: bool


class PeakBacktestData(BaseModel):
    as_of: date = Field(description="The selected as-of date")
    as_of_dates: list[date]
    cells: list[BacktestCell] = Field(description="Every cell of the selected as-of date")
    scores: list[BacktestScore] = Field(description="Over every as-of date, split by era (and era = all)")
    comparisons: list[SourceComparison] = Field(description="basecast vs each official source, paired cells")
    ablation: list[BacktestScore] = Field(description="basecast vs basecast_organic_only over every cell")
    eras: list[Era]
    actuals: list[ActualPeak]
    fan_target_year: int
    fan: list[FanPoint]


class PeakBacktestResponse(BaseModel):
    meta: Meta
    data: PeakBacktestData


class InvalidAsOf(BaseModel):
    """422 body when `as_of` is not one of the backtest dates."""

    detail: Literal["invalid_as_of"]
    as_of_dates: list[date]


# --- official forecast errors -----------------------------------------------------------------------------


class OfficialError(BaseModel):
    product: str
    vintage: str
    vintage_date: date | None = None
    target_year: int
    horizon: int
    series: str | None = Field(None, description="Which base series the value came from")
    forecast_mw: float
    actual_mw: float | None = None
    actual_complete: bool = True
    actual_peak_local: str | None = None
    error_mw: float | None = None
    error_pct: float | None = None


class HorizonSummary(BaseModel):
    product: str
    horizon: int
    n: int
    mape: float
    bias_pct: float


class OfficialErrorsData(BaseModel):
    items: list[OfficialError]
    summary: list[HorizonSummary]
    products: list[str]


class OfficialErrorsResponse(BaseModel):
    meta: Meta
    data: OfficialErrorsData


# --- generation queue -------------------------------------------------------------------------------------


class QueueBacktestRow(BaseModel):
    report_month: date
    stratum: str
    window_months: int
    raw_mw: float
    pred_mw: float
    actual_mw: float
    developer_projected_mw: float | None = None
    error_pct: float
    model_variant: str | None = None


class CountyRankCheck(BaseModel):
    """Spearman correlation across counties between each ranking and what was built."""

    report_month: date
    rho_adj: float | None = None
    rho_raw: float | None = None
    rho_developer: float | None = None


class QueueBacktestData(BaseModel):
    items: list[QueueBacktestRow]
    county_rank: list[CountyRankCheck]


class QueueBacktestResponse(BaseModel):
    meta: Meta
    data: QueueBacktestData
