"""The real marts, trimmed into tests/fixtures/marts by scripts/sample_marts.py: they load under the contract
and the API serves the numbers the analysis docs report."""

from __future__ import annotations

import pytest

from basecast_get_data import config
from basecast_get_data.db import pg
from basecast_get_data.products import marts, store
from basecast_get_data.schemas.accounts import AccountCounty, AccountSummary
from basecast_get_data.schemas.backtest import ActualPeak
from tests.fake_pg import FakePostgres, fixture_tables, samples

SAMPLES = samples()
# The row model each mart's rows must validate against.
ROW_MODELS = {
    "mart_actual_summer_peaks": ActualPeak,
    "mart_accounts": AccountSummary,
    "mart_account_counties": AccountCounty,
}
X9_ACCOUNTS = ("30123", "30120", "30012")


@pytest.fixture
def real(monkeypatch):
    tables = fixture_tables()
    real_meta = SAMPLES.get("mart_meta", [])
    sampled = {r["mart"] for r in real_meta}
    tables.update(SAMPLES)
    tables["mart_meta"] = [r for r in fixture_tables()["mart_meta"] if r["mart"] not in sampled] + real_meta
    monkeypatch.setattr(pg, "fetch_all", FakePostgres(tables).fetch_all)
    monkeypatch.setenv("DATA_MODE", "marts")
    config.get_settings.cache_clear()
    store.reset()
    return tables


@pytest.mark.parametrize("name", sorted(n for n in SAMPLES if n in marts.MARTS))
def test_real_mart_has_the_columns_the_api_reads(real, name):
    # The detail's payload mixes value types, so the API reads it as records, never as a frame.
    rows = store.records(name) if name == "mart_account_detail" else store.frame(name).to_dicts()
    assert len(rows) == len(SAMPLES[name])
    model = ROW_MODELS.get(name)
    if model:
        for row in rows:
            model(**row)


def test_actual_summer_peaks_match_q1(real):
    peaks = {r["year"]: r for r in store.frame("mart_actual_summer_peaks").to_dicts()}
    assert peaks[2026]["hourly_peak_mw"] == pytest.approx(91_134, abs=1)
    assert peaks[2026]["hour_ending_local"] == 18 and peaks[2026]["final"] is False
    assert peaks[2023]["hourly_peak_mw"] == pytest.approx(85_508, abs=1)
    assert "preliminary_actuals" in store.mart_caveats("mart_actual_summer_peaks")


def test_every_summer_before_the_latest_is_final(real):
    peaks = store.frame("mart_actual_summer_peaks").sort("year").to_dicts()
    assert all(p["final"] for p in peaks[:-1]) and peaks[-1]["final"] is False


# --- accounts (A-M3; golden numbers from X5 and X9) -------------------------------------------------------


def test_accounts_list_serves_x5_numbers(real, client):
    body = client.get("/accounts").json()
    items = body["data"]["items"]
    assert body["meta"]["simulated"] is False and body["data"]["total"] == 107
    assert sum(1 for a in items if a["n_strong"] > 0) == 46
    actions = {
        a: sum(1 for i in items if i["next_action"] == a) for a in ("call_now", "nurture", "watch", "hold")
    }
    assert actions == {"call_now": 25, "nurture": 12, "watch": 34, "hold": 36}
    first = items[0]
    assert first["name"] == "New Braunfels Utilities" and first["rank"] == 1
    assert first["action_changes_on"] == "2026-10-22" and first["action_changes_to"] == "nurture"


@pytest.mark.parametrize("account_id", X9_ACCOUNTS)
def test_x9_diagnoses_conform_to_the_contract(real, client, account_id):
    d = client.get(f"/accounts/{account_id}").json()["data"]
    assert all(f["source"] for f in [*d["header"], *d["territory"]["facts"]])
    assert all(c["label"] for c in d["triggers"]["context_summary"])
    assert all(site["name"] and site["tceq_rn"] for site in d["territory"]["data_centers"])
    events = client.get(f"/accounts/{account_id}/events", params={"limit": 500}).json()["data"]
    own = [e for e in SAMPLES["mart_account_events"] if e["account_id"] == account_id]
    assert events["total"] == len(own) and all(e["title"] and e["event_date"] for e in events["items"])


def test_nbu_lead_trigger_is_the_data_center_permit(real, client):
    d = client.get("/accounts/30123").json()["data"]
    assert d["next_action"]["action"] == "call_now" and d["next_action"]["changes_on"] == "2026-10-22"
    assert [s["name"] for s in d["territory"]["data_centers"]] == ["CLOUDBURST DATA CENTERS"]


