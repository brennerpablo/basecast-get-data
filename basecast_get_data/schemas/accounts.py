"""Commercial intelligence: the account list, the per-account diagnosis and its events (contract §5).

`account_id` is the PUCT `ccn_no`. While the validation lock holds, no model here has an `is_base_partner`
field, and an account outside the scored universe answers 404 like any unknown id.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.common import Meta

AccountType = Literal["coop", "muni"]
Tier = Literal["A", "B", "C"]
NextAction = Literal["call_now", "nurture", "watch", "hold"]
Strength = Literal["strong", "context"]
RankScope = Literal["all", "within_type"]
AccountSort = Literal["rank", "score", "name", "meters", "latest_event_date", "action_changes_on"]
AccountFlag = Literal[
    "no_exposed_county", "apportionment_under", "apportionment_over", "short_form", "eia_break"
]

TRIGGER_DOC = (
    "Trigger code: dc_permit, gen_storage_ia, dev_agreement, dev_agreement_gen, market_registration, "
    "permit_surge, new_transmission, rate_increase, tsp_large_load (more may be added)."
)


class SignalValue(BaseModel):
    raw: float | None = None
    pct: float | None = Field(None, description="Percentile rank within the scored universe, 0–1")


class SignalDef(BaseModel):
    signal: str
    label: str
    unit: str | None = None
    weight: float


class TopTrigger(BaseModel):
    trigger: str = Field(description=TRIGGER_DOC)
    title: str
    event_date: date
    age_days: int


class AccountSummary(BaseModel):
    account_id: str = Field(description="PUCT ccn_no")
    name: str
    account_type: AccountType
    eia_utility_id: str | None = None
    gt: str | None = Field(None, description="G&T / wholesale supplier")
    primary_weather_zone: str | None = None
    meters: float | None = Field(None, description="EIA latest final year, bundled + delivery-only")
    score: float
    rank: int = Field(description="1 = best, over every account")
    rank_within_type: int = Field(description="1 = best among the accounts of the same type")
    tier: Tier
    signals: dict[str, SignalValue]
    next_action: NextAction
    action_changes_on: date | None = Field(None, description="The day the action lapses without a new event")
    action_changes_to: NextAction | None = Field(None, description="The action it lapses to")
    n_strong: int
    n_context: int
    latest_event_date: date | None = None
    top_trigger: TopTrigger | None = None
    active_triggers: list[str]
    flags: list[AccountFlag]
    simulated: bool = False


class AccountsData(BaseModel):
    items: list[AccountSummary]
    total: int
    rank_scope: RankScope
    signals: list[SignalDef]
    weights_set: str
    weights_status: str


class AccountsResponse(BaseModel):
    meta: Meta
    data: AccountsData


# --- detail -----------------------------------------------------------------------------------------------


class Fact(BaseModel):
    """One displayed value with where it comes from. `value = null` is a gap, never a zero."""

    key: str
    label: str
    value: float | str | None = None
    unit: str | None = None
    source: str
    as_of: date | None = None
    note: str | None = None
    simulated: bool = False
    verified: bool = True


class ScoreSignal(BaseModel):
    signal: str
    label: str
    raw: float | None = None
    unit: str | None = None
    pct: float | None = None
    weight: float
    weight_used: float = Field(description="Renormalized when a signal is missing")
    contribution: float = Field(description="Contributions sum to the score")
    source: str
    as_of: date | None = None


class AccountScore(BaseModel):
    score: float
    rank: int
    rank_within_type: int | None = None
    n_accounts: int
    tier: Tier
    method: str
    weights_set: str | None = None
    weights_status: str | None = None
    signals: list[ScoreSignal]


class Event(BaseModel):
    event_date: date
    age_days: int
    active: bool = Field(description="Inside the 12-month window")
    trigger: str = Field(description=TRIGGER_DOC)
    label: str
    strength: Strength
    title: str
    detail: str | None = None
    county_fips: str | None = Field(None, description="Null for a match by name")
    county_name: str | None = None
    exposure: float = Field(description="The account's share of the county, or 1 for a match by name")
    mapping: str = Field(description="county or name")
    source: str
    source_ref: str | None = None
    offer: str | None = None


class LeadTrigger(BaseModel):
    trigger: str
    title: str
    event_date: date
    source: str
    source_ref: str | None = None


class NextActionCard(BaseModel):
    action: NextAction
    action_label: str
    rule: str = Field(description="The rule that fired, in words")
    lead_trigger: LeadTrigger | None = None
    offer: str | None = None
    talking_points: list[str] = Field(default_factory=list)
    changes_on: date | None = None
    changes_to: NextAction | None = None


class ContextSummary(BaseModel):
    trigger: str
    label: str
    count: int
    latest_date: date
    counties: list[str] = Field(default_factory=list)


class AccountTriggers(BaseModel):
    active: list[Event] = Field(description="Every active strong event")
    context_summary: list[ContextSummary] = Field(description="One line per context trigger")
    history_count: int = Field(description="Every event ever; the full list is /accounts/{id}/events")


class AccountCounty(BaseModel):
    county_fips: str
    county_name: str
    overlap_km2: float
    county_share: float
    territory_share: float
    weather_zone: str | None = None
    exposed: bool = Field(description="The account covers ≥ 20% of the county")
    context: bool = Field(
        description="Used for the territory facts (the exposed counties, or the home county)"
    )


class ZoneShare(BaseModel):
    weather_zone: str
    area_share: float


class NearbyDataCenter(BaseModel):
    first_permit_date: date | None = None
    name: str
    tceq_rn: str
    county_name: str
    county_share: float
    exposed: bool
    context: bool = False
    matched_by: Literal["name", "naics"]


class TerritoryQueue(BaseModel):
    stratum: str
    projects_context: int | None = None
    raw_mw_context: float | None = None
    adj_mw_2027_context: float | None = None
    adj_mw_2028_context: float | None = None
    raw_mw_apportioned: float | None = None
    adj_mw_2027_apportioned: float | None = None
    adj_mw_2028_apportioned: float | None = None


class ZoneOutlook(BaseModel):
    zone: str
    ltlf_start: float | None = Field(None, description="LTLF summer peak, first year (MW)")
    ltlf_end: float | None = Field(None, description="LTLF summer peak, last year (MW)")
    ltlf_now: float | None = Field(None, description="LTLF summer peak for the current year (MW)")
    ltlf_cagr: float | None = None
    cp_year: int | None = None
    cp_avg_mw: float | None = None
    cf_summer: float | None = None
    share_4cp: float | None = None
    share_energy: float | None = None
    intensity: float | None = None
    ncp_summer_mw_full: float | None = None
    ncp_end_hour: float | None = Field(None, description="Local hour as a decimal (18.25 = 18:15)")
    hour_years: str | None = None
    ncp_now_mw: float | None = None
    ncp_now_months: int | None = None
    now_vs_ltlf: float | None = None
    peak_mismatch: Literal["early", "late"] | None = None
    q7_holdout_mape: float | None = None
    line: str | None = Field(None, description="The zone's 4CP talking point")


class Territory(BaseModel):
    facts: list[Fact]
    counties: list[AccountCounty]
    context_rule: Literal["exposed", "home_county"] = "exposed"
    context_label: str | None = None
    zones: list[ZoneShare]
    data_centers: list[NearbyDataCenter]
    queue: list[TerritoryQueue]
    zone_outlook: ZoneOutlook | None = None


class EiaYear(BaseModel):
    data_year: int
    early_release: bool
    form: Literal["long", "short"] | None = None
    customers: float | None = None
    delivery_customers: float | None = None
    meters: float | None = None
    sales_mwh: float | None = None
    revenue_kusd: float | None = None
    price_usd_kwh: float | None = None
    res_price_usd_kwh: float | None = None


class Gap(BaseModel):
    key: str
    kind: Literal["missing", "stale", "no_as_of", "structural", "quality", "coverage"]
    detail: str


class Coverage(BaseModel):
    public_data: bool = True
    utility_private_data: bool = False
    fleet_data: bool = False
    resolution: Literal["zone", "territory (simulated)"] = "zone"


# --- P1 blocks of the detail -----------------------------------------------------------------------------


class SupplierPoint(BaseModel):
    year: int
    mw: float


class SupplierCard(BaseModel):
    """The wholesale supplier's large-load requests (X13): requests, not forecasts, no location below it."""

    gt: str = Field(description="The wholesale supplier (G&T)")
    tsp: str | None = Field(None, description="Its row in the PUCT 58777 RFI")
    via: str = Field(description="g&t (through the supplier) or self")
    fact: str = Field(description="The sentence the card shows, built from the mart values")
    path: list[SupplierPoint] = Field(description="Requested MW by year (2026, 2030, 2032)")
    share_of_rfi: float | None = Field(
        None, description="The supplier's share of the ERCOT-wide RFI (near year)"
    )
    n_accounts: int | None = Field(None, description="Analysed accounts with the same supplier")
    filed_date: date | None = None
    source_ref: str | None = None
    fires_trigger: bool = Field(description="Whether it counts as the tsp_large_load context trigger")
    verified: bool = False


