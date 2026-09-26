"""The closed list of caveat codes (docs/data-contract.md, "Ressalvas") and the standard text of each.

The app shows `label` as a badge and `text` as its tooltip, and never writes a caveat of its own, so a
required caveat never depends on someone remembering it. A new code goes here, in the contract and in
openapi.json together.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel

CaveatCode = Literal[
    "machine_read_unverified",
    "preliminary_actuals",
    "weights_pending_review",
    "band_uncalibrated",
    "beyond_backtested_window",
    "allocated_statewide",
    "by_county_not_point",
    "by_area_not_homes",
    "requests_not_forecasts",
    "policy_pause_2026",
    "optimistic_weather",
    "simulated",
    "fixture",
]

# code → (label, text). Texts carry no numbers that a model run can move.
CATALOG: dict[str, tuple[str, str]] = {
    "machine_read_unverified": (
        "Machine-read, not verified",
        "Read by machine from the charts of ERCOT's large-load decks and not yet checked by a person.",
    ),
    "preliminary_actuals": (
        "Preliminary actuals",
        "Summer 2026 loads are not final-settled yet (July and August), so the 2026 actual can still move.",
    ),
    "weights_pending_review": (
        "Weights pending review",
        "The score weights are a proposal awaiting review; scores, ranks and tiers can change once they are "
        "approved.",
    ),
    "band_uncalibrated": (
        "Band not calibrated",
        "The P10–P90 band is not calibrated: in the backtest it covered fewer outcomes than the nominal 80%.",
    ),
    "beyond_backtested_window": (
        "Beyond the backtested window",
        "This horizon is past the 24 months the queue model was backtested on.",
    ),
    "allocated_statewide": (
        "Allocated from a statewide forecast",
        "Large loads are forecast statewide and allocated to zones by fixed shares; a zone value is not a "
        "zone forecast of large load.",
    ),
    "by_county_not_point": (
        "By county, not by point",
        "Attributed through the county (the account's share of it), not by the site's location.",
    ),
    "by_area_not_homes": (
        "By land area, not homes",
        "Channel shares are measured by land area, not by homes or customers, so dense city cores are "
        "under-read.",
    ),
    "requests_not_forecasts": (
        "Requests, not forecasts",
        "Large-load requests reported by the transmission providers: requests, not forecasts, with no "
        "location below the provider.",
    ),
    "policy_pause_2026": (
        "Approvals paused on 2026-08-03",
        "ERCOT paused approvals of data-center and crypto loads of 75 MW or more on 2026-08-03; 2026 "
        "approval figures reflect that policy, not a realization rate.",
    ),
    "optimistic_weather": (
        "Observed weather (optimistic)",
        "Uses the observed ERA5 weather in place of a forecast, so the hit rates are optimistic.",
    ),
    "simulated": (
        "Simulated",
        "Comes from a simulated private-data adapter: not real data, and never used in scores, triggers, "
        "forecasts or backtests.",
    ),
    "fixture": (
        "Fixture",
        "Development fixture with invented values; not a model output.",
    ),
}

CODES: tuple[str, ...] = get_args(CaveatCode)


class Caveat(BaseModel):
    code: CaveatCode
    label: str
    text: str


class CaveatsResponse(BaseModel):
    """Not an envelope: the static catalog of caveat codes and their standard text."""

    items: list[Caveat]
