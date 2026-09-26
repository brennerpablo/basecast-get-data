"""The Explorer map: every Texas county with its acquisition priority, generation queue and new data centers
(docs/data-contract.md §1). One payload carries every layer; the map switches layers on the client."""

from __future__ import annotations

from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import all_verified, product_meta
from basecast_get_data.schemas.common import NOT_FOUND, PRODUCT_ERRORS
from basecast_get_data.schemas.geo import (
    AcquisitionWeights,
    CountiesData,
    CountiesResponse,
    CountyAccount,
    CountyAcquisition,
    CountyDataCenters,
    CountyDetail,
    CountyDetailResponse,
    CountyQueue,
    CountyQueueStratum,
    CountyRow,
    CountySignal,
    DataCenterSite,
    LegendClasses,
    SignalWeight,
)
from basecast_get_data.schemas.queue import QueueProject, Stratum

router = APIRouter(prefix="/geo", tags=["explorer"], dependencies=[Depends(require_token)])

COUNTIES, ACQUISITION, QUEUE, PROJECTS, SITES = (
    "county_weather_zone",
    "mart_county_acquisition",
    "mart_queue_adjusted_county",
    "mart_queue_project_scores",
    "mart_data_center_sites_new",
)
ACCOUNTS, ACCOUNT_COUNTIES = "mart_accounts", "mart_account_counties"
HORIZONS = [2027, 2028]
STRATA = ["all", "solar", "storage", "wind", "gas_other"]
TOP_PROJECTS = 10


def _latest_month(df: pl.DataFrame) -> pl.DataFrame:
    """The queue marts keep one block per GIS report month; the map shows the latest."""
    if "as_of_month" not in df.columns or not df.height:
        return df
    return df.filter(pl.col("as_of_month") == pl.col("as_of_month").max())


def _queue_layer(horizon: int, stratum: str) -> pl.DataFrame:
    q = _latest_month(store.frame(QUEUE)).filter(pl.col("stratum") == stratum)
    adj = f"adj_mw_{horizon}"
    return q.with_columns(
        adj_mw=pl.col(adj),
        ratio=pl.when(pl.col("raw_mw") > 0).then(pl.col(adj) / pl.col("raw_mw")),
        rank_raw=pl.col("raw_mw").rank("min", descending=True).cast(pl.Int64),
        rank_adj=pl.col(adj).rank("min", descending=True).cast(pl.Int64),
    ).with_columns(rank_change=pl.col("rank_raw") - pl.col("rank_adj"))


def _site_counts() -> pl.DataFrame:
    return (
        store.frame(SITES)
        .group_by("county_fips")
        .agg(
            sites=pl.len().cast(pl.Int64),
            sites_naics_only=(pl.col("matched_by") == "naics").sum().cast(pl.Int64),
        )
    )


def _acquisition(row: dict[str, Any] | None) -> CountyAcquisition | None:
    return CountyAcquisition(**row) if row and row.get("priority") is not None else None


def _weights() -> tuple[LegendClasses, AcquisitionWeights]:
    m = store.mart_meta("county_acquisition")
    breaks = m.get("legend_breaks", [])
    return (
        LegendClasses(breaks=breaks, classes=len(breaks) + 1),
        AcquisitionWeights(
            signals=[SignalWeight(**s) for s in m.get("signals", [])], grid_tilt=m.get("grid_tilt", 0)
        ),
    )


def _caveats(horizon: int) -> list[str]:
    codes = ["by_area_not_homes", "by_county_not_point"]
    return [*codes, "beyond_backtested_window"] if horizon > 2027 else codes


