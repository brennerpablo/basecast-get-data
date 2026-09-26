"""The 4CP (data-contract.md §3): ERCOT's four coincident summer peaks and what it takes to catch them."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import product_meta
from basecast_get_data.schemas.common import PRODUCT_ERRORS
from basecast_get_data.schemas.four_cp import (
    DispatchPoint,
    FourCpData,
    FourCpInterval,
    FourCpResponse,
    FourCpZone,
    Scarcity,
    TransmissionRate,
)

router = APIRouter(tags=["forecast"], dependencies=[Depends(require_token)])

INTERVALS, ZONES, DISPATCH, SCARCITY, RATES = (
    "mart_four_cp_intervals",
    "mart_four_cp_zone",
    "mart_four_cp_dispatch_curve",
    "mart_four_cp_scarcity",
    "mart_four_cp_rates",
)


@router.get("/four-cp", response_model=FourCpResponse, responses=PRODUCT_ERRORS)
def four_cp() -> FourCpResponse:
    """The 4CP intervals by year, each zone's load at them, the dispatch days a weather rule needs to catch
    all four, the shift of scarcity past 6 pm, and the transmission rate each year's 4CP is billed at."""
    intervals = store.frame(INTERVALS).sort(["year", "month"])
    zones = store.frame(ZONES).sort(["region_type", "region_id", "year"])
    dispatch = store.frame(DISPATCH).sort(["forecast", "window", "dispatch_days"])
    scarcity = store.frame(SCARCITY).sort("year")
    rates = store.frame(RATES).sort("charges_for_year")
    window = store.mart_meta("four_cp_intervals")
    preliminary = bool(intervals.height) and not bool(intervals["final"].all())
    return FourCpResponse(
        meta=product_meta(
            INTERVALS,
            ZONES,
            DISPATCH,
            SCARCITY,
            RATES,
            rows=intervals,
            caveats=["optimistic_weather", *(["preliminary_actuals"] if preliminary else [])],
        ),
        data=FourCpData(
            window_start_local=window.get("window_start_local"),
            window_end_local=window.get("window_end_local"),
            intervals=[FourCpInterval(**r) for r in intervals.to_dicts()],
            zones=[FourCpZone(**r) for r in zones.to_dicts()],
            dispatch_curve=[DispatchPoint(**r) for r in dispatch.to_dicts()],
            scarcity=[Scarcity(**r) for r in scarcity.to_dicts()],
            rates=[TransmissionRate(**r) for r in rates.to_dicts()],
        ),
    )
