"""The marts the product resources read: each one's resource group and the columns the API needs.

A group (accounts, explorer, forecast, backtest) is the unit that switches from fixtures to marts. The columns
a mart must have are its fixture's columns (the fixtures copy the marts' shape), less the ones the API can do
without.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"

GROUPS = ("accounts", "explorer", "forecast", "backtest", "insights")

MARTS: dict[str, str] = {
    "mart_accounts": "accounts",
    "mart_account_detail": "accounts",
    "mart_account_events": "accounts",
    "mart_account_counties": "accounts",
    # Not a mart: the processed table that lists the 254 counties.
    "county_weather_zone": "explorer",
    "mart_county_acquisition": "explorer",
    "mart_queue_adjusted_county": "explorer",
    "mart_queue_project_scores": "explorer",
    "mart_data_center_sites_new": "explorer",
    "mart_zone_layers": "explorer",
    "mart_county_large_load": "explorer",
    "mart_peak_forecast": "forecast",
    "mart_official_peak_lines": "forecast",
    "mart_large_load_realization": "forecast",
    "mart_large_load_in_service": "forecast",
    "mart_large_load_monthly": "forecast",
    "mart_annotations": "forecast",
    "mart_queue_stage_curves": "forecast",
    "mart_load_normalized_monthly": "forecast",
    "mart_load_normalized_annual": "forecast",
    "mart_four_cp_intervals": "forecast",
    "mart_four_cp_zone": "forecast",
    "mart_four_cp_dispatch_curve": "forecast",
    "mart_four_cp_scarcity": "forecast",
    "mart_four_cp_rates": "forecast",
    "mart_peak_backtest": "backtest",
    "mart_actual_summer_peaks": "backtest",
    "mart_backtest_fan": "backtest",
    "mart_official_forecast_errors": "backtest",
    "mart_queue_backtest": "backtest",
    "mart_insights": "insights",
}

# Marts read as records, never as a frame (the detail's payload mixes value types).
RECORDS_ONLY = frozenset({"mart_account_detail"})

# `mart_meta` rows (mart, key, value) belong to the group of the mart they describe.
META = "mart_meta"
META_GROUPS: dict[str, str] = {
    "accounts": "accounts",
    "glossary": "accounts",
    "county_acquisition": "explorer",
    "peak_forecast": "forecast",
    "load_normalized_monthly": "forecast",
    "four_cp_intervals": "forecast",
    "peak_backtest": "backtest",
}

# Columns the API reads only when present (provenance, ranks it recomputes, the detail's extra keys).
OPTIONAL = frozenset(
    {
        "model_version",
        "as_of",
        "as_of_month",
        "data_as_of",
        "built_at",
        "rank_raw",
        "rank_adj",
        "report_date",
        "note",
    }
)


def group_of(name: str) -> str:
    if name == META:
        raise ValueError("mart_meta spans groups: use META_GROUPS")
    return MARTS[name]


@cache
def required(name: str) -> frozenset[str]:
    rows = json.loads((FIXTURES / f"{name}.json").read_text())["rows"]
    columns = {c for r in rows for c in r}
    return frozenset(columns - OPTIONAL)
