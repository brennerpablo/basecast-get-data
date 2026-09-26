"""Writes basecast_get_data/data/fixtures/: small invented copies of the marts, for the app to build against.

    uv run python scripts/make_contract_fixtures.py

Every value is invented with a fixed seed; only the county keys are real (fixture_data/tx_counties.csv, from
`county_weather_zone`). The accounts are fictional (ids FX001…, made-up names), so no real account, and no
held-out validation account, can appear in a fixture. The numbers are plausible but kept away from the real
ones of the analyses. Tables and columns follow basecast-airflow's docs/analysis/marts-proposal.md.
"""

from __future__ import annotations

import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from statistics import quantiles
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "basecast_get_data" / "data" / "fixtures"
COUNTIES_CSV = Path(__file__).resolve().parent / "fixture_data" / "tx_counties.csv"

SEED = 20260926
AS_OF = date(2026, 9, 20)
QUEUE_MONTH = date(2026, 9, 1)
MODEL_VERSION = "fixture-v2"
NOTE = "Fixture: invented values, not a model output (scripts/make_contract_fixtures.py)."
ZONES = ["COAST", "EAST", "FWEST", "NCENT", "NORTH", "SCENT", "SOUTH", "WEST"]

rng = random.Random(SEED)


def r1(x: float) -> float:
    return round(x, 1)


