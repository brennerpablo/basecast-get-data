"""The backtest: our peak model and ERCOT's official forecasts against the actual, and the queue model
(docs/data-contract.md §4). Scores are plain aggregates of the mart's cells, so they match the doc's
tables."""

from __future__ import annotations

from datetime import date

import polars as pl
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import all_verified, product_meta
from basecast_get_data.schemas.backtest import (
    ActualPeak,
    BacktestCell,
    BacktestScore,
    CountyRankCheck,
    Era,
    FanPoint,
    HorizonSummary,
    InvalidAsOf,
    OfficialError,
    OfficialErrorsData,
    OfficialErrorsResponse,
    PeakBacktestData,
    PeakBacktestResponse,
    QueueBacktestData,
    QueueBacktestResponse,
    QueueBacktestRow,
    SourceComparison,
)
from basecast_get_data.schemas.common import PRODUCT_ERRORS

router = APIRouter(prefix="/backtest", tags=["backtest"], dependencies=[Depends(require_token)])

CELLS, ACTUALS, FAN, ERRORS, QUEUE = (
    "mart_peak_backtest",
    "mart_actual_summer_peaks",
    "mart_backtest_fan",
    "mart_official_forecast_errors",
    "mart_queue_backtest",
)
ABLATION = ("basecast", "basecast_organic_only")


