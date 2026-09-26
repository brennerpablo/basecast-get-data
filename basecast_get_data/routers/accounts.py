"""Commercial intelligence: the ranked accounts, their diagnosis and events (docs/data-contract.md §5).

Validation lock: the marts hold only the scored universe, this router returns exactly their rows, and an id
that is not there answers the same 404 as any unknown id.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date
from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import product_meta
from basecast_get_data.schemas.accounts import (
    AccountDetail,
    AccountDetailResponse,
    AccountsData,
    AccountSort,
    AccountsResponse,
    AccountSummary,
    AccountType,
    Event,
    EventsPage,
    EventsResponse,
    NextAction,
    RankScope,
    SignalDef,
    Strength,
    Tier,
)
from basecast_get_data.schemas.common import NOT_FOUND, PRODUCT_ERRORS

router = APIRouter(prefix="/accounts", tags=["accounts"], dependencies=[Depends(require_token)])

ACCOUNTS, DETAIL, EVENTS, COUNTIES = (
    "mart_accounts",
    "mart_account_detail",
    "mart_account_events",
    "mart_account_counties",
)
ACCOUNT_ID = Path(..., min_length=1, max_length=32, description="PUCT ccn_no")
COUNTY_MIN_SHARE = 0.01


class CSVResponse(StreamingResponse):
    media_type = "text/csv"


def _caveats(accounts: pl.DataFrame) -> list[str]:
    pending = "weights_status" in accounts.columns and (accounts["weights_status"] == "pending_review").any()
    return ["weights_pending_review", "by_county_not_point"] if pending else ["by_county_not_point"]


def _filtered(
    account_type: list[str] | None,
    tier: list[str] | None,
    next_action: list[str] | None,
    trigger: list[str] | None,
    zone: list[str] | None,
    gt: list[str] | None,
    county: str | None,
    q: str | None,
    sort: str,
    desc: bool,
    rank_scope: str,
) -> pl.DataFrame:
    df = store.frame(ACCOUNTS)
    for column, values in (
        ("account_type", account_type),
        ("tier", tier),
        ("next_action", next_action),
        ("primary_weather_zone", zone),
        ("gt", gt),
    ):
        if values:
            df = df.filter(pl.col(column).is_in(values))
    if trigger:
        df = df.filter(pl.col("active_triggers").list.eval(pl.element().is_in(trigger)).list.any())
    if county:
        links = store.frame(COUNTIES).filter(
            pl.col("county_fips") == county, pl.col("county_share") >= COUNTY_MIN_SHARE
        )
        df = df.filter(pl.col("account_id").is_in(links["account_id"].implode()))
    if q:
        df = df.filter(pl.col("name").str.to_lowercase().str.contains(q.strip().lower(), literal=True))
    if sort == "rank":
        by = ["account_type", "rank_within_type"] if rank_scope == "within_type" else ["rank"]
    else:
        by = [sort, "rank"]
    return df.sort(by, descending=[desc] + [False] * (len(by) - 1), nulls_last=True)


def _filters(
    account_type: list[AccountType] | None = Query(None, alias="type"),
    tier: list[Tier] | None = Query(None),
    next_action: list[NextAction] | None = Query(None),
    trigger: list[str] | None = Query(None, description="Accounts with any of these active triggers"),
    zone: list[str] | None = Query(None, description="Primary weather zone"),
    gt: list[str] | None = Query(None, description="G&T / wholesale supplier"),
    county: str | None = Query(None, pattern=r"^\d{5}$", description="Accounts covering ≥ 1% of this county"),
    q: str | None = Query(None, max_length=100, description="Name contains"),
    sort: AccountSort = Query("rank"),
    desc: bool = Query(False),
    rank_scope: RankScope = Query("all", description="within_type: sort=rank orders by rank_within_type"),
) -> dict[str, Any]:
    return dict(
        account_type=account_type,
        tier=tier,
        next_action=next_action,
        trigger=trigger,
        zone=zone,
        gt=gt,
        county=county,
        q=q,
        sort=sort,
        desc=desc,
        rank_scope=rank_scope,
    )


@router.get("", response_model=AccountsResponse, responses=PRODUCT_ERRORS)
def list_accounts(filters: dict[str, Any] = Depends(_filters)) -> AccountsResponse:
    df = _filtered(**filters)
    everything = store.frame(ACCOUNTS)
    first = everything.row(0, named=True) if everything.height else {}
    return AccountsResponse(
        meta=product_meta(ACCOUNTS, rows=everything, caveats=_caveats(everything)),
        data=AccountsData(
            items=[AccountSummary(**row) for row in df.to_dicts()],
            total=df.height,
            rank_scope=filters["rank_scope"],
            signals=[SignalDef(**s) for s in store.mart_meta("accounts").get("signals", [])],
            weights_set=first.get("weights_set") or "",
            weights_status=first.get("weights_status") or "",
        ),
    )


# Flat columns of the export, in order; the signals follow as raw_<signal>, pct_<signal>.
CSV_COLUMNS = (
    "account_id",
    "name",
    "account_type",
    "eia_utility_id",
    "gt",
    "primary_weather_zone",
    "meters",
    "score",
    "rank",
    "rank_within_type",
    "tier",
    "next_action",
    "action_changes_on",
    "action_changes_to",
    "n_strong",
    "n_context",
    "latest_event_date",
    "top_trigger",
    "top_trigger_title",
    "top_trigger_date",
    "top_trigger_age_days",
    "active_triggers",
    "flags",
)


def _csv_rows(items: list[AccountSummary], signals: list[str]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        out = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return out

    writer.writerow([*CSV_COLUMNS, *(f"{p}_{s}" for s in signals for p in ("raw", "pct")), "simulated"])
    yield flush()
    for a in items:
        t = a.top_trigger
        row = a.model_dump(include=set(CSV_COLUMNS))
        row.update(
            top_trigger=t.trigger if t else None,
            top_trigger_title=t.title if t else None,
            top_trigger_date=t.event_date if t else None,
            top_trigger_age_days=t.age_days if t else None,
            active_triggers="; ".join(a.active_triggers),
            flags="; ".join(a.flags),
        )
        values: list[Any] = [row.get(c) for c in CSV_COLUMNS]
        for s in signals:
            v = a.signals.get(s)
            values += [v.raw if v else None, v.pct if v else None]
        values.append(a.simulated)
        writer.writerow(["" if v is None else v.isoformat() if isinstance(v, date) else v for v in values])
        yield flush()


@router.get(
    "/export.csv",
    response_class=CSVResponse,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}, **PRODUCT_ERRORS},
)
def export_accounts(filters: dict[str, Any] = Depends(_filters)) -> CSVResponse:
    """The rows of `GET /accounts` with the same filters, one line per account, signals flattened."""
    df = _filtered(**filters)
    items = [AccountSummary(**row) for row in df.to_dicts()]
    signals = [s["signal"] for s in store.mart_meta("accounts").get("signals", [])]
    as_of = product_meta(ACCOUNTS, rows=store.frame(ACCOUNTS)).data_as_of or date.today().isoformat()
    return CSVResponse(
        _csv_rows(items, signals),
        headers={"Content-Disposition": f'attachment; filename="basecast-accounts-{as_of}.csv"'},
    )


# The detail payload as basecast-airflow wrote its first build; `_conform` maps it to the contract (X9 §4) and
# does nothing once the mart writes the contract's keys.
DATA_CENTER_KEYS = {
    "reg_ent_name": "name",
    "ref_num_txt": "tceq_rn",
    "first_affil_begin_dt": "first_permit_date",
}


def _event_title(event: dict[str, Any]) -> dict[str, Any]:
    """Some sources have no title (TPIT projects without a name): the event's detail stands in."""
    if event.get("title") is None:
        return {**event, "title": event.get("detail") or event.get("label") or event.get("trigger")}
    return event