def r4(x: float) -> float:
    return round(x, 4)


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def write(name: str, rows: Any, key: str = "rows") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"note": NOTE, key: rows}
    (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")


def pct_ranks(values: list[float | None]) -> list[float | None]:
    """Percentile rank 0–1 among the non-null values, ties averaged."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    out: list[float | None] = []
    for v in values:
        if v is None or n < 2:
            out.append(None)
            continue
        below = sum(1 for x in present if x < v)
        equal = sum(1 for x in present if x == v)
        out.append(r4((below + (equal - 1) / 2) / (n - 1)))
    return out


def dense_rank(values: list[float], descending: bool = True) -> list[int]:
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=descending)
    ranks = [0] * len(values)
    for position, i in enumerate(order, start=1):
        ranks[i] = position
    return ranks


# --- counties ---------------------------------------------------------------------------------------------


def load_counties() -> list[dict[str, Any]]:
    with COUNTIES_CSV.open() as f:
        return [
            {
                "county_fips": r["county_fips"],
                "county_name": r["county_name"],
                "weather_zone": r["weather_zone"] or None,
                "in_ercot": r["in_ercot"] == "true",
            }
            for r in csv.DictReader(f)
        ]


COUNTIES = load_counties()
ERCOT = [c for c in COUNTIES if c["in_ercot"]]
BY_FIPS = {c["county_fips"]: c for c in COUNTIES}
BY_ZONE = {z: [c for c in ERCOT if c["weather_zone"] == z] for z in ZONES}

# --- generation queue --------------------------------------------------------------------------------------

STRATA = ["solar", "storage", "wind", "gas_other"]
FUEL = {"solar": "Solar", "storage": "Battery Energy Storage", "wind": "Wind", "gas_other": "Gas"}
CAPACITY = {"solar": (50, 600), "storage": (20, 400), "wind": (100, 500), "gas_other": (50, 1200)}
CDR_ZONE = {
    "COAST": "Coastal",
    "EAST": "North",
    "FWEST": "West",
    "NCENT": "North",
    "NORTH": "Panhandle",
    "SCENT": "South",
    "SOUTH": "South",
    "WEST": "West",
}


def month_add(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def make_projects() -> list[dict[str, Any]]:
    queue_counties = rng.sample(ERCOT, 150)
    projects = []
    for n in range(1, 321):
        county = rng.choice(queue_counties)
        stratum = rng.choices(STRATA, weights=[35, 35, 12, 18])[0]
        cap = r1(rng.uniform(*CAPACITY[stratum]))
        stage = "ia" if rng.random() < 0.3 else "entry"
        elapsed = r1(rng.uniform(1, 60))
        stage_date = month_add(QUEUE_MONTH, -int(elapsed))
        p27 = rng.uniform(0.12, 0.55) if stage == "ia" else rng.uniform(0.0, 0.1)
        p28 = min(0.9, p27 + rng.uniform(0.05, 0.25))
        projects.append(
            {
                "as_of_month": iso(QUEUE_MONTH),
                "inr": f"{rng.randint(19, 26)}FX{n:04d}",
                "project_name": f"Fixture {FUEL[stratum].split()[0]} {n}",
                "county_fips": county["county_fips"],
                "county_name": county["county_name"],
                "weather_zone": county["weather_zone"],
                "cdr_reporting_zone": CDR_ZONE[county["weather_zone"]],
                "fuel_type": FUEL[stratum],
                "stratum": stratum,
                "stage": stage,
                "stage_date": iso(stage_date),
                "elapsed_months": elapsed,
                "capacity_mw": cap,
                "projected_cod": iso(month_add(date(2026, 10, 1), rng.randint(0, 50))),
                "curve": "own" if stratum != "wind" else "pooled",
                "p_cod_2027": r4(p27),
                "p_cod_2028": r4(p28),
                "mw_2027": r1(cap * p27),
                "mw_2028": r1(cap * p28),
                "clamped_2027": elapsed > 54,
                "clamped_2028": elapsed > 50,
                "model_version": MODEL_VERSION,
            }
        )
    return projects


def county_queue(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in projects:
        for stratum in ("all", p["stratum"]):
            groups.setdefault((p["county_fips"], stratum), []).append(p)
    rows = []
    for (fips, stratum), ps in sorted(groups.items()):
        c = BY_FIPS[fips]
        raw = sum(p["capacity_mw"] for p in ps)
        a27 = sum(p["mw_2027"] for p in ps)
        a28 = sum(p["mw_2028"] for p in ps)
        gas = sum(p["mw_2028"] for p in ps if p["stratum"] == "gas_other" and p["capacity_mw"] >= 500)
        rows.append(
            {
                "as_of_month": iso(QUEUE_MONTH),
                "county_fips": fips,
                "county_name": c["county_name"],
                "weather_zone": c["weather_zone"],
                "cdr_reporting_zone": CDR_ZONE[c["weather_zone"]],
                "stratum": stratum,
                "projects": len(ps),
                "projects_ia": sum(1 for p in ps if p["stage"] == "ia"),
                "raw_mw": r1(raw),
                "raw_mw_ia": r1(sum(p["capacity_mw"] for p in ps if p["stage"] == "ia")),
                "adj_mw_2027": r1(a27),
                "adj_mw_2028": r1(a28),
                "ratio_2027": r4(a27 / raw) if raw else None,
                "ratio_2028": r4(a28 / raw) if raw else None,
                "large_gas_mw_2028": r1(gas) if stratum in ("all", "gas_other") else None,
                "model_version": MODEL_VERSION,
            }
        )
    for stratum in ("all", *STRATA):
        block = [r for r in rows if r["stratum"] == stratum]
        for r, a, b in zip(
            block,
            dense_rank([r["raw_mw"] for r in block]),
            dense_rank([r["adj_mw_2028"] for r in block]),
            strict=True,
        ):
            r["rank_raw"], r["rank_adj"] = a, b
    return rows


# --- data-center sites ------------------------------------------------------------------------------------


def make_sites(account_fips: list[str]) -> list[dict[str, Any]]:
    pool = rng.sample(ERCOT, 10) + [BY_FIPS[f] for f in account_fips[:4]]
    sites = []
    for n in range(1, 21):
        c = rng.choice(pool)
        first = date(2025, 1, 1) + timedelta(days=rng.randint(0, (AS_OF - date(2025, 1, 1)).days))
        density = r1(rng.uniform(4, 900))
        sites.append(
            {
                "tceq_rn": f"RNFX{n:06d}",
                "site_name": f"FIXTURE DATA CAMPUS {n}",
                "county_fips": c["county_fips"],
                "county_name": c["county_name"],
                "city": rng.choice([None, None, "Sampleton", "Exampleville"]),
                "first_permit_date": iso(first),
                "matched_by": "name" if n % 2 else "naics",
                "has_undated_affiliation": n % 7 == 0,
                "largest_type": rng.choice(["coop", "coop", "iou", "muni"]),
                "coop_share_w": r4(rng.uniform(0, 1)),
                "iso_class": "ercot" if n % 9 else "mixed",
                "density_per_km2": density,
                "metro_legacy": density > 400,
                "metro_density": density >= 100,
                "as_of": iso(AS_OF),
                "model_version": MODEL_VERSION,
            }
        )
    return sites


# --- county acquisition -----------------------------------------------------------------------------------

COUNTY_SIGNALS = [
    ("addr_sf_homes", "Owner-occupied single-family homes × addressable share", "market", "homes", 0.40),
    ("pop_growth", "Population growth 2020→2025", "market", "share", 0.25),
    ("permits_per_1k", "Permits 2023–2025 per 1,000 residents", "market", "units/1k", 0.15),
    ("owner_sf_share", "Owner-occupied single-family share", "market", "share", 0.20),
    ("zone_peak_cagr", "Weather-normalized zone summer peak CAGR", "grid", "per year", 0.30),
    ("ll_pressure", "Zone summer-peak excess over the pre-2020 fit", "grid", "share", 0.25),
    ("lz_spread", "Load-zone daily 2-hour price spread", "grid", "USD/MWh", 0.25),
    ("dc_sites", "New data-center sites since 2025", "grid", "sites", 0.20),
]
GRID_TILT = 0.35


def make_acquisition(sites: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[float]]:
    zone_values = {
        z: {
            "zone_peak_cagr": rng.uniform(0.005, 0.06),
            "ll_pressure": rng.uniform(0, 0.2),
            "lz_spread": rng.uniform(18, 70),
        }
        for z in ZONES
    }
    site_counts = {f: sum(1 for s in sites if s["county_fips"] == f) for f in BY_FIPS}
    raw: dict[str, list[float | None]] = {s[0]: [] for s in COUNTY_SIGNALS}
    shares = []
    for c in ERCOT:
        mix = [rng.gammavariate(a, 1) for a in (1.2, 1.4, 0.4, 0.15)]
        total = sum(mix)
        retail, coop, muni, outside = (m / total for m in mix)
        shares.append((retail, coop, muni, outside))
        homes = math.exp(rng.uniform(5, 12))
        raw["addr_sf_homes"].append(r1(homes * (1 - outside)))
        raw["pop_growth"].append(r4(rng.uniform(-0.06, 0.3)))
        raw["permits_per_1k"].append(None if rng.random() < 0.05 else r1(rng.uniform(0, 45)))
        raw["owner_sf_share"].append(r4(rng.uniform(0.45, 0.85)))
        for key in ("zone_peak_cagr", "ll_pressure", "lz_spread"):
            raw[key].append(r4(zone_values[c["weather_zone"]][key]))
        raw["dc_sites"].append(float(site_counts[c["county_fips"]]))
    pcts = {k: pct_ranks(v) for k, v in raw.items()}

    def block_score(i: int, block: str) -> float:
        parts = [(w, pcts[k][i]) for k, _, b, _, w in COUNTY_SIGNALS if b == block and pcts[k][i] is not None]
        weight = sum(w for w, _ in parts)
        return sum(w * p for w, p in parts) / weight

    rows = []
    for i, c in enumerate(ERCOT):
        market, grid = block_score(i, "market"), block_score(i, "grid")
        factor = 1 - GRID_TILT + GRID_TILT * grid
        retail, coop, muni, outside = shares[i]
        partner = coop + muni
        channel = "retail_direct" if retail >= 0.5 else "partnership" if partner >= 0.5 else "mixed"
        ranked = sorted(((pcts[k][i], k) for k, *_ in COUNTY_SIGNALS if pcts[k][i] is not None), reverse=True)
        n_partners = rng.randint(1, 4) if partner > 0.02 else 0
        row = {
            "county_fips": c["county_fips"],
            "county_name": c["county_name"],
            "weather_zone": c["weather_zone"],
            "priority": r4(market * factor),
            "market_score": r4(market),
            "grid_score": r4(grid),
            "grid_factor": r4(factor),
            "channel": channel,
            "partner_type": (None if partner == 0 else "coop" if coop >= muni else "muni"),
            "retail_share": r4(retail),
            "coop_share": r4(coop),
            "muni_share": r4(muni),
            "outside_share": r4(outside),
            "partner_share": r4(partner),
            "addressable_share": r4(1 - outside),
            "n_partners": n_partners,
            "top_partner_share": r4(partner / n_partners * rng.uniform(1, 1.6)) if n_partners else None,
            "drivers": [k for _, k in ranked[:2]],
            "drags": [k for _, k in ranked[-2:]][::-1],
            "as_of": iso(AS_OF),
            "model_version": MODEL_VERSION,
        }
        for k, *_ in COUNTY_SIGNALS:
            row[k] = raw[k][i]
            row[f"pct_{k}"] = pcts[k][i]
        rows.append(row)
    for r, rank in zip(rows, dense_rank([r["priority"] for r in rows]), strict=True):
        r["rank"] = rank
    breaks = [r4(b) for b in quantiles([r["priority"] for r in rows], n=5)]
    for r in rows:
        r["priority_class"] = 1 + sum(1 for b in breaks if r["priority"] > b)
    for share, rank_key in (("retail_share", "retail_rank"), ("partner_share", "partner_rank")):
        listed = sorted((r for r in rows if r[share] >= 0.25), key=lambda r: -r["priority"])
        for position, r in enumerate(listed, start=1):
            r[rank_key] = position
        for r in rows:
            r.setdefault(rank_key, None)
    return rows, breaks


# --- accounts ---------------------------------------------------------------------------------------------

ACCOUNTS = [
    ("FX001", "Fixture Valley Electric Cooperative", "coop", "NCENT", "Sample G&T Cooperative"),
    ("FX002", "Sample City Utilities", "muni", "SCENT", None),
    ("FX003", "Example Plains Electric Cooperative", "coop", "WEST", "Sample G&T Cooperative"),
    ("FX004", "Demo Springs Municipal Utilities", "muni", "COAST", "Demo Power Supply Cooperative"),
    ("FX005", "Placeholder Ridge Electric Cooperative", "coop", "FWEST", "Demo Power Supply Cooperative"),
    ("FX006", "Testing Bend Public Utilities", "muni", "NORTH", None),
    ("FX007", "Mock County Electric Cooperative", "coop", "EAST", "Sample G&T Cooperative"),
    ("FX008", "Stub Creek Municipal Power", "muni", "SOUTH", "Demo Power Supply Cooperative"),
]
ACCOUNT_SIGNALS = [
    ("pop_growth", "Population growth 2020→2025", "share", 0.20, "census_population_county", "2025-07-01"),
    (
        "permits_per_1k",
        "Permits 2023–2025 per 1,000 residents",
        "units/1k",
        0.15,
        "census_permits_county",
        "2025-12-31",
    ),
    (
        "owner_sf_homes",
        "Owner-occupied single-family homes",
        "homes",
        0.20,
        "census_housing_county",
        "2024-12-31",
    ),
    (
        "dc_sites",
        "New data-center sites since 2025 (expected)",
        "sites",
        0.15,
        "tceq_data_center_sites",
        None,
    ),
    (
        "owner_sf_share",
        "Owner-occupied single-family share",
        "share",
        0.10,
        "census_housing_county",
        "2024-12-31",
    ),
    ("zone_peak_cagr", "Zone summer peak CAGR (LTLF)", "per year", 0.20, "ltlf_forecasts", "2025-10-06"),
]
TRIGGERS = {
    "dc_permit": (
        "New data-center air permit in the territory",
        "strong",
        "tceq_data_center_sites",
        "capacity: large-load pressure on the co-op's peak and 4CP; offer VPP capacity",
    ),
    "gen_storage_ia": (
        "Generation or storage project signed its interconnection agreement nearby",
        "strong",
        "gis_project_events",
        "local capacity is being built by others; position distributed storage as the member-owned option",
    ),
    "dev_agreement": (
        "Local economic-development agreement (Ch. 312 / Ch. 380)",
        "strong",
        "cpa_local_dev_agreements",
        "new commercial/industrial load coming; offer peak shaving before it lands",
    ),
    "market_registration": (
        "Account registered a new ERCOT market role (LSE / QSE / TDSP / RE)",
        "strong",
        "ercot_market_participants",
        "wholesale set-up is changing; offer a QSE-ready VPP",
    ),
    "permit_surge": (
        "Residential permits up ≥ 25% (last 12 months vs the 12 before)",
        "strong",
        "census_permits_county",
        "new homes: batteries at construction, builder partnerships",
    ),
    "new_transmission": (
        "New transmission project listed in ERCOT TPIT touching the territory",
        "context",
        "tpit_projects",
        "grid constraint in the area; storage as a non-wires alternative",
    ),
    "rate_increase": (
        "Residential average price up ≥ 10% (EIA-861)",
        "context",
        "eia861_sales",
        "bill pressure: demand-charge / 4CP savings for members",
    ),
    "tsp_large_load": (
        "The account's G&T / TSP reported large-load requests (PUCT 58777 RFI)",
        "context",
        "puct_tsp_large_load_requests",
        "wholesale supplier faces large-load growth; capacity costs likely to rise",
    ),
}
COUNTY_TRIGGERS = ["dc_permit", "gen_storage_ia", "permit_surge", "new_transmission"]
NAME_TRIGGERS = ["dev_agreement", "market_registration", "rate_increase", "tsp_large_load"]
ACTION_LABEL = {"call_now": "Call now", "nurture": "Nurture", "watch": "Watch", "hold": "Hold"}
# The glossary's shape (mart_meta, mart "glossary"); basecast-airflow writes the real one from its config.
TRIGGER_CHIPS = {
    "dc_permit": "Data-center permit",
    "gen_storage_ia": "Gen/storage IA nearby",
    "dev_agreement": "Dev agreement",
    "market_registration": "New ERCOT role",
    "permit_surge": "Permit surge",
    "new_transmission": "New transmission",
    "rate_increase": "Rate increase",
    "tsp_large_load": "G&T large-load requests",
}
FLAGS = {
    "no_exposed_county": (
        "No exposed county",
        "Covers no county at 20% or more, so county triggers cannot fire.",
    ),
    "apportionment_under": (
        "Under-counted by area",
        "Area apportionment reads the territory smaller than its meters.",
    ),
    "apportionment_over": (
        "Over-counted by area",
        "Area apportionment reads the territory larger than its meters.",
    ),
    "short_form": ("EIA short form", "Files the EIA-861 short form: totals only, no residential price."),
    "eia_break": ("EIA series break", "The EIA customer series jumps between two years."),
}
ACTION_TEXT = {
    "call_now": "Top tiers with an active strong trigger (fixture wording).",
    "nurture": "An active strong trigger outside the top tiers (fixture wording).",
    "watch": "Only context triggers are active (fixture wording).",
    "hold": "No active trigger (fixture wording).",
}


def glossary() -> list[dict[str, Any]]:
    items = [
        {"kind": "next_action", "code": code, "label": label, "text": ACTION_TEXT[code], "strength": None}
        for code, label in ACTION_LABEL.items()
    ]
    items += [
        {"kind": "trigger", "code": code, "label": TRIGGER_CHIPS[code], "text": text, "strength": strength}
        for code, (text, strength, _, _) in TRIGGERS.items()
    ]
    items += [
        {"kind": "flag", "code": code, "label": label, "text": text, "strength": None}
        for code, (label, text) in FLAGS.items()
    ]
    return items


def account_counties(account_id: str, kind: str, zone: str) -> list[dict[str, Any]]:
    picks = rng.sample(BY_ZONE[zone], 4 if kind == "coop" else 2)
    rows = []
    for i, c in enumerate(picks):
        share = rng.uniform(0.15, 0.7) if kind == "coop" else rng.uniform(0.004, 0.05)
        if kind == "coop" and i == 3:
            share = rng.uniform(0.02, 0.1)
        overlap = share * rng.uniform(1500, 2800)
        rows.append(
            {
                "account_id": account_id,
                "county_fips": c["county_fips"],
                "county_name": c["county_name"],
                "overlap_km2": r1(overlap),
                "county_share": r4(share),
                "weather_zone": zone,
                "exposed": share >= 0.2,
            }
        )
    total = sum(r["overlap_km2"] for r in rows)
    for r in rows:
        r["territory_share"] = r4(r["overlap_km2"] / total)
    rows.sort(key=lambda r: -r["overlap_km2"])
    exposed = any(r["exposed"] for r in rows)
    for i, r in enumerate(rows):
        r["context"] = r["exposed"] if exposed else i == 0
        r["model_version"] = MODEL_VERSION
        r["as_of"] = iso(AS_OF)
    return rows


def make_events(
    account_id: str, kind: str, gt: str | None, counties: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    exposed = [c for c in counties if c["exposed"]]
    options = NAME_TRIGGERS + (COUNTY_TRIGGERS if exposed else [])
    if not gt:
        options = [t for t in options if t != "tsp_large_load"]
    events = []
    for n in range(rng.randint(4, 14)):
        trigger = rng.choice(options)
        label, strength, source, offer = TRIGGERS[trigger]
        day = AS_OF - timedelta(days=rng.randint(3, 900))
        county = rng.choice(exposed) if trigger in COUNTY_TRIGGERS else None
        title = {
            "dc_permit": f"FIXTURE DATA CAMPUS {rng.randint(21, 99)}",
            "gen_storage_ia": f"Fixture Storage {rng.randint(400, 499)}",
            "dev_agreement": "Example Manufacturing Co.",
            "market_registration": f"Registered as {rng.choice(['QSE', 'LSE', 'RE'])}",
            "permit_surge": f"{county['county_name'] if county else ''}: permits up (12 months)",
            "new_transmission": f"Sample {rng.choice([69, 138, 345])} kV substation upgrade",
            "rate_increase": "Residential price up (2024 → 2025 early release)",
            "tsp_large_load": f"{gt} reported large-load requests",
        }[trigger]
        age = (AS_OF - day).days
        events.append(
            {
                "account_id": account_id,
                "event_date": iso(day),
                "age_days": age,
                "active": age <= 365,
                "trigger": trigger,
                "label": label,
                "strength": strength,
                "title": title,
                "detail": "Fixture event" if n % 3 else None,
                "county_fips": county["county_fips"] if county else None,
                "county_name": county["county_name"] if county else None,
                "exposure": county["county_share"] if county else 1.0,
                "mapping": "county" if county else "name",
                "source": source,
                "source_ref": f"FX-{trigger}-{account_id}-{n}",
                "offer": offer,
                "model_version": MODEL_VERSION,
                "as_of": iso(AS_OF),
            }
        )
    events.sort(key=lambda e: e["event_date"], reverse=True)
    return events


def make_accounts(dc_sites: list[dict[str, Any]], queue: list[dict[str, Any]]):
    links, events, base = [], [], []
    for n, (account_id, name, kind, zone, gt) in enumerate(ACCOUNTS):
        counties = account_counties(account_id, kind, zone)
        links += counties
        evs = make_events(account_id, kind, gt, counties)
        events += evs
        base.append(
            {
                "account_id": account_id,
                "name": name,
                "kind": kind,
                "zone": zone,
                "gt": gt,
                "n": n,
                "counties": counties,
                "events": evs,
            }
        )
    raw = {
        "pop_growth": [r4(rng.uniform(-0.02, 0.28)) for _ in base],
        "permits_per_1k": [r1(rng.uniform(1, 42)) for _ in base],
        "owner_sf_homes": [r1(rng.uniform(300, 45000)) for _ in base],
        "dc_sites": [r4(rng.uniform(0, 2.5)) for _ in base],
        "owner_sf_share": [r4(rng.uniform(0.4, 0.8)) for _ in base],
        "zone_peak_cagr": [r4(rng.uniform(0.01, 0.09)) for _ in base],
    }
    pcts = {k: pct_ranks(v) for k, v in raw.items()}
    scores = [sum(w * (pcts[k][i] or 0) for k, _, _, w, _, _ in ACCOUNT_SIGNALS) for i in range(len(base))]
    ranks = dense_rank(scores)
    by_type: dict[str, list[int]] = {}
    for i, b in enumerate(base):
        by_type.setdefault(b["kind"], []).append(i)
    within = [0] * len(base)
    for idx in by_type.values():
        for i, r in zip(idx, dense_rank([scores[i] for i in idx]), strict=True):
            within[i] = r

    rows, details = [], []
    for i, b in enumerate(base):
        rank = ranks[i]
        tier = "A" if rank <= 2 else "B" if rank <= 4 else "C"
        active = [e for e in b["events"] if e["active"]]
        strong = [e for e in active if e["strength"] == "strong"]
        context = [e for e in active if e["strength"] == "context"]
        lead = strong[0] if strong else None
        if strong and tier in "AB":
            action, changes_to = "call_now", "nurture"
        elif strong:
            action, changes_to = "nurture", "watch" if context else "hold"
        elif context:
            action, changes_to = "watch", "hold"
        else:
            action, changes_to = "hold", None
        if lead:
            changes_on = date.fromisoformat(lead["event_date"]) + timedelta(days=365)
        elif context:
            changes_on = date.fromisoformat(context[0]["event_date"]) + timedelta(days=365)
        else:
            changes_on = None
        flags = []
        if not any(c["exposed"] for c in b["counties"]):
            flags += ["no_exposed_county", "apportionment_under"]
        if b["account_id"] == "FX006":
            flags.append("short_form")
        meters = r1(rng.uniform(8000, 60000) if b["kind"] == "coop" else rng.uniform(2500, 80000))
        signals = {k: {"raw": raw[k][i], "pct": pcts[k][i]} for k in raw}
        row = {
            "account_id": b["account_id"],
            "name": b["name"],
            "account_type": b["kind"],
            "eia_utility_id": f"99{9001 + i}",
            "gt": b["gt"],
            "primary_weather_zone": b["zone"],
            "meters": meters,
            "score": r4(scores[i]),
            "rank": rank,
            "rank_within_type": within[i],
            "tier": tier,
            "signals": signals,
            "next_action": action,
            "action_changes_on": iso(changes_on),
            "n_strong": len(strong),
            "n_context": len(context),
            "latest_event_date": b["events"][0]["event_date"] if b["events"] else None,
            "top_trigger": (
                {
                    "trigger": lead["trigger"],
                    "title": lead["title"],
                    "event_date": lead["event_date"],
                    "age_days": lead["age_days"],
                }
                if lead
                else None
            ),
            "active_triggers": sorted({e["trigger"] for e in active}),
            "flags": flags,
            "simulated": False,
            "weights_set": "q3",
            "weights_status": "pending_review",
            "as_of": iso(AS_OF),
            "model_version": MODEL_VERSION,
        }
        rows.append(row)
        details.append(
            {
                "account_id": b["account_id"],
                "as_of": iso(AS_OF),
                "model_version": MODEL_VERSION,
                "payload": detail_payload(
                    b,
                    row,
                    raw,
                    pcts,
                    i,
                    lead,
                    strong,
                    context,
                    action,
                    changes_on,
                    changes_to,
                    dc_sites,
                    queue,
                ),
            }
        )
    rows.sort(key=lambda r: r["rank"])
    return rows, details, events, links


def fact(key, label, value, unit, source, as_of, note=None):
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "source": source,
        "as_of": as_of,
        "note": note,
        "simulated": False,
        "verified": True,
    }


def detail_payload(
    b, row, raw, pcts, i, lead, strong, context, action, changes_on, changes_to, dc_sites, queue
):
    counties = b["counties"]
    ctx = [c for c in counties if c["context"]]
    context_rule = "exposed" if any(c["exposed"] for c in counties) else "home_county"
    context_label = (
        f"{len(ctx)} exposed counties (share ≥ 20%)"
        if context_rule == "exposed"
        else f"home county: {ctx[0]['county_name']}"
    )
    series = []
    meters = row["meters"] * 0.8
    price = rng.uniform(0.08, 0.11)
    for year in range(2019, 2026):
        meters *= rng.uniform(1.0, 1.06)
        price *= rng.uniform(0.97, 1.09)
        sales = meters * rng.uniform(13, 18)
        series.append(
            {
                "data_year": year,
                "early_release": year == 2025,
                "form": "short" if b["account_id"] == "FX006" else "long",
                "customers": r1(meters),
                "delivery_customers": 0.0,
                "meters": r1(meters),
                "sales_mwh": r1(sales),
                "revenue_kusd": r1(sales * price),
                "price_usd_kwh": r4(price),
                "res_price_usd_kwh": None if b["account_id"] == "FX006" else r4(price * 1.12),
            }
        )
    last = series[-2]
    first = series[0]
    cagr = lambda a, z: r4((z / a) ** (1 / 5) - 1)  # noqa: E731
    eia_src = "eia861_sales"
    header = [
        fact("name", "Name", b["name"], None, "puct_ccn_territories", "2026-06-29"),
        fact("account_type", "Type", b["kind"], None, "puct_ccn_territories", "2026-06-29"),
        fact("ccn_no", "PUCT CCN", b["account_id"], None, "puct_ccn_territories", "2026-06-29"),
        fact(
            "gt",
            "G&T / wholesale supplier",
            b["gt"],
            None,
            "puct_ccn_territories",
            "2026-06-29",
            None if b["gt"] else "no G&T in the PUCT layer",
        ),
        fact(
            "eia_utility_id",
            "EIA-861 utility id",
            row["eia_utility_id"],
            None,
            "utility_crosswalk",
            iso(AS_OF),
        ),
        fact(
            "counties",
            "Counties (largest overlap first)",
            ", ".join(c["county_name"] for c in counties),
            None,
            "county_utility_overlap_puct",
            iso(AS_OF),
            f"{len(counties)} counties",
        ),
        fact(
            "territory_km2",
            "Territory area in ERCOT counties",
            r1(sum(c["overlap_km2"] for c in counties)),
            "km²",
            "county_utility_overlap_puct",
            iso(AS_OF),
        ),
        fact("eia_form", "EIA-861 form (latest year)", last["form"], None, eia_src, "2024-12-31"),
        fact(
            "customers",
            "Meters (all classes, incl. delivery-only)",
            last["meters"],
            "customers",
            eia_src,
            "2024-12-31",
        ),
        fact(
            "customer_cagr",
            "Meter growth 2019→2024",
            cagr(first["meters"], last["meters"]),
            "per year",
            eia_src,
            "2024-12-31",
        ),
        fact("sales_mwh", "Retail sales", last["sales_mwh"], "MWh", eia_src, "2024-12-31"),
        fact("revenue_kusd", "Retail revenue", last["revenue_kusd"], "thousand USD", eia_src, "2024-12-31"),
        fact("price", "Average price (all classes)", last["price_usd_kwh"], "USD/kWh", eia_src, "2024-12-31"),
        fact(
            "price_cagr",
            "Price trend 2019→2024",
            cagr(first["price_usd_kwh"], last["price_usd_kwh"]),
            "per year",
            eia_src,
            "2024-12-31",
        ),
        fact(
            "res_price",
            "Residential price",
            last["res_price_usd_kwh"],
            "USD/kWh",
            eia_src,
            "2024-12-31",
            "long form only",
        ),
        fact(
            "price_early",
            "Average price, early release",
            series[-1]["price_usd_kwh"],
            "USD/kWh",
            eia_src,
            "2025-12-31",
            "early release, not final",
        ),
        fact(
            "ercot_roles",
            "ERCOT market roles registered (ever)",
            "LSE, QSE",
            None,
            "ercot_market_participants",
            iso(AS_OF),
        ),
    ]
    ctx_fips = {c["county_fips"] for c in ctx}
    q_all = [q for q in queue if q["county_fips"] in ctx_fips and q["stratum"] == "all"]
    q_sto = [q for q in queue if q["county_fips"] in ctx_fips and q["stratum"] == "storage"]
    share = {c["county_fips"]: c["county_share"] for c in counties}
    queue_rows = []
    for stratum, qs in (("total", q_all), ("storage", q_sto)):
        if not qs:
            continue
        queue_rows.append(
            {
                "stratum": stratum,
                "projects_context": sum(q["projects"] for q in qs),
                "raw_mw_context": r1(sum(q["raw_mw"] for q in qs)),
                "adj_mw_2027_context": r1(sum(q["adj_mw_2027"] for q in qs)),
                "adj_mw_2028_context": r1(sum(q["adj_mw_2028"] for q in qs)),
                "raw_mw_apportioned": r1(sum(q["raw_mw"] * share[q["county_fips"]] for q in qs)),
                "adj_mw_2027_apportioned": r1(sum(q["adj_mw_2027"] * share[q["county_fips"]] for q in qs)),
                "adj_mw_2028_apportioned": r1(sum(q["adj_mw_2028"] * share[q["county_fips"]] for q in qs)),
            }
        )
    sites = [s for s in dc_sites if s["county_fips"] in share]
    ncp_hour = r4(rng.uniform(15.5, 18.6))
    outlook = {
        "zone": b["zone"],
        "ltlf_start": r1(rng.uniform(2000, 20000)),
        "ltlf_end": None,
        "ltlf_now": None,
        "ltlf_cagr": r4(rng.uniform(0.01, 0.09)),
        "cp_year": 2025,
        "cp_avg_mw": None,
        "cf_summer": r4(rng.uniform(0.82, 0.97)),
        "share_4cp": r4(rng.uniform(0.02, 0.3)),
        "share_energy": None,
        "intensity": r4(rng.uniform(0.9, 1.15)),
        "ncp_summer_mw_full": None,
        "ncp_end_hour": ncp_hour,
        "hour_years": "2021–2025",
        "ncp_now_mw": None,
        "ncp_now_months": 3,
        "now_vs_ltlf": r4(rng.uniform(-0.08, 0.12)),
        "peak_mismatch": "late" if ncp_hour > 18 else "early" if ncp_hour < 16 else None,
        "q7_holdout_mape": None,
        "line": f"4CP ({b['zone']}, 2025): fixture talking point",
    }
    outlook["ltlf_end"] = r1(outlook["ltlf_start"] * (1 + outlook["ltlf_cagr"]) ** 6)
    outlook["ltlf_now"] = r1(outlook["ltlf_start"] * (1 + outlook["ltlf_cagr"]))
    outlook["ncp_now_mw"] = r1(outlook["ltlf_now"] * (1 + outlook["now_vs_ltlf"]))
    outlook["cp_avg_mw"] = r1(outlook["ncp_now_mw"] * outlook["cf_summer"])
    outlook["ncp_summer_mw_full"] = r1(outlook["ncp_now_mw"] * 0.97)
    outlook["share_energy"] = r4(outlook["share_4cp"] / outlook["intensity"])
    territory_facts = [
        fact(
            "population",
            "Population (apportioned)",
            r1(row["meters"] * rng.uniform(1.8, 2.6)),
            "people",
            "census_population_county",
            "2025-07-01",
            "PEP vintage 2025",
        ),
        fact(
            "pop_growth",
            "Population growth 2020→2025",
            raw["pop_growth"][i],
            "share",
            "census_population_county",
            "2025-07-01",
        ),
        fact(
            "permits_per_1k",
            "Residential permits 2023–2025 per 1,000 residents",
            raw["permits_per_1k"][i],
            "units/1k",
            "census_permits_county",
            "2025-12-31",
        ),
        fact(
            "owner_sf_homes",
            "Owner-occupied single-family homes (apportioned)",
            raw["owner_sf_homes"][i],
            "homes",
            "census_housing_county",
            "2024-12-31",
        ),
        fact(
            "owner_sf_share",
            "Owner-occupied single-family share",
            raw["owner_sf_share"][i],
            "share",
            "census_housing_county",
            "2024-12-31",
        ),
        fact(
            "dc_sites_expected",
            "New data-center sites since 2025 (expected, by county share)",
            raw["dc_sites"][i],
            "sites",
            "tceq_data_center_sites",
            None,
            "no snapshot date in the table",
        ),
        fact(
            "queue_raw_mw",
            "Generation queue, raw (context counties)",
            queue_rows[0]["raw_mw_context"] if queue_rows else None,
            "MW",
            "queue_adjusted",
            iso(QUEUE_MONTH),
            context_label,
        ),
        fact(
            "queue_adj_2028",
            "Adjusted: expected COD by Dec 2028 (context counties)",
            queue_rows[0]["adj_mw_2028_context"] if queue_rows else None,
            "MW",
            "queue_adjusted",
            iso(QUEUE_MONTH),
        ),
        fact(
            "weather_zone",
            "Primary weather zone (by area)",
            b["zone"],
            None,
            "county_weather_zone",
            iso(AS_OF),
        ),
        fact(
            "zone_ltlf_cagr",
            "Zone summer peak outlook, LTLF CAGR",
            outlook["ltlf_cagr"],
            "per year",
            "ltlf_forecasts",
            "2025-10-06",
        ),
        fact(
            "zone_peak_now",
            "Zone summer peak 2026 (15-min, actual)",
            outlook["ncp_now_mw"],
            "MW",
            "ercot_monthly_peaks",
            "2026-08-01",
            "3 of 4 months; not final-settled",
        ),
        fact(
            "zone_4cp_intensity",
            "4CP intensity (4CP share ÷ energy share)",
            outlook["intensity"],
            "ratio",
            "ercot_monthly_peaks",
            "2026-08-01",
        ),
        fact(
            "zone_peak_hour",
            "Zone's own monthly peaks end (2021–2025 mean, local)",
            ncp_hour,
            "hour",
            "ercot_monthly_peaks",
            "2026-08-01",
        ),
        fact(
            "account_4cp_mw",
            "Account load at the 4CP",
            None,
            "MW",
            "UtilityDataSource",
            None,
            "not public: needs the co-op's own meter data",
        ),
    ]
    score_signals = []
    for k, label, unit, w, source, as_of in ACCOUNT_SIGNALS:
        p = pcts[k][i] or 0
        score_signals.append(
            {
                "signal": k,
                "label": label,
                "raw": raw[k][i],
                "unit": unit,
                "pct": pcts[k][i],
                "weight": w,
                "weight_used": w,
                "contribution": r4(w * p),
                "source": source,
                "as_of": as_of,
            }
        )
    contexts: dict[str, list[dict[str, Any]]] = {}
    for e in context:
        contexts.setdefault(e["trigger"], []).append(e)

    def event(e):
        return {k: v for k, v in e.items() if k not in ("account_id", "model_version", "as_of")}

    gaps = [
        {"key": f["key"], "kind": "missing", "detail": f"{f['label']}: no value ({f['source']})"}
        for f in [*header, *territory_facts]
        if f["value"] is None
    ]
    if context_rule == "home_county":
        gaps.append(
            {
                "key": "exposed_counties",
                "kind": "structural",
                "detail": "no county ≥ 20%: county triggers cannot fire; territory facts use the home county",
            }
        )
    return {
        "account_id": b["account_id"],
        "as_of": iso(AS_OF),
        "simulated": False,
        "header": header,
        "score": {
            "score": row["score"],
            "rank": row["rank"],
            "rank_within_type": row["rank_within_type"],
            "n_accounts": len(ACCOUNTS),
            "tier": row["tier"],
            "method": "weighted mean of within-universe percentile ranks (fixture weights)",
            "weights_set": "q3",
            "weights_status": "pending_review",
            "signals": score_signals,
        },
        "next_action": {
            "action": action,
            "action_label": ACTION_LABEL[action],
            "rule": f"Tier {row['tier']} (rank {row['rank']} of {len(ACCOUNTS)}) + {len(strong)} active "
            "strong "
            f"trigger(s) → {ACTION_LABEL[action]}",
            "lead_trigger": (
                {
                    "trigger": lead["trigger"],
                    "title": lead["title"],
                    "event_date": lead["event_date"],
                    "source": lead["source"],
                    "source_ref": lead["source_ref"],
                }
                if lead
                else None
            ),
            "offer": lead["offer"] if lead else None,
            "talking_points": [e[0]["offer"] for e in contexts.values()] + [outlook["line"]],
            "changes_on": iso(changes_on),
            "changes_to": changes_to,
        },
        "triggers": {
            "active": [event(e) for e in strong],
            "context_summary": [
                {
                    "trigger": t,
                    "label": es[0]["label"],
                    "count": len(es),
                    "latest_date": es[0]["event_date"],
                    "counties": sorted({e["county_name"] for e in es if e["county_name"]}),
                }
                for t, es in contexts.items()
            ],
            "history_count": len(b["events"]),
        },
        "territory": {
            "facts": territory_facts,
            "counties": [
                {k: v for k, v in c.items() if k not in ("account_id", "model_version", "as_of")}
                for c in counties
            ],
            "context_rule": context_rule,
            "context_label": context_label,
            "zones": [{"weather_zone": b["zone"], "area_share": 1.0}],
            "data_centers": [
                {
                    "first_permit_date": s["first_permit_date"],
                    "name": s["site_name"],
                    "tceq_rn": s["tceq_rn"],
                    "county_name": s["county_name"],
                    "county_share": share[s["county_fips"]],
                    "exposed": share[s["county_fips"]] >= 0.2,
                    "context": s["county_fips"] in ctx_fips,
                    "matched_by": s["matched_by"],
                }
                for s in sites
            ],
            "queue": queue_rows,
            "zone_outlook": outlook,
        },
        "eia_series": series,
        "gaps": gaps,
        "coverage": {
            "public_data": True,
            "utility_private_data": False,
            "fleet_data": False,
            "resolution": "zone",
        },
    }


# --- forecast ---------------------------------------------------------------------------------------------

YEARS = [2027, 2028, 2029, 2030]
VARIANTS = {
    "deck_pre_batch_zero": ("Before Batch Zero", True, date(2026, 3, 2), (6300, 2950)),
    "deck_latest": ("Latest deck", True, date(2026, 9, 1), (8100, 4200)),
    "approvals_pace": ("Approvals pace", False, date(2026, 9, 1), (4700, 1250)),
}
ORGANIC_SHARE = dict(zip(ZONES, [0.27, 0.04, 0.05, 0.31, 0.02, 0.12, 0.13, 0.06], strict=True))
LLU_SHARE = dict(zip(ZONES, [0.12, 0.02, 0.30, 0.15, 0.04, 0.09, 0.06, 0.22], strict=True))


def make_forecast() -> list[dict[str, Any]]:
    rows = []
    for variant, (_, band, deck, (ll0, ll_step)) in VARIANTS.items():
        for year in YEARS:
            k = year - 2027
            layers = {
                "organic": (80600 + 1150 * k, 0.03),
                "large_load": (ll0 + ll_step * k, 0.4),
                "unattributed": (2400 + 100 * k, 0.2),
            }
            # The analyses allocate zones for the default variant only.
            regions = ["ERCOT", *ZONES] if variant == "deck_pre_batch_zero" else ["ERCOT"]
            for region in regions:
                values = {}
                for layer, (p50, spread) in layers.items():
                    share = (
                        1.0
                        if region == "ERCOT"
                        else (ORGANIC_SHARE if layer == "organic" else LLU_SHARE)[region]
                    )
                    mid = p50 * share
                    values[layer] = (
                        mid * (1 - spread) if band else None,
                        mid,
                        mid * (1 + spread * 1.3) if band else None,
                    )
                total50 = sum(v[1] for v in values.values())
                if band:
                    lo = total50 - math.sqrt(sum((v[1] - v[0]) ** 2 for v in values.values()))
                    hi = total50 + math.sqrt(sum((v[2] - v[1]) ** 2 for v in values.values()))
                    values["total"] = (lo, total50, hi)
                else:
                    values["total"] = (None, total50, None)
                for layer, (p10, p50, p90) in values.items():
                    rows.append(
                        {
                            "run_id": "fixture-run-1",
                            "as_of": iso(AS_OF),
                            "region_type": "ercot" if region == "ERCOT" else "weather_zone",
                            "region_id": region,
                            "target_year": year,
                            "variant": variant,
                            "layer": layer,
                            "p10_mw": r1(p10) if p10 is not None else None,
                            "p50_mw": r1(p50),
                            "p90_mw": r1(p90) if p90 is not None else None,
                            "band_kind": (
                                None
                                if not band
                                else "p10_p90"
                                if region == "ERCOT" or layer == "organic"
                                else "allocation_range"
                            ),
                            "deck_vintage": iso(deck),
                            "factor": 0.47,
                            "ratio_p10": 0.12,
                            "ratio_p50": 0.17,
                            "ratio_p90": 0.26,
                            "approved_stock_mw": 7400.0,
                            "share_of_ll_u": None if region == "ERCOT" else LLU_SHARE[region],
                            "verified": False,
                            "model_version": MODEL_VERSION,
                        }
                    )
    return rows


def make_official_lines() -> list[dict[str, Any]]:
    lines = [
        ("LTLF", "2025", date(2025, 4, 10), "ercot_adjusted", "LTLF 2025 (ERCOT-adjusted)", 97500, 4000),
        ("LTLF", "2025", date(2025, 4, 10), "tsp_provided", "LTLF 2025 (TSP-provided)", 118000, 12000),
        ("CDR", "Dec 2025", date(2025, 12, 5), "cdr", "CDR Dec 2025", 99800, 5000),
    ]
    rows = []
    for product, vintage, vdate, series, label, start, step in lines:
        for region in ["ERCOT", *ZONES]:
            share = 1.0 if region == "ERCOT" else ORGANIC_SHARE[region]
            for year in YEARS:
                rows.append(
                    {
                        "product": product,
                        "vintage": vintage,
                        "vintage_date": iso(vdate),
                        "series": series,
                        "label": label,
                        "region_id": region,
                        "target_year": year,
                        "mw": r1((start + step * (year - 2027)) * share),
                    }
                )
    return rows


def make_large_load() -> tuple[list, list, list, list]:
    realization = []
    for m in range(1, 13):
        vintage = date(2025, m, 3)
        base = 3500 + 280 * m
        for target, promised0, realized, energized in (
            (2025, 13000, 6900.0, 4300.0),
            (2026, 26000, None, None),
        ):
            promised = promised0 * rng.uniform(0.85, 1.15) * (1 - 0.02 * m)
            firm = promised * rng.uniform(0.6, 0.8)
            horizon = (target - vintage.year) * 12 + 12 - vintage.month
            rec = {
                "deck_vintage": iso(vintage),
                "report_date": iso(vintage + timedelta(days=2)),
                "target_year": target,
                "horizon_months": horizon,
                "promised_mw": r1(promised),
                "promised_firm_mw": r1(firm),
                "base_a2e_mw": float(base),
                "realized_a2e_mw": realized,
                "realized_energized_mw": energized,
                "known_from": "2026-01-05" if realized else None,
                "document": f"fixture-large-load-deck-{vintage:%Y-%m}.pdf",
                "page": 7,
                "verified": False,
                "model_version": MODEL_VERSION,
            }
            if realized:
                rec.update(
                    gross_a2e=r4(realized / promised),
                    gross_a2e_firm=r4(realized / firm),
                    incremental_a2e=r4((realized - base) / (promised - base)),
                    incremental_a2e_firm=r4((realized - base) / (firm - base)),
                    gross_energized=r4(energized / promised),
                )
            else:
                rec.update(
                    gross_a2e=None,
                    gross_a2e_firm=None,
                    incremental_a2e=None,
                    incremental_a2e_firm=None,
                    gross_energized=None,
                )
            realization.append(rec)
    in_service = []
    statuses = [
        ("approved_to_energize", 7000, 900),
        ("planning_studies_approved", 5000, 3500),
        ("under_ercot_review", 9000, 11000),
        ("no_studies_submitted", 6000, 8000),
    ]
    for vintage, scale in ((date(2025, 6, 2), 0.6), (date(2026, 3, 2), 0.9), (date(2026, 9, 1), 1.0)):
        for year in range(2026, 2031):
            for status, start, step in statuses:
                in_service.append(
                    {
                        "deck_vintage": iso(vintage),
                        "in_service_year": year,
                        "status": status,
                        "mw": r1((start + step * (year - 2026)) * scale),
                        "document": f"fixture-large-load-deck-{vintage:%Y-%m}.pdf",
                        "page": 5,
                        "verified": False,
                        "model_version": MODEL_VERSION,
                    }
                )
    monthly = []
    stock = 4100.0
    for k in range(24):
        month = month_add(date(2024, 9, 1), k)
        if month < date(2026, 8, 1):
            stock += rng.uniform(40, 220)
        missing = k in (3, 8)
        vintage = month_add(month, 1)
        monthly.append(
            {
                "month": iso(month),
                "a2e_mw": None if missing else r1(stock),
                "observed_simultaneous_mw": None if missing else r1(stock * rng.uniform(0.44, 0.5)),
                "observed_nonsimultaneous_mw": None if missing else r1(stock * rng.uniform(0.55, 0.62)),
                "a2e_lz_west_mw": None if missing else r1(stock * 0.55),
                "a2e_other_mw": None if missing else r1(stock * 0.45),
                "read_from_vintage": iso(vintage + timedelta(days=2)),
                "document": f"fixture-large-load-deck-{vintage:%Y-%m}.pdf",
                "page": 4,
                "verified": False,
                "as_of": iso(AS_OF),
                "model_version": MODEL_VERSION,
            }
        )
    annotations = [
        {
            "date": "2026-04-15",
            "title": "Fixture annotation: a batch study intake",
            "detail": "Invented for the fixture",
            "source_url": None,
            "verified": False,
        },
        {
            "date": "2026-08-03",
            "title": "Fixture annotation: large-load approvals paused",
            "detail": "Invented for the fixture; the real annotation comes from mart_annotations",
            "source_url": None,
            "verified": False,
        },
    ]
    return realization, in_service, monthly, annotations


# --- backtest ---------------------------------------------------------------------------------------------

ACTUALS = {
    2016: 71100,
    2017: 69500,
    2018: 73300,
    2019: 74300,
    2020: 73900,
    2021: 72800,
    2022: 79600,
    2023: 83100,
    2024: 84000,
    2025: 83400,
    2026: 88700,
}
AS_OF_DATES = [
    date(2022, 11, 30),
    date(2023, 5, 31),
    date(2023, 11, 30),
    date(2024, 5, 31),
    date(2024, 11, 30),
    date(2025, 5, 31),
    date(2025, 11, 30),
    date(2026, 5, 31),
]
ERA_START = date(2024, 5, 1)


def make_actuals() -> list[dict[str, Any]]:
    return [
        {
            "year": y,
            "hourly_peak_mw": float(mw),
            "peak_ts_utc": f"{y}-08-{rng.randint(1, 28):02d}T22:00:00Z",
            "hour_ending_local": 17,
            "peak_15min_mw": r1(mw * 1.0015),
            "interval_end_local_15min": "16:45",
            "final": y < 2026,
            "data_as_of": iso(AS_OF),
            "model_version": MODEL_VERSION,
        }
        for y, mw in ACTUALS.items()
    ]


def make_backtest() -> list[dict[str, Any]]:
    cells = []
    for as_of in AS_OF_DATES:
        first = as_of.year if as_of.month <= 5 else as_of.year + 1
        era = "with_tsp_loads" if as_of >= ERA_START else "before_tsp_loads"
        for target in range(first, min(first + 3, 2027)):
            horizon = target - first + 1
            actual = ACTUALS[target]
            final = target < 2026
            base = {
                "as_of": iso(as_of),
                "target_year": target,
                "horizon": horizon,
                "era": era,
                "actual_mw": float(actual),
                "actual_final": final,
                "model_version": MODEL_VERSION,
            }
            p50 = actual * (1 + rng.gauss(0.005, 0.03))
            p10, p90 = p50 * 0.95, p50 * 1.06
            ll, u = p50 * rng.uniform(0.03, 0.08), p50 * rng.uniform(0.02, 0.03)
            cells.append(
                {
                    **base,
                    "source": "basecast",
                    "product": None,
                    "vintage": None,
                    "vintage_date": None,
                    "variant": "deck_pre_batch_zero",
                    "p10_mw": r1(p10),
                    "p50_mw": r1(p50),
                    "p90_mw": r1(p90),
                    "error_pct": r4((p50 - actual) / actual * 100),
                    "in_band": p10 <= actual <= p90,
                    "organic_p50": r1(p50 - ll - u),
                    "ll_p50": r1(ll),
                    "u_p50": r1(u),
                    "ll_realized": r1(ll * rng.uniform(0.7, 1.2)),
                    "u_realized": r1(u * rng.uniform(0.8, 1.2)),
                    "leak_note": "fixture: factor and ratio from decks published after the as-of date"
                    if as_of < date(2024, 1, 1)
                    else None,
                    "verified": False,
                }
            )
            organic = actual * (1 - rng.uniform(0.04, 0.11))
            cells.append(
                {
                    **base,
                    "source": "basecast_organic_only",
                    "product": None,
                    "vintage": None,
                    "vintage_date": None,
                    "variant": None,
                    "p10_mw": None,
                    "p50_mw": r1(organic),
                    "p90_mw": None,
                    "error_pct": r4((organic - actual) / actual * 100),
                    "in_band": None,
                    "organic_p50": r1(organic),
                    "ll_p50": None,
                    "u_p50": None,
                    "ll_realized": None,
                    "u_realized": None,
                    "leak_note": None,
                    "verified": True,
                }
            )
            for product, bias in (("LTLF", 0.07 if era == "with_tsp_loads" else 0.02), ("CDR", 0.045)):
                vintage_year = as_of.year if as_of.month >= 5 else as_of.year - 1
                official = actual * (1 + bias + rng.gauss(0, 0.02))
                cells.append(
                    {
                        **base,
                        "source": product,
                        "product": product,
                        "vintage": str(vintage_year),
                        "vintage_date": iso(date(vintage_year, 4, 10)),
                        "variant": None,
                        "p10_mw": None,
                        "p50_mw": r1(official),
                        "p90_mw": None,
                        "error_pct": r4((official - actual) / actual * 100),
                        "in_band": None,
                        "organic_p50": None,
                        "ll_p50": None,
                        "u_p50": None,
                        "ll_realized": None,
                        "u_realized": None,
                        "leak_note": None,
                        "verified": True,
                    }
                )
    return cells


def make_fan(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models = [c for c in cells if c["source"] == "basecast" and c["target_year"] == 2026]
    common = {"target_year": 2026, "model_version": MODEL_VERSION}
    return [
        {
            **common,
            "kind": "official_preliminary",
            "label": "Preliminary long-term forecast (fixture)",
            "product": "LTLF-prelim",
            "vintage": "2026 preliminary",
            "vintage_date": "2026-04-15",
            "value_mw": 108000.0,
            "low_mw": None,
            "high_mw": None,
            "source": "official_forecasts",
            "method": "manual",
            "final": True,
            "verified": True,
        },
        {
            **common,
            "kind": "official_range",
            "label": "ERCOT's own projection (fixture)",
            "product": None,
            "vintage": "2026 preliminary",
            "vintage_date": "2026-04-15",
            "value_mw": None,
            "low_mw": 86000.0,
            "high_mw": 95000.0,
            "source": "official_forecasts",
            "method": "manual",
            "final": True,
            "verified": True,
        },
        {
            **common,
            "kind": "official",
            "label": "LTLF 2025, TSP-provided (fixture)",
            "product": "LTLF",
            "vintage": "2025",
            "vintage_date": "2025-04-10",
            "value_mw": 104500.0,
            "low_mw": None,
            "high_mw": None,
            "source": "official_forecasts",
            "method": "file",
            "final": True,
            "verified": True,
        },
        {
            **common,
            "kind": "official",
            "label": "LTLF 2025, ERCOT-adjusted (fixture)",
            "product": "LTLF",
            "vintage": "2025",
            "vintage_date": "2025-04-10",
            "value_mw": 92300.0,
            "low_mw": None,
            "high_mw": None,
            "source": "official_forecasts",
            "method": "file",
            "final": True,
            "verified": True,
        },
        {
            **common,
            "kind": "actual",
            "label": "Actual summer peak (fixture)",
            "product": None,
            "vintage": None,
            "vintage_date": None,
            "value_mw": float(ACTUALS[2026]),
            "low_mw": None,
            "high_mw": None,
            "source": "mart_actual_summer_peaks",
            "method": "file",
            "final": False,
            "verified": True,
        },
    ] + [
        {
            **common,
            "kind": "model",
            "label": f"basecast as of {m['as_of']} (fixture)",
            "product": None,
            "vintage": m["as_of"],
            "vintage_date": m["as_of"],
            "value_mw": m["p50_mw"],
            "low_mw": m["p10_mw"],
            "high_mw": m["p90_mw"],
            "source": "mart_peak_backtest",
            "method": "model",
            "final": True,
            "verified": False,
        }
        for m in models
    ]


def make_official_errors() -> list[dict[str, Any]]:
    rows = []
    for product, first_vintage in (("LTLF", 2015), ("CDR", 2016)):
        for vintage in range(first_vintage, 2026):
            for target in range(vintage + 1, min(vintage + 4, 2027)):
                actual = ACTUALS[target]
                drift = 0.01 + 0.012 * max(0, vintage - 2020)
                forecast = actual * (1 + drift + rng.gauss(0.01, 0.025))
                rows.append(
                    {
                        "product": product,
                        "vintage": str(vintage),
                        "vintage_date": iso(date(vintage, 4 if product == "LTLF" else 12, 10)),
                        "target_year": target,
                        "horizon": target - vintage,
                        "series": "base" if product == "LTLF" else "cdr",
                        "forecast_mw": r1(forecast),
                        "actual_mw": float(actual),
                        "actual_complete": target < 2026,
                        "actual_peak_local": f"{target}-08-15 17:00",
                        "error_mw": r1(forecast - actual),
                        "error_pct": r4((forecast - actual) / actual * 100),
                        "model_version": MODEL_VERSION,
                    }
                )
    return rows


def make_queue_backtest() -> list[dict[str, Any]]:
    rows = []
    for k, month in enumerate((date(2022, 6, 1), date(2023, 6, 1), date(2024, 6, 1))):
        for stratum, raw in (
            ("all", 160000),
            ("solar", 70000),
            ("storage", 45000),
            ("wind", 20000),
            ("gas_other", 25000),
        ):
            actual = raw * rng.uniform(0.12, 0.2)
            pred = actual * (1 + rng.uniform(-0.18, 0.15))
            rows.append(
                {
                    "report_month": iso(month),
                    "stratum": stratum,
                    "window_months": 24,
                    "raw_mw": r1(raw * (1 + 0.2 * k)),
                    "pred_mw": r1(pred),
                    "actual_mw": r1(actual),
                    "developer_projected_mw": r1(actual * rng.uniform(1.6, 2.6)),
                    "error_pct": r4((pred - actual) / actual * 100),
                    "model_variant": "entry_ia_sm",
                    "county_rho_adj": 0.57 + 0.01 * k,
                    "county_rho_raw": 0.37 + 0.01 * k,
                    "county_rho_developer": 0.48,
                    "model_version": MODEL_VERSION,
                }
            )
    return rows


# The insights page: one card per line graded A or B in video-candidates.md, with invented values.
# (id, grade, title, value, unit, queue, caveat codes, link, source doc, pending review items)
INSIGHTS = [
    (
        "A1",
        "A",
        "Large loads promised vs approved",
        21400.0,
        "MW",
        "large_load",
        ["machine_read_unverified"],
        "/forecast?tab=large-loads",
        "q5_large_load.md §3",
        ["R3"],
    ),
    (
        "A2",
        "A",
        "The preliminary forecast vs the peak",
        17300.0,
        "MW",
        None,
        ["preliminary_actuals"],
        "/backtest",
        "q1_backtest.md §4",
        ["R6"],
    ),
    (
        "B1",
        "B",
        "Our model, rebuilt at an earlier date",
        87100.0,
        "MW",
        None,
        ["preliminary_actuals", "band_uncalibrated"],
        "/backtest",
        "x7_peak_forecast.md §3",
        ["R13", "R3"],
    ),
    (
        "B2",
        "B",
        "The peak above weather and trend",
        5200.0,
        "MW",
        None,
        [],
        "/forecast?tab=peak",
        "x1_peak_excess.md §1",
        ["R5", "R9"],
    ),
    (
        "B3",
        "B",
        "The generation queue, adjusted",
        31500.0,
        "MW",
        "generation",
        ["beyond_backtested_window"],
        "/explorer?layer=queue",
        "x2_adjusted_queue.md §2",
        ["R4", "R10"],
    ),
    (
        "B4",
        "B",
        "Filings as submitted vs adjusted",
        12800.0,
        "MW",
        None,
        ["preliminary_actuals"],
        "/backtest",
        "q1_backtest.md §4",
        ["R6"],
    ),
    (
        "B5",
        "B",
        "Backtest: our misses vs ERCOT's",
        2.9,
        "%",
        None,
        ["preliminary_actuals"],
        "/backtest",
        "x7_peak_forecast.md §3",
        ["R13", "R3"],
    ),
    (
        "B6",
        "B",
        "Promised large loads approved on time",
        0.18,
        "ratio",
        "large_load",
        ["machine_read_unverified"],
        "/forecast?tab=large-loads",
        "q5_large_load.md §3",
        ["R3"],
    ),
    (
        "B7",
        "B",
        "The large-load queue slides",
        11200.0,
        "MW",
        "large_load",
        ["machine_read_unverified"],
        "/forecast?tab=large-loads",
        "q5_large_load.md §3",
        ["R3"],
    ),
    (
        "B8",
        "B",
        "Promised after the intake",
        150000.0,
        "MW",
        "large_load",
        ["machine_read_unverified", "policy_pause_2026"],
        "/forecast?tab=large-loads",
        "x7_peak_forecast.md §1",
        ["R3", "R13"],
    ),
    (
        "B9",
        "B",
        "Generation projects reaching COD",
        0.41,
        "share",
        "generation",
        [],
        "/forecast?tab=queue",
        "q6_survival.md §4",
        ["R4"],
    ),
    (
        "B10",
        "B",
        "4CP intervals vs the priciest hours",
        0.0,
        "share",
        None,
        [],
        "/forecast?tab=4cp",
        "x3_four_cp.md Q4",
        ["R11"],
    ),
    (
        "B11",
        "B",
        "A two-hour window catches the 4CP",
        0.95,
        "share",
        None,
        [],
        "/forecast?tab=4cp",
        "x3_four_cp.md Q2",
        ["R11"],
    ),
    (
        "B12",
        "B",
        "Dispatch days to catch all four",
        51.0,
        "days",
        None,
        ["optimistic_weather"],
        "/forecast?tab=4cp",
        "x3_four_cp.md Q2",
        ["R11"],
    ),
    (
        "B13",
        "B",
        "Why the large-load layer matters",
        9.4,
        "%",
        None,
        [],
        "/backtest",
        "x7_peak_forecast.md §3",
        ["R13"],
    ),
]


def make_insights() -> list[dict[str, Any]]:
    return [
        {
            "id": card_id,
            "grade": grade,
            "rank": rank,
            "title": f"{title} (fixture)",
            "caption": f"Fixture caption: {title.lower()}; the real line is built from the marts.",
            "value": value,
            "unit": unit,
            "figures": [{"label": "Fixture companion figure", "value": r1(value * 1.3), "unit": unit}],
            "caveat": "Fixture caveat: the real card carries the caveat its line requires.",
            "caveat_codes": codes,
            "queue": queue,
            "verified": grade == "A",
            "depends_on": depends,
            "source_doc": f"docs/analysis/{doc}",
            "link": link,
            "as_of": iso(AS_OF),
            "model_version": MODEL_VERSION,
        }
        for rank, (card_id, grade, title, value, unit, queue, codes, link, doc, depends) in enumerate(
            INSIGHTS, start=1
        )
    ]


def main() -> None:
    projects = make_projects()
    queue = county_queue(projects)
    accounts_zones = {z for *_, z, _ in ACCOUNTS}
    account_fips = [BY_ZONE[z][0]["county_fips"] for z in sorted(accounts_zones)]
    sites = make_sites(account_fips)
    acquisition, breaks = make_acquisition(sites)
    accounts, details, events, links = make_accounts(sites, queue)
    cells = make_backtest()
    realization, in_service, monthly, annotations = make_large_load()

    write("county_weather_zone", COUNTIES)
    write("mart_queue_project_scores", projects)
    write("mart_queue_adjusted_county", queue)
    write("mart_data_center_sites_new", sites)
    write("mart_county_acquisition", acquisition)
    write("mart_accounts", accounts)
    write("mart_account_detail", details)
    write("mart_account_events", events)
    write("mart_account_counties", links)
    write("mart_peak_forecast", make_forecast())
    write("mart_official_peak_lines", make_official_lines())
    write("mart_large_load_realization", realization)
    write("mart_large_load_in_service", in_service)
    write("mart_large_load_monthly", monthly)
    write("mart_annotations", annotations)
    write("mart_peak_backtest", cells)
    write("mart_actual_summer_peaks", make_actuals())
    write("mart_backtest_fan", make_fan(cells))
    write("mart_official_forecast_errors", make_official_errors())
    write("mart_queue_backtest", make_queue_backtest())
    write("mart_insights", make_insights())
    write(
        "mart_meta",
        {
            "accounts": {
                "signals": [
                    {"signal": k, "label": label, "unit": unit, "weight": w}
                    for k, label, unit, w, _, _ in ACCOUNT_SIGNALS
                ],
            },
            "county_acquisition": {
                "legend_breaks": breaks,
                "grid_tilt": GRID_TILT,
                "signals": [
                    {"signal": k, "label": label, "block": block, "unit": unit, "weight": w}
                    for k, label, block, unit, w in COUNTY_SIGNALS
                ],
            },
            "peak_forecast": {
                "default_variant": "deck_pre_batch_zero",
                "variants": [
                    {"variant": v, "label": label, "has_band": band}
                    for v, (label, band, _, _) in VARIANTS.items()
                ],
                "ratio_definition": "Incremental realization ratio: approved MW added by December of the "
                "target year "
                "÷ MW the deck promised beyond its own approved stock (horizons of 6 months or more).",
            },
            "glossary": {"items": glossary()},
            "peak_backtest": {
                "eras": [
                    {
                        "era": "before_tsp_loads",
                        "label": "Before TSP large loads",
                        "description": "As-of dates before ERCOT's forecasts took in the TSPs' large loads.",
                    },
                    {
                        "era": "with_tsp_loads",
                        "label": "With TSP large loads",
                        "description": "As-of dates after ERCOT's forecasts took in the TSPs' large loads.",
                    },
                ],
            },
        },
        key="marts",
    )
    print(f"wrote {len(list(OUT.glob('*.json')))} fixtures to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