def test_no_partner_field_in_the_account_marts(real):
    """Validation lock: the marts hold the 107 scored accounts and no partner field."""
    for name in ("mart_accounts", "mart_account_detail", "mart_account_events", "mart_account_counties"):
        assert all("is_base_partner" not in row for row in SAMPLES[name])
    assert "is_base_partner" not in str(SAMPLES["mart_account_detail"])


# --- backtest (A-M6; golden numbers from X7, Q1 and X2) ---------------------------------------------------


def test_backtest_serves_x7_paired_scores(real, client):
    body = client.get("/backtest/peak").json()
    d = body["data"]
    assert body["meta"]["simulated"] is False and len(d["as_of_dates"]) == 8
    pairs = {c["official_source"]: c for c in d["comparisons"] if c["era"] == "all"}
    assert pairs["LTLF"]["n"] == 18 and pairs["LTLF"]["basecast_mape"] == pytest.approx(3.30, abs=0.01)
    assert pairs["LTLF"]["official_mape"] == pytest.approx(5.06, abs=0.01)
    assert pairs["CDR"]["n"] == 15 and pairs["CDR"]["basecast_mape"] == pytest.approx(3.24, abs=0.01)
    assert pairs["CDR"]["official_mape"] == pytest.approx(4.81, abs=0.01)
    ablation = {s["source"]: s for s in d["ablation"]}
    assert ablation["basecast_organic_only"]["mape"] == pytest.approx(10.46, abs=0.01)
    assert ablation["basecast"]["coverage"] == pytest.approx(10 / 18)


def test_backtest_latest_cell_and_fan(real, client):
    d = client.get("/backtest/peak", params={"as_of": "2026-05-31"}).json()["data"]
    ours = next(c for c in d["cells"] if c["source"] == "basecast" and c["target_year"] == 2026)
    assert ours["p50_mw"] == pytest.approx(89_037, abs=1)
    kinds = [f["kind"] for f in d["fan"]]
    assert kinds.count("official_preliminary") == 1 and kinds.count("model") == 8
    prelim = next(f for f in d["fan"] if f["kind"] == "official_preliminary")
    band = next(f for f in d["fan"] if f["kind"] == "official_range")
    assert prelim["value_mw"] == 112_000 and (band["low_mw"], band["high_mw"]) == (90_500, 98_000)


def test_official_errors_and_queue_backtest_match_q1_and_x2(real, client):
    assert len(client.get("/backtest/official-errors").json()["data"]["items"]) == 354
    q = client.get("/backtest/queue").json()["data"]
    statewide = [round(i["error_pct"], 1) for i in q["items"] if i["stratum"] == "all"]
    assert statewide == [-13.2, 9.3, 1.4]
    assert all(0.63 <= r["rho_adj"] <= 0.67 and 0.42 <= r["rho_raw"] <= 0.46 for r in q["county_rank"])


# --- explorer (A-M4; golden numbers from X2, X14 and Q4) --------------------------------------------------


def test_explorer_serves_x2_and_x14_numbers(real, client):
    body = client.get("/geo/counties").json()
    items = body["data"]["items"]
    assert body["meta"]["simulated"] is False and len(items) == 254
    with_queue = [i["queue"] for i in items if i["queue"]]
    assert sum(q["raw_mw"] for q in with_queue) == pytest.approx(438_261.6, abs=0.5)
    assert sum(q["adj_mw"] for q in with_queue) == pytest.approx(70_394.4, abs=0.5)
    early = client.get("/geo/counties", params={"horizon": 2027}).json()["data"]["items"]
    assert sum(i["queue"]["adj_mw"] for i in early if i["queue"]) == pytest.approx(38_689.3, abs=0.5)
    acquisition = [i["acquisition"] for i in items if i["acquisition"]]
    assert len(acquisition) == 204
    channels = {
        c: sum(1 for a in acquisition if a["channel"] == c) for c in ("partnership", "retail_direct", "mixed")
    }
    assert channels == {"partnership": 161, "retail_direct": 39, "mixed": 4}
    first = next(i for i in items if i["acquisition"] and i["acquisition"]["rank"] == 1)
    assert first["county_name"] == "Comal"


def test_data_center_counts_keep_sites_outside_ercot_out(real, client):
    """Q4 / R7: 38 new sites in 27 counties, of which 31 in ERCOT; the rest are flagged, not counted."""
    items = client.get("/geo/counties").json()["data"]["items"]
    counted = sum(i["data_centers"]["sites"] for i in items)
    outside = sum(i["data_centers"]["sites_outside_ercot"] for i in items)
    assert counted + outside == 38 and counted == 31


def test_queue_projects_of_the_sampled_counties(real, client):
    body = client.get("/queue/projects", params={"limit": 500}).json()["data"]
    assert body["total"] == len(SAMPLES["mart_queue_project_scores"])
    mw = [p["mw_2028"] for p in body["items"]]
    assert mw == sorted(mw, reverse=True)
