"""The peak forecast in three layers and the large-load flow (docs/data-contract.md §3)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import polars as pl
from fastapi import APIRouter, Depends, Query

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import all_verified, product_meta
from basecast_get_data.schemas.common import PRODUCT_ERRORS
from basecast_get_data.schemas.forecasts import (
    Annotation,
    InService,
    LargeLoadData,
    LargeLoadResponse,
    MonthlyStock,
    OfficialLine,
    PeakForecastData,
    PeakForecastResponse,
    PeakInputs,
    PeakLayer,
    PeakPoint,
    RatioBand,
    Realization,
    Region,
    Variant,
    VariantOption,
)
from basecast_get_data.schemas.queue import (
    CurveMilestone,
    CurvePoint,
    QueueCurvesData,
    QueueCurvesResponse,
    StageCurve,
    Stratum,
)

router = APIRouter(prefix="/forecasts", tags=["forecast"], dependencies=[Depends(require_token)])

PEAK, OFFICIAL = "mart_peak_forecast", "mart_official_peak_lines"
REALIZATION, IN_SERVICE, MONTHLY, ANNOTATIONS = (
    "mart_large_load_realization",
    "mart_large_load_in_service",
    "mart_large_load_monthly",
    "mart_annotations",
)
ZONES = ["COAST", "EAST", "FWEST", "NCENT", "NORTH", "SCENT", "SOUTH", "WEST"]
INPUT_COLUMNS = (
    "deck_vintage",
    "factor",
    "ratio_p10",
    "ratio_p50",
    "ratio_p90",
    "approved_stock_mw",
    "share_of_ll_u",
)


def _latest_run(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("as_of") == pl.col("as_of").max()) if df.height else df


def _default_variant() -> str:
    return store.mart_meta("peak_forecast").get("default_variant", "deck_pre_batch_zero")


def _variants(run: pl.DataFrame) -> list[VariantOption]:
    default = _default_variant()
    options = []
    for v in store.mart_meta("peak_forecast").get("variants", []):
        built = set(run.filter(pl.col("variant") == v["variant"])["region_id"].unique().to_list())
        regions = [r for r in ["ERCOT", *ZONES] if r in built]
        options.append(VariantOption(**v, is_default=v["variant"] == default, regions=regions))
    return options


def _inputs(rows: pl.DataFrame) -> PeakInputs:
    if not rows.height:
        return PeakInputs()
    first: dict[str, Any] = rows.row(0, named=True)
    return PeakInputs(**{c: first.get(c) for c in INPUT_COLUMNS}, verified=all_verified(rows))


@router.get("/peak", response_model=PeakForecastResponse, responses=PRODUCT_ERRORS)
def peak(
    region: Region = Query("ERCOT", description="ERCOT, or a weather zone (its coincident contribution)"),
    variant: Variant | None = Query(
        None, description="Which large-load input drives the layer; default in data"
    ),
) -> PeakForecastResponse:
    """Summer peak by year: the total with its P10–P90 band, the three layers (organic, large load,
    unattributed), ERCOT's official forecasts as lines and the inputs of the large-load layer."""
    chosen = variant or _default_variant()
    run = _latest_run(store.frame(PEAK))
    rows = run.filter(pl.col("region_id") == region, pl.col("variant") == chosen).sort("target_year")
    total = rows.filter(pl.col("layer") == "total")
    layers = rows.filter(pl.col("layer") != "total")
    official = (
        store.frame(OFFICIAL)
        .filter(pl.col("region_id") == region)
        .sort(["product", "vintage", "target_year"])
    )
    present = set(run["region_id"].unique().to_list()) if run.height else set()
    verified = all_verified(rows)
    variants = _variants(run)
    has_band = "band_kind" in rows.columns and (rows["band_kind"] == "p10_p90").any()
    caveats = ["band_uncalibrated"] if has_band else []
    if region != "ERCOT":
        caveats.append("allocated_statewide")
    first = rows.row(0, named=True) if rows.height else {}
    return PeakForecastResponse(
        meta=product_meta(PEAK, OFFICIAL, rows=rows, verified=verified, caveats=caveats),
        data=PeakForecastData(
            region=region,
            region_type="ercot" if region == "ERCOT" else "weather_zone",
            variant=chosen,  # type: ignore[arg-type]
            available=rows.height > 0,
            variants=variants,
            regions=[r for r in ["ERCOT", *ZONES] if r in present],
            run_id=first.get("run_id"),
            as_of=first.get("as_of"),
            series=[PeakPoint(**r) for r in total.to_dicts()],
            layers=[PeakLayer(**r) for r in layers.to_dicts()],
            official=[OfficialLine(**r) for r in official.to_dicts()],
            inputs=_inputs(rows),
        ),
    )


