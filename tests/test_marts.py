"""The marts read from Postgres (DATA_MODE=marts or MARTS_LIVE), against a fake database built from the
fixtures with Postgres types (dates, numeric as Decimal, jsonb as dicts)."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any

import polars as pl
import pytest
from psycopg import errors as pg_errors

from basecast_get_data import config
from basecast_get_data.db import pg
from basecast_get_data.products import marts, store
from basecast_get_data.tables import catalog, registry

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMERIC = {"score", "meters", "priority", "capacity_mw"}


def _typed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """As Postgres types them: a column of ISO dates is a date column, and numeric is Decimal."""
    columns = {c for r in rows for c in r}
    dates = {
        c
        for c in columns
        if (values := [r[c] for r in rows if r.get(c) is not None])
        and all(isinstance(v, str) and ISO_DATE.match(v) for v in values)
    }

    def value(column: str, v: Any) -> Any:
        if v is None:
            return None
        if column in dates:
            return date.fromisoformat(v)
        if column in NUMERIC and isinstance(v, float):
            return Decimal(str(v))
        return v

    return [{k: value(k, v) for k, v in r.items()} for r in rows]


def _tables() -> dict[str, list[dict[str, Any]]]:
    out = {}
    for name in marts.MARTS:
        out[name] = _typed(json.loads((marts.FIXTURES / f"{name}.json").read_text())["rows"])
    meta = json.loads((marts.FIXTURES / "mart_meta.json").read_text())["marts"]
    out["mart_meta"] = [
        {"mart": m, "key": k, "value": v} for m, values in meta.items() for k, v in values.items()
    ]
    return out


class FakePostgres:
    def __init__(self) -> None:
        self.tables = _tables()
        self.full_reads: Counter[str] = Counter()

    def fetch_all(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        if "information_schema.columns" in query:
            rows = self.tables.get(params[1])
            columns = dict.fromkeys(c for r in rows for c in r) if rows is not None else {}
            return [{"column_name": c} for c in columns]
        name = re.search(r'FROM public\."(\w+)"', query).group(1)
        rows = self.tables[name]
        if query.startswith("SELECT count(*)"):
            out: dict[str, Any] = {"n": len(rows)}
            for column in ("built_at", "model_version", "as_of"):
                if f"max({column})" in query:
                    out[column] = max(
                        (str(r[column]) for r in rows if r.get(column) is not None), default=None
                    )
            return [out]
        self.full_reads[name] += 1
        return [dict(r) for r in rows]


@pytest.fixture
def fake_pg(monkeypatch):
    fake = FakePostgres()
    monkeypatch.setattr(pg, "fetch_all", fake.fetch_all)
    return fake


def _mode(monkeypatch, data_mode: str, live: str = "") -> None:
    monkeypatch.setenv("DATA_MODE", data_mode)
    monkeypatch.setenv("MARTS_LIVE", live)
    config.get_settings.cache_clear()
    store.reset()


def test_marts_mode_serves_the_mart_rows_as_real_data(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "marts")
    body = client.get("/accounts").json()
    assert body["meta"]["simulated"] is False
    assert "fixture" not in [c["code"] for c in body["meta"]["caveats"]]
    assert body["data"]["total"] == len(fake_pg.tables["mart_accounts"])
    assert store.frame("mart_accounts")["score"].dtype == pl.Float64
    assert client.get("/accounts/FX001").status_code == 200


def test_marts_mode_unknown_account_is_the_common_404(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "marts")
    r = client.get("/accounts/NOT-IN-THE-MART")
    assert r.status_code == 404 and r.json() == {"detail": "not_found"}


def test_every_endpoint_answers_in_marts_mode(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "marts")
    for path in (
        "/accounts",
        "/accounts/export.csv",
        "/accounts/FX002/events",
        "/geo/counties",
        "/geo/counties/48001",
        "/queue/projects",
        "/forecasts/peak",
        "/forecasts/large-load",
        "/backtest/peak",
        "/backtest/official-errors",
        "/backtest/queue",
        "/glossary",
    ):
        assert client.get(path).status_code == 200, path


def test_a_missing_mart_is_503_never_a_fixture(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "marts")
    del fake_pg.tables["mart_peak_backtest"]
    r = client.get("/backtest/peak")
    assert r.status_code == 503
    assert r.json() == {"detail": "mart_not_built", "mart": "mart_peak_backtest"}


def test_a_mart_without_the_columns_the_api_reads_is_not_built(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "marts")
    fake_pg.tables["mart_queue_backtest"] = [
        {k: v for k, v in r.items() if k != "pred_mw"} for r in fake_pg.tables["mart_queue_backtest"]
    ]
    r = client.get("/backtest/queue")
    assert r.status_code == 503 and r.json()["mart"] == "mart_queue_backtest"


def test_groups_go_live_one_by_one(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "fixtures", live="accounts")
    accounts = client.get("/accounts").json()["meta"]
    forecast = client.get("/forecasts/peak").json()["meta"]
    assert accounts["simulated"] is False and fake_pg.full_reads["mart_accounts"] == 1
    assert forecast["simulated"] is True and "fixture" in [c["code"] for c in forecast["caveats"]]
    assert fake_pg.full_reads["mart_peak_forecast"] == 0
    # The county panel mixes live accounts with the explorer's fixtures, so it still says simulated.
    fips = fake_pg.tables["mart_account_counties"][0]["county_fips"]
    assert client.get(f"/geo/counties/{fips}").json()["meta"]["simulated"] is True


def test_refresh_reloads_only_a_changed_build(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "marts")
    client.get("/accounts")
    assert store.refresh() == []
    assert fake_pg.full_reads["mart_accounts"] == 1
    for r in fake_pg.tables["mart_accounts"]:
        r["model_version"] = "next-build"
    assert "mart_accounts" in store.refresh()
    assert client.get("/accounts").json()["meta"]["model_version"] == "next-build"
    del fake_pg.tables["mart_accounts"]
    assert "mart_accounts" in store.refresh()
    assert client.get("/accounts").status_code == 503


def test_health_reports_the_mode(client, fake_pg, monkeypatch):
    _mode(monkeypatch, "fixtures", live="backtest")
    client.get("/backtest/queue")
    h = client.get("/health").json()
    assert h["data_mode"] == "fixtures" and h["marts_live"] == ["backtest"]
    assert h["marts_loaded"] == {"mart_queue_backtest": len(fake_pg.tables["mart_queue_backtest"])}


def test_glossary_labels_every_code(client):
    items = client.get("/glossary").json()["items"]
    kinds = Counter(i["kind"] for i in items)
    assert kinds["next_action"] == 4 and kinds["flag"] == 5 and kinds["trigger"] >= 8
    assert all(i["strength"] in ("strong", "context") for i in items if i["kind"] == "trigger")
    assert all(i["strength"] is None for i in items if i["kind"] != "trigger")


# --- /tables inputs ---------------------------------------------------------------------------------------


REGISTRY_ROW = {
    "source_id": "marts",
    "dataset": "mart_actual_summer_peaks",
    "target": "postgres",
    "mode": "replace",
    "version": 1,
    "description": "Summer peaks",
    "key_columns": ["year"],
    "partition_field": None,
    "cluster_fields": None,
}


def test_tables_carry_the_inputs_of_a_mart(client, monkeypatch):
    monkeypatch.setattr(pg, "db_available", lambda: True)
    monkeypatch.setattr(registry, "_rows", lambda: [{**REGISTRY_ROW, "inputs": ["ercot_load_hourly_wz"]}])
    monkeypatch.setattr(catalog, "_pg_tables", lambda: {})
    items = client.get("/tables").json()["data"]["items"]
    mart = next(t for t in items if t["name"] == "mart_actual_summer_peaks")
    assert mart["inputs"] == ["ercot_load_hourly_wz"] and mart["sources"] == ["marts"]


def test_registry_reads_without_the_inputs_column(monkeypatch):
    queries = []

    def fetch_all(query, params=None):
        queries.append(query)
        if "inputs" in query:
            raise pg_errors.UndefinedColumn("column inputs does not exist")
        return [REGISTRY_ROW]

    monkeypatch.setattr(pg, "db_available", lambda: True)
    monkeypatch.setattr(pg, "fetch_all", fetch_all)
    datasets = registry.load()
    assert datasets["mart_actual_summer_peaks"].inputs is None and len(queries) == 2
