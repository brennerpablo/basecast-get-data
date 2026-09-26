"""Weather-normalized load by zone (docs/data-contract.md §3): the load at normal weather, month by month and
year by year."""

from __future__ import annotations

import polars as pl
from fastapi import APIRouter, Depends, Query

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import product_meta
from basecast_get_data.schemas.common import PRODUCT_ERRORS
from basecast_get_data.schemas.forecasts import Region
from basecast_get_data.schemas.load import (
    NormalizedLoadData,
    NormalizedLoadResponse,
    NormalizedMonth,
    NormalizedYear,
)

router = APIRouter(prefix="/load", tags=["forecast"], dependencies=[Depends(require_token)])

MONTHLY, ANNUAL = "mart_load_normalized_monthly", "mart_load_normalized_annual"
ORDER = ["ERCOT", "COAST", "EAST", "FWEST", "NCENT", "NORTH", "SCENT", "SOUTH", "WEST"]


@router.get("/normalized", response_model=NormalizedLoadResponse, responses=PRODUCT_ERRORS)
def normalized(
    region: Region = Query("ERCOT", description="ERCOT or a weather zone"),
) -> NormalizedLoadResponse:
    """The region's load actual and at normal weather: monthly (average, energy, peak) and yearly (energy and
    the summer peak under the normal weather years)."""
    monthly = store.frame(MONTHLY)
    annual = store.frame(ANNUAL)
    present = set(monthly["weather_zone"].unique().to_list()) if monthly.height else set()
    months = monthly.filter(pl.col("weather_zone") == region).sort("month")
    years = annual.filter(pl.col("weather_zone") == region).sort("year")
    m = store.mart_meta("load_normalized_monthly")
    return NormalizedLoadResponse(
        meta=product_meta(MONTHLY, ANNUAL, rows=months),
        data=NormalizedLoadData(
            region=region,
            regions=[r for r in ORDER if r in present],  # type: ignore[misc]
            normal_period=m.get("normal_period"),
            weather_source=m.get("weather_source"),
            monthly=[NormalizedMonth(**r) for r in months.to_dicts()],
            annual=[NormalizedYear(**r) for r in years.to_dicts()],
        ),
    )
