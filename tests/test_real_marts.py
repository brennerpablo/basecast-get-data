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
    dated = [e for e in SAMPLES["mart_account_events"] if e["account_id"] == account_id and e["event_date"]]
    assert events["total"] == len(dated) and all(e["title"] for e in events["items"])


def test_nbu_lead_trigger_is_the_data_center_permit(real, client):
    d = client.get("/accounts/30123").json()["data"]
    assert d["next_action"]["action"] == "call_now" and d["next_action"]["changes_on"] == "2026-10-22"
    assert [s["name"] for s in d["territory"]["data_centers"]] == ["CLOUDBURST DATA CENTERS"]


def test_no_partner_field_in_the_account_marts(real):
    """Validation lock: the marts hold the 107 scored accounts and no partner field."""
    for name in ("mart_accounts", "mart_account_detail", "mart_account_events", "mart_account_counties"):
        assert all("is_base_partner" not in row for row in SAMPLES[name])
    assert "is_base_partner" not in str(SAMPLES["mart_account_detail"])