def _scores(cells: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    scored = cells.filter(pl.col("error_pct").is_not_null())
    return (
        scored.group_by(by)
        .agg(
            n=pl.len().cast(pl.Int64),
            mape=pl.col("error_pct").abs().mean(),
            bias_pct=pl.col("error_pct").mean(),
            coverage=pl.col("in_band").cast(pl.Float64).mean(),
        )
        .sort(by)
    )


def _comparisons(cells: pl.DataFrame) -> list[SourceComparison]:
    """For each official source, basecast and the source on the same (as_of, target_year) cells, by era and
    over every era, as the doc's score table pairs them."""
    scored = cells.filter(pl.col("error_pct").is_not_null())
    ours = scored.filter(pl.col("source") == "basecast").select("as_of", "target_year", "era", "error_pct")
    out: list[SourceComparison] = []
    for official in sorted(set(scored["source"].to_list()) - set(ABLATION)):
        theirs = scored.filter(pl.col("source") == official).select("as_of", "target_year", "error_pct")
        pairs = ours.join(theirs, on=["as_of", "target_year"], suffix="_official")
        eras = sorted(pairs["era"].unique().to_list())
        for era, block in [("all", pairs), *((e, pairs.filter(pl.col("era") == e)) for e in eras)]:
            if not block.height:
                continue
            out.append(
                SourceComparison(
                    era=era,
                    official_source=official,
                    n=block.height,
                    basecast_mape=block["error_pct"].abs().mean(),  # type: ignore[arg-type]
                    official_mape=block["error_pct_official"].abs().mean(),  # type: ignore[arg-type]
                    basecast_bias_pct=block["error_pct"].mean(),  # type: ignore[arg-type]
                    official_bias_pct=block["error_pct_official"].mean(),  # type: ignore[arg-type]
                )
            )
    return out


def _by_era(cells: pl.DataFrame) -> list[BacktestScore]:
    per_era = _scores(cells, ["era", "source"])
    overall = _scores(cells, ["source"]).with_columns(era=pl.lit("all")).select(per_era.columns)
    return [BacktestScore(**r) for r in pl.concat([per_era, overall]).to_dicts()]


@router.get(
    "/peak",
    response_model=PeakBacktestResponse,
    responses={
        422: {"model": InvalidAsOf, "description": "`as_of` is not a backtest date"},
        **PRODUCT_ERRORS,
    },
)
def peak(
    as_of: date | None = Query(None, description="One of data.as_of_dates; default the latest"),
) -> PeakBacktestResponse | JSONResponse:
    """Our model re-run at past dates against the official vintages of the same date and the actual summer
    peak: the cells of one date, the scores by era over every date, the paired comparison with each official
    source, the organic-only ablation and the latest fan."""
    cells = store.frame(CELLS)
    dates = sorted(set(cells["as_of"].to_list()))
    if as_of is not None and as_of not in dates:
        body = InvalidAsOf(detail="invalid_as_of", as_of_dates=dates)
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
    chosen = as_of or dates[-1]
    selected = cells.filter(pl.col("as_of") == chosen).sort(
        ["target_year", "source", "vintage"], nulls_last=True
    )
    scores = _by_era(cells)
    fan = store.frame(FAN)
    fan_year = int(fan["target_year"].max()) if fan.height else max(cells["target_year"].to_list(), default=0)
    fan = fan.filter(pl.col("target_year") == fan_year) if fan.height else fan
    actuals = store.frame(ACTUALS).sort("year")
    preliminary = not bool(actuals["final"].all()) if actuals.height else False
    verified = all_verified(cells, fan)
    return PeakBacktestResponse(
        meta=product_meta(
            CELLS,
            ACTUALS,
            FAN,
            rows=cells,
            verified=verified,
            caveats=["band_uncalibrated", *(["preliminary_actuals"] if preliminary else [])],
        ),
        data=PeakBacktestData(
            as_of=chosen,
            as_of_dates=dates,
            cells=[BacktestCell(**r) for r in selected.to_dicts()],
            scores=scores,
            comparisons=_comparisons(cells),
            ablation=[s for s in scores if s.era == "all" and s.source in ABLATION],
            eras=[Era(**e) for e in store.mart_meta("peak_backtest").get("eras", [])],
            actuals=[ActualPeak(**r) for r in actuals.to_dicts()],
            fan_target_year=fan_year,
            fan=[FanPoint(**r) for r in fan.to_dicts()],
        ),
    )


@router.get("/official-errors", response_model=OfficialErrorsResponse, responses=PRODUCT_ERRORS)
def official_errors(
    product: list[str] | None = Query(None, description="LTLF, CDR..."),
) -> OfficialErrorsResponse:
    """Every official summer-peak forecast (product × vintage × target year) against the actual, and the mean
    error by product and horizon."""
    df = store.frame(ERRORS)
    products = sorted(set(df["product"].to_list()))
    if product:
        df = df.filter(pl.col("product").is_in(product))
    df = df.sort(["product", "vintage", "target_year"])
    summary = (
        df.filter(pl.col("error_pct").is_not_null())
        .group_by(["product", "horizon"])
        .agg(
            n=pl.len().cast(pl.Int64),
            mape=pl.col("error_pct").abs().mean(),
            bias_pct=pl.col("error_pct").mean(),
        )
        .sort(["product", "horizon"])
    )
    preliminary = "actual_complete" in df.columns and not bool(df["actual_complete"].all())
    return OfficialErrorsResponse(
        meta=product_meta(ERRORS, rows=df, caveats=["preliminary_actuals"] if preliminary else []),
        data=OfficialErrorsData(
            items=[OfficialError(**r) for r in df.to_dicts()],
            summary=[HorizonSummary(**r) for r in summary.to_dicts()],
            products=products,
        ),
    )


@router.get("/queue", response_model=QueueBacktestResponse, responses=PRODUCT_ERRORS)
def queue() -> QueueBacktestResponse:
    """The generation-queue model re-run on past GIS reports, 24 months ahead: predicted vs built vs the
    developers' own dates, statewide and by fuel, and how well each ranks the counties."""
    df = store.frame(QUEUE).sort(["report_month", "stratum"])
    ranks = (
        df.group_by("report_month")
        .agg(
            rho_adj=pl.col("county_rho_adj").first(),
            rho_raw=pl.col("county_rho_raw").first(),
            rho_developer=pl.col("county_rho_developer").first(),
        )
        .sort("report_month")
    )
    return QueueBacktestResponse(
        meta=product_meta(QUEUE, rows=df),
        data=QueueBacktestData(
            items=[QueueBacktestRow(**r) for r in df.to_dicts()],
            county_rank=[CountyRankCheck(**r) for r in ranks.to_dicts()],
        ),
    )