def _trigger_labels() -> dict[str, str]:
    items = store.mart_meta("glossary").get("items", [])
    return {i["code"]: i["text"] for i in items if i.get("kind") == "trigger"}


def _conform(payload: dict[str, Any]) -> dict[str, Any]:
    territory = dict(payload.get("territory") or {})
    sites = []
    for site in territory.get("data_centers") or []:
        site = {DATA_CENTER_KEYS.get(k, k): v for k, v in site.items()}
        if str(site.get("matched_by", "")).lower().startswith("naics"):
            site["matched_by"] = "naics"
        sites.append(site)
    territory["data_centers"] = sites
    triggers = dict(payload.get("triggers") or {})
    triggers["active"] = [_event_title(e) for e in triggers.get("active") or []]
    if any("label" not in c for c in triggers.get("context_summary") or []):
        labels = _trigger_labels()
        triggers["context_summary"] = [
            {"label": labels.get(c["trigger"], c["trigger"]), **c}
            for c in triggers.get("context_summary") or []
        ]
    return {**payload, "territory": territory, "triggers": triggers}


def _known(account_id: str) -> dict[str, Any]:
    row = store.frame(ACCOUNTS).filter(pl.col("account_id") == account_id)
    if not row.height:
        raise HTTPException(404, "not_found")
    return row.row(0, named=True)


@router.get("/{account_id}", response_model=AccountDetailResponse, responses={**NOT_FOUND, **PRODUCT_ERRORS})
def account_detail(account_id: str = ACCOUNT_ID) -> AccountDetailResponse:
    """The diagnosis of one account: header facts, score breakdown, next action, triggers, territory, EIA
    series and data gaps. Every scalar is a Fact with its source and as-of date."""
    summary = _known(account_id)
    payload = next((r["payload"] for r in store.records(DETAIL) if r["account_id"] == account_id), None)
    if payload is None:
        raise HTTPException(404, "not_found")
    detail = AccountDetail(
        **{**_conform(payload), "name": summary["name"], "account_type": summary["account_type"]}
    )
    facts = [*detail.header, *detail.territory.facts]
    return AccountDetailResponse(
        meta=product_meta(
            DETAIL,
            rows=store.frame(ACCOUNTS).filter(pl.col("account_id") == account_id),
            verified=all(f.verified for f in facts),
            caveats=_caveats(store.frame(ACCOUNTS)),
        ),
        data=detail,
    )


@router.get("/{account_id}/events", response_model=EventsResponse, responses={**NOT_FOUND, **PRODUCT_ERRORS})
def account_events(
    account_id: str = ACCOUNT_ID,
    since: date | None = Query(None, description="Events on or after this date"),
    trigger: list[str] | None = Query(None),
    strength: Strength | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> EventsResponse:
    """The account's full event history, newest first."""
    _known(account_id)
    # An event without a date (a few expired agreements) has no place on the timeline and is never active.
    df = store.frame(EVENTS).filter(pl.col("account_id") == account_id, pl.col("event_date").is_not_null())
    if since:
        df = df.filter(pl.col("event_date") >= since)
    if trigger:
        df = df.filter(pl.col("trigger").is_in(trigger))
    if strength:
        df = df.filter(pl.col("strength") == strength)
    df = df.sort(["event_date", "trigger"], descending=[True, False])
    return EventsResponse(
        meta=product_meta(EVENTS, rows=store.frame(ACCOUNTS), caveats=["by_county_not_point"]),
        data=EventsPage(
            items=[Event(**_event_title(row)) for row in df.slice(offset, limit).to_dicts()],
            total=df.height,
            offset=offset,
            limit=limit,
        ),
    )