def _ratio_band() -> RatioBand:
    """The band the default variant's ERCOT forecast uses, so the chart and the forecast never disagree."""
    run = _latest_run(store.frame(PEAK))
    rows = run.filter(pl.col("region_id") == "ERCOT", pl.col("variant") == _default_variant())
    definition = store.mart_meta("peak_forecast").get("ratio_definition", "")
    if not rows.height:
        return RatioBand(definition=definition)
    r = rows.row(0, named=True)
    return RatioBand(
        p10=r.get("ratio_p10"),
        p50=r.get("ratio_p50"),
        p90=r.get("ratio_p90"),
        deck_vintage=r.get("deck_vintage"),
        definition=definition,
        verified=all_verified(rows),
    )


@router.get("/large-load", response_model=LargeLoadResponse, responses=PRODUCT_ERRORS)
def large_load() -> LargeLoadResponse:
    """What each ERCOT large-load deck promised against what got approved, the approved stock month by month,
    the realization-ratio band and dated annotations (policy changes, batches)."""
    realization = store.frame(REALIZATION).sort(["deck_vintage", "target_year"])
    in_service = store.frame(IN_SERVICE).sort(["deck_vintage", "in_service_year", "status"])
    monthly = store.frame(MONTHLY).sort("month")
    annotations = store.frame(ANNOTATIONS).sort("date")
    vintages: set[date] = set(realization["deck_vintage"].to_list()) | set(
        in_service["deck_vintage"].to_list()
    )
    return LargeLoadResponse(
        meta=product_meta(
            REALIZATION,
            IN_SERVICE,
            MONTHLY,
            ANNOTATIONS,
            rows=monthly,
            verified=all_verified(realization, in_service, monthly),
            caveats=["policy_pause_2026"],
        ),
        data=LargeLoadData(
            realization=[Realization(**r) for r in realization.to_dicts()],
            ratio_band=_ratio_band(),
            in_service=[InService(**r) for r in in_service.to_dicts()],
            monthly=[MonthlyStock(**r) for r in monthly.to_dicts()],
            annotations=[Annotation(**r) for r in annotations.to_dicts()],
            deck_vintages=sorted(vintages),
        ),
    )


CURVES = "mart_queue_stage_curves"
MILESTONES = (12, 24, 36, 48)


@router.get("/queue-curves", response_model=QueueCurvesResponse, responses=PRODUCT_ERRORS)
def queue_curves(
    stratum: list[Stratum] | None = Query(None),
    stage: list[str] | None = Query(None, description="entry or ia"),
    weighting: Literal["mw", "count"] | None = Query(None),
) -> QueueCurvesResponse:
    """Generation-queue survival: the share of projects (or MW) reaching COD, and withdrawing, by months since
    they entered the queue or signed their IA, per stratum. Values past the point where fewer than 10 projects
    remain at risk come back null."""
    df = store.frame(CURVES)
    if df.height and "as_of_month" in df.columns:
        df = df.filter(pl.col("as_of_month") == pl.col("as_of_month").max())
    for column, values in (("stratum", stratum), ("stage", stage)):
        if values:
            df = df.filter(pl.col(column).is_in(values))
    if weighting:
        df = df.filter(pl.col("weighting") == weighting)
    # No screen may show a value the curve does not support (fewer than 10 at risk).
    df = df.with_columns(
        [
            pl.when(pl.col("supported")).then(pl.col(c)).alias(c)
            for c in ("cif_cod", "cif_withdrawn", "survival")
        ]
    ).sort(["stage", "stratum", "weighting", "month"])
    curves = [
        StageCurve(
            stage=stage_,
            stratum=stratum_,
            weighting=weighting_,
            points=[CurvePoint(**p) for p in g.to_dicts()],
        )
        for (stage_, stratum_, weighting_), g in df.group_by(
            ["stage", "stratum", "weighting"], maintain_order=True
        )
    ]
    milestones = [
        CurveMilestone(**r)
        for r in df.filter(pl.col("month").is_in(MILESTONES))
        .select("stage", "stratum", "weighting", "month", "cif_cod", "supported")
        .to_dicts()
    ]
    month = df["as_of_month"].max() if df.height and "as_of_month" in df.columns else None
    return QueueCurvesResponse(
        meta=product_meta(CURVES, rows=df),
        data=QueueCurvesData(as_of_month=month, curves=curves, milestones=milestones),  # type: ignore[arg-type]
    )
