"""The contract v2 product endpoints, served from the fixtures in basecast_get_data/data/fixtures."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from basecast_get_data.products import store
from basecast_get_data.schemas.caveats import CATALOG, CODES

SPEC = Path(__file__).resolve().parent.parent / "openapi.json"


def test_every_caveat_code_has_a_standard_text(client):
    assert set(CATALOG) == set(CODES)
    items = client.get("/caveats").json()["items"]
    assert [i["code"] for i in items] == list(CODES)
    assert all(i["label"] and i["text"] for i in items)


def test_fixture_responses_say_simulated_and_carry_caveat_texts(client):
    meta = client.get("/accounts").json()["meta"]
    assert meta["simulated"] is True
    assert meta["model_version"] == "fixture-v2"
    codes = [c["code"] for c in meta["caveats"]]
    assert "fixture" in codes and "weights_pending_review" in codes
    assert all(c["text"] == CATALOG[c["code"]][1] for c in meta["caveats"])


# --- accounts ---------------------------------------------------------------------------------------------


def test_accounts_list_is_every_mart_row_ranked(client):
    data = client.get("/accounts").json()["data"]
    assert data["total"] == store.frame("mart_accounts").height == len(data["items"])
    assert [a["rank"] for a in data["items"]] == sorted(a["rank"] for a in data["items"])
    assert {s["signal"] for s in data["signals"]} == set(data["items"][0]["signals"])


def test_accounts_filters(client):
    munis = client.get("/accounts", params={"type": "muni"}).json()["data"]["items"]
    assert munis and {a["account_type"] for a in munis} == {"muni"}
    ab = client.get("/accounts", params=[("tier", "A"), ("tier", "B")]).json()["data"]["items"]
    assert {a["tier"] for a in ab} <= {"A", "B"}
    named = client.get("/accounts", params={"q": "SAMPLE city"}).json()["data"]["items"]
    assert [a["name"] for a in named] == ["Sample City Utilities"]
    dc = client.get("/accounts", params={"trigger": "market_registration"}).json()["data"]["items"]
    assert dc and all("market_registration" in a["active_triggers"] for a in dc)


def test_accounts_county_filter_uses_the_account_counties(client):
    link = store.frame("mart_account_counties").row(0, named=True)
    items = client.get("/accounts", params={"county": link["county_fips"]}).json()["data"]["items"]
    assert link["account_id"] in {a["account_id"] for a in items}


def test_rank_scope_within_type_orders_by_type_then_rank(client):
    items = client.get("/accounts", params={"rank_scope": "within_type"}).json()["data"]["items"]
    keys = [(a["account_type"], a["rank_within_type"]) for a in items]
    assert keys == sorted(keys)


def test_sort_by_score_descending(client):
    items = client.get("/accounts", params={"sort": "score", "desc": "true"}).json()["data"]["items"]
    scores = [a["score"] for a in items]
    assert scores == sorted(scores, reverse=True)


def test_csv_export_has_the_list_rows(client):
    r = client.get("/accounts/export.csv", params={"type": "coop"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    listed = client.get("/accounts", params={"type": "coop"}).json()["data"]["items"]
    assert [row["account_id"] for row in rows] == [a["account_id"] for a in listed]
    assert "pct_pop_growth" in rows[0] and "top_trigger_title" in rows[0]


def test_account_detail_is_a_diagnosis_of_facts(client):
    body = client.get("/accounts/FX001").json()
    d = body["data"]
    assert d["account_id"] == "FX001" and d["name"]
    assert all({"value", "source", "as_of", "verified", "simulated"} <= set(f) for f in d["header"])
    assert sum(s["contribution"] for s in d["score"]["signals"]) == pytest.approx(
        d["score"]["score"], abs=1e-3
    )
    assert d["triggers"]["history_count"] >= len(d["triggers"]["active"])


@pytest.mark.parametrize("path", ["/accounts/UNKNOWN", "/accounts/30123", "/accounts/UNKNOWN/events"])
def test_unknown_account_answers_the_same_404(client, path):
    """Validation lock: a held-out account looks exactly like any unknown id."""
    r = client.get(path)
    assert r.status_code == 404
    assert r.json() == {"detail": "not_found"}


def test_account_events_page_and_filters(client):
    all_events = client.get("/accounts/FX001/events", params={"limit": 500}).json()["data"]
    page = client.get("/accounts/FX001/events", params={"limit": 2, "offset": 1}).json()["data"]
    assert page["total"] == all_events["total"]
    assert page["items"] == all_events["items"][1:3]
    dates = [e["event_date"] for e in all_events["items"]]
    assert dates == sorted(dates, reverse=True)
    strong = client.get("/accounts/FX001/events", params={"strength": "strong"}).json()["data"]["items"]
    assert all(e["strength"] == "strong" for e in strong)


def test_no_partner_field_anywhere_in_the_contract():
    """Validation lock: no is_base_partner until the reveal (A-M8)."""
    assert "is_base_partner" not in SPEC.read_text()


def test_fixture_accounts_are_fictional():
    ids = store.frame("mart_accounts")["account_id"].to_list()
    assert ids and all(i.startswith("FX") for i in ids)


# --- explorer ---------------------------------------------------------------------------------------------


def test_counties_cover_texas_with_null_priority_outside_ercot(client):
    data = client.get("/geo/counties").json()["data"]
    items = data["items"]
    assert len(items) == 254
    assert all(i["acquisition"] is None for i in items if not i["in_ercot"])
    assert all(i["acquisition"] is not None for i in items if i["in_ercot"])
    assert len(data["legend"]["breaks"]) == data["legend"]["classes"] - 1


def test_counties_queue_follows_horizon_and_stratum(client):
    r = client.get("/geo/counties", params={"horizon": 2027, "stratum": "storage"})
    assert r.status_code == 200
    body = r.json()
    rows = [i for i in body["data"]["items"] if i["queue"]]
    mart = store.frame("mart_queue_adjusted_county")
    expected = mart.filter(mart["stratum"] == "storage")
    assert len(rows) == expected.height
    by_fips = {r["county_fips"]: r for r in expected.to_dicts()}
    for row in rows:
        assert row["queue"]["adj_mw"] == by_fips[row["county_fips"]]["adj_mw_2027"]
        assert row["queue"]["rank_change"] == row["queue"]["rank_raw"] - row["queue"]["rank_adj"]
    assert "beyond_backtested_window" not in [c["code"] for c in body["meta"]["caveats"]]
    assert client.get("/geo/counties", params={"horizon": 2030}).status_code == 422


def test_county_detail(client):
    fips = store.frame("mart_account_counties").row(0, named=True)["county_fips"]
    d = client.get(f"/geo/counties/{fips}").json()["data"]
    assert d["county_fips"] == fips
    assert len(d["signals"]) == 8
    assert d["accounts"] and all(a["county_share"] >= 0.01 for a in d["accounts"])
    mw = [p["mw_2028"] for p in d["top_projects"]]
    assert mw == sorted(mw, reverse=True) and len(mw) <= 10
    assert client.get("/geo/counties/99999").json() == {"detail": "not_found"}


def test_queue_projects_filter_and_page(client):
    body = client.get("/queue/projects", params={"stratum": "storage", "limit": 5}).json()
    items = body["data"]["items"]
    assert len(items) == 5 and all(p["stratum"] == "storage" for p in items)
    assert [p["mw_2028"] for p in items] == sorted((p["mw_2028"] for p in items), reverse=True)
    assert body["data"]["total"] >= 5


# --- forecast ---------------------------------------------------------------------------------------------


def test_peak_forecast_default_variant_and_layers(client):
    body = client.get("/forecasts/peak").json()
    d = body["data"]
    assert d["variant"] == next(v["variant"] for v in d["variants"] if v["is_default"])
    assert {layer["layer"] for layer in d["layers"]} == {"organic", "large_load", "unattributed"}
    for point in d["series"]:
        parts = [x["p50_mw"] for x in d["layers"] if x["target_year"] == point["target_year"]]
        assert sum(parts) == pytest.approx(point["p50_mw"], abs=1)
        assert point["verified"] is False
    assert body["meta"]["verified"] is False
    codes = [c["code"] for c in body["meta"]["caveats"]]
    assert "machine_read_unverified" in codes and "band_uncalibrated" in codes
    assert "allocated_statewide" not in codes


def test_zone_forecast_is_allocated_and_its_band_is_an_allocation_range(client):
    body = client.get("/forecasts/peak", params={"region": "FWEST"}).json()
    d = body["data"]
    assert d["available"] and "allocated_statewide" in [c["code"] for c in body["meta"]["caveats"]]
    kinds = {x["layer"]: x["band_kind"] for x in d["layers"]}
    assert kinds == {
        "organic": "p10_p90",
        "large_load": "allocation_range",
        "unattributed": "allocation_range",
    }
    assert {p["band_kind"] for p in d["series"]} == {"allocation_range"}
    assert d["inputs"]["share_of_ll_u"] is not None


def test_pace_variant_has_no_band_and_no_zones(client):
    d = client.get("/forecasts/peak", params={"variant": "approvals_pace"}).json()
    assert all(p["p10_mw"] is None and p["band_kind"] is None for p in d["data"]["series"])
    assert "band_uncalibrated" not in [c["code"] for c in d["meta"]["caveats"]]
    pace = next(v for v in d["data"]["variants"] if v["variant"] == "approvals_pace")
    assert pace["regions"] == ["ERCOT"]
    zone = client.get("/forecasts/peak", params={"region": "FWEST", "variant": "approvals_pace"}).json()[
        "data"
    ]
    assert zone["available"] is False and zone["series"] == [] and zone["layers"] == []


def test_large_load_rows_carry_verified(client):
    d = client.get("/forecasts/large-load").json()["data"]
    assert d["realization"] and all(r["verified"] is False for r in d["realization"])
    assert d["ratio_band"]["p50"] is not None
    months = [m["month"] for m in d["monthly"]]
    assert months == sorted(months)


# --- backtest ---------------------------------------------------------------------------------------------


def test_backtest_peak_defaults_to_the_latest_date(client):
    d = client.get("/backtest/peak").json()["data"]
    assert d["as_of"] == d["as_of_dates"][-1]
    assert d["cells"] and all(c["as_of"] == d["as_of"] for c in d["cells"])
    assert {s["source"] for s in d["ablation"]} == {"basecast", "basecast_organic_only"}
    assert {s["era"] for s in d["scores"]} == {e["era"] for e in d["eras"]} | {"all"}
    assert d["fan"] and d["fan_target_year"] == 2026


def test_backtest_scores_are_the_mean_absolute_error_of_the_cells(client):
    d = client.get("/backtest/peak").json()["data"]
    cells = store.frame("mart_peak_backtest")
    ours = [c for c in cells.to_dicts() if c["source"] == "basecast"]
    expected = sum(abs(c["error_pct"]) for c in ours) / len(ours)
    got = next(s for s in d["scores"] if s["era"] == "all" and s["source"] == "basecast")
    assert got["mape"] == pytest.approx(expected)
    assert got["coverage"] == pytest.approx(sum(c["in_band"] for c in ours) / len(ours))


def test_backtest_compares_each_official_source_on_paired_cells(client):
    d = client.get("/backtest/peak").json()["data"]
    cells = store.frame("mart_peak_backtest").to_dicts()
    cdr = {(c["as_of"], c["target_year"]) for c in cells if c["source"] == "CDR"}
    ours = [c for c in cells if c["source"] == "basecast" and (c["as_of"], c["target_year"]) in cdr]
    got = next(c for c in d["comparisons"] if c["official_source"] == "CDR" and c["era"] == "all")
    assert got["n"] == len(ours)
    assert got["basecast_mape"] == pytest.approx(sum(abs(c["error_pct"]) for c in ours) / len(ours))
    assert {c["official_source"] for c in d["comparisons"]} == {"CDR", "LTLF"}


def test_backtest_invalid_as_of_lists_the_valid_dates(client):
    r = client.get("/backtest/peak", params={"as_of": "2020-01-01"})
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "invalid_as_of" and len(body["as_of_dates"]) == 8


def test_official_errors_and_queue_backtest(client):
    d = client.get("/backtest/official-errors", params={"product": "CDR"}).json()["data"]
    assert d["items"] and {e["product"] for e in d["items"]} == {"CDR"}
    assert {"LTLF", "CDR"} <= set(d["products"])
    q = client.get("/backtest/queue").json()["data"]
    assert q["items"] and len(q["county_rank"]) == 3


# --- errors -----------------------------------------------------------------------------------------------


def test_missing_fixture_answers_503_mart_not_built(client, monkeypatch):
    monkeypatch.setattr(store, "FIXTURES", store.FIXTURES / "nowhere")
    store.reset()
    r = client.get("/backtest/peak")
    assert r.status_code == 503
    assert r.json() == {"detail": "mart_not_built", "mart": "mart_peak_backtest"}


def test_product_endpoints_declare_the_503_body():
    spec = json.loads(SPEC.read_text())
    for path in ("/accounts", "/geo/counties", "/forecasts/peak", "/backtest/peak"):
        ref = spec["paths"][path]["get"]["responses"]["503"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/MartNotBuilt")