class FourCpRate(BaseModel):
    charges_for_year: int
    docket: str
    usd_per_mw_yr: float
    status: Literal["final", "pending"]
    billed_year: int


class FourCpOffer(BaseModel):
    """What a 4CP discharge is worth to the co-op (X3 + X15): avoided cost for the co-op, not Base revenue."""

    zone: str
    zone_line: str | None = Field(None, description="The zone's 4CP talking point")
    window_start_local: str
    window_end_local: str
    dispatch_days: float | None = Field(None, description="Dispatch days per summer a weather rule needs")
    rates: list[FourCpRate]
    note: str = Field(description="How to read the dollars: avoided cost, not revenue; fleet kW per home")
    account_4cp: Fact = Field(description="The account's own load at the 4CP: private data, a gap for now")
    verified: bool = True


class CityFacts(BaseModel):
    """Census place facts for a muni (X10), labelled as the city, not the territory."""

    place_name: str
    place_fips: str
    fit: Literal["same", "city_larger", "territory_larger"] = Field(
        description="How well the city describes the territory, by area"
    )
    place_in_territory: float | None = None
    territory_in_place: float | None = None
    facts: list[Fact]


class AccountDetail(BaseModel):
    account_id: str
    name: str
    account_type: AccountType
    as_of: date
    simulated: bool = False
    header: list[Fact]
    score: AccountScore
    next_action: NextActionCard
    triggers: AccountTriggers
    territory: Territory
    eia_series: list[EiaYear]
    gaps: list[Gap] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    suppliers: list[SupplierCard] = Field(default_factory=list, description="P1: the wholesale supplier card")
    four_cp_offer: FourCpOffer | None = Field(None, description="P1: the 4CP offer card")
    city: CityFacts | None = Field(None, description="P1: city facts, munis only")


class AccountDetailResponse(BaseModel):
    meta: Meta
    data: AccountDetail


class EventsPage(BaseModel):
    items: list[Event]
    total: int
    offset: int
    limit: int


class EventsResponse(BaseModel):
    meta: Meta
    data: EventsPage