@router.get("/counties", response_model=CountiesResponse, responses=PRODUCT_ERRORS)
def counties(
    horizon: int = Query(
        2028,
        ge=2027,
        le=2028,
        description="Adjusted queue: expected MW reaching COD by December of this year",
    ),
    stratum: Stratum = Query("all"),
) -> CountiesResponse:
    """All 254 Texas counties in one payload: acquisition priority (null outside ERCOT), the generation queue
    for the horizon and stratum (null without an active project) and the new data-center sites."""
    queue = _queue_layer(horizon, stratum)
    acquisition = {r["county_fips"]: r for r in store.frame(ACQUISITION).to_dicts()}
    queue_rows = {r["county_fips"]: r for r in queue.to_dicts()}
    sites = {r["county_fips"]: r for r in _site_counts().to_dicts()}
    items = []
    for c in store.frame(COUNTIES).sort("county_fips").to_dicts():
        fips = c["county_fips"]
        q = queue_rows.get(fips)
        s = sites.get(fips, {"sites": 0, "sites_naics_only": 0})
        items.append(
            CountyRow(
                county_fips=fips,
                county_name=c["county_name"],
                weather_zone=c.get("weather_zone"),
                in_ercot=bool(c.get("in_ercot")),
                acquisition=_acquisition(acquisition.get(fips)),
                queue=CountyQueue(**q) if q else None,
                data_centers=CountyDataCenters(sites=s["sites"], sites_naics_only=s["sites_naics_only"]),
            )
        )
    legend, weights = _weights()
    acq = store.frame(ACQUISITION)
    months = queue["as_of_month"].drop_nulls() if "as_of_month" in queue.columns else pl.Series([])
    return CountiesResponse(
        meta=product_meta(ACQUISITION, QUEUE, SITES, rows=acq, caveats=_caveats(horizon)),
        data=CountiesData(
            items=items,
            horizon=horizon,  # type: ignore[arg-type]
            stratum=stratum,
            horizons=HORIZONS,
            strata=STRATA,
            queue_as_of_month=months.max() if months.len() else None,
            legend=legend,
            weights=weights,
        ),
    )


@router.get(
    "/counties/{county_fips}", response_model=CountyDetailResponse, responses={**NOT_FOUND, **PRODUCT_ERRORS}
)
def county_detail(county_fips: str = Path(..., pattern=r"^\d{5}$")) -> CountyDetailResponse:
    """One county: the priority's signals, drivers and drags, the queue by stratum and its largest projects,
    the data-center sites and the co-ops and munis that cover at least 1% of it."""
    county = store.frame(COUNTIES).filter(pl.col("county_fips") == county_fips)
    if not county.height:
        raise HTTPException(404, "not_found")
    c = county.row(0, named=True)
    acq_rows = store.frame(ACQUISITION).filter(pl.col("county_fips") == county_fips).to_dicts()
    acq = acq_rows[0] if acq_rows else None
    signals = [
        CountySignal(
            **s, raw=acq.get(s["signal"]) if acq else None, pct=acq.get(f"pct_{s['signal']}") if acq else None
        )
        for s in store.mart_meta("county_acquisition").get("signals", [])
    ]
    queue = _latest_month(store.frame(QUEUE)).filter(pl.col("county_fips") == county_fips)
    queue = queue.sort(pl.col("stratum").replace_strict({s: i for i, s in enumerate(STRATA)}, default=99))
    projects = _latest_month(store.frame(PROJECTS)).filter(pl.col("county_fips") == county_fips)
    sites = (
        store.frame(SITES)
        .filter(pl.col("county_fips") == county_fips)
        .sort("first_permit_date", descending=True)
    )
    links = store.frame(ACCOUNT_COUNTIES).filter(
        pl.col("county_fips") == county_fips, pl.col("county_share") >= 0.01
    )
    accounts = links.join(
        store.frame(ACCOUNTS).select("account_id", "name", "account_type", "rank", "tier", "next_action"),
        on="account_id",
    ).sort("county_share", descending=True)
    months = queue["as_of_month"].drop_nulls() if "as_of_month" in queue.columns else pl.Series([])
    return CountyDetailResponse(
        meta=product_meta(
            ACQUISITION,
            QUEUE,
            PROJECTS,
            SITES,
            ACCOUNT_COUNTIES,
            rows=store.frame(ACQUISITION),
            verified=all_verified(projects),
            caveats=_caveats(2028),
        ),
        data=CountyDetail(
            county_fips=county_fips,
            county_name=c["county_name"],
            weather_zone=c.get("weather_zone"),
            in_ercot=bool(c.get("in_ercot")),
            acquisition=_acquisition(acq),
            signals=signals,
            queue=[CountyQueueStratum(**r) for r in queue.to_dicts()],
            queue_as_of_month=months.max() if months.len() else None,
            top_projects=[
                QueueProject(**r)
                for r in projects.sort("mw_2028", descending=True).head(TOP_PROJECTS).to_dicts()
            ],
            data_centers=[DataCenterSite(**r) for r in sites.to_dicts()],
            accounts=[CountyAccount(**r) for r in accounts.to_dicts()],
        ),
    )
