"""The real marts, trimmed into tests/fixtures/marts by scripts/sample_marts.py: they load under the contract
and the API serves the numbers the analysis docs report."""

from __future__ import annotations

import pytest

from basecast_get_data import config
from basecast_get_data.db import pg
from basecast_get_data.products import marts, store
from basecast_get_data.schemas.backtest import ActualPeak
from tests.fake_pg import FakePostgres, fixture_tables, samples

SAMPLES = samples()
# The row model each mart's rows must validate against.
ROW_MODELS = {"mart_actual_summer_peaks": ActualPeak}


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
    df = store.frame(name)
    assert df.height == len(SAMPLES[name])
    model = ROW_MODELS.get(name)
    if model:
        for row in df.to_dicts():
            model(**row)


def test_actual_summer_peaks_match_q1(real):
    peaks = {r["year"]: r for r in store.frame("mart_actual_summer_peaks").to_dicts()}
    assert peaks[2026]["hourly_peak_mw"] == pytest.approx(91_134, abs=1)
    assert peaks[2026]["hour_ending_local"] == 18 and peaks[2026]["final"] is False
    assert peaks[2023]["hourly_peak_mw"] == pytest.approx(85_508, abs=1)
    assert "preliminary_actuals" in store.mart_caveats("mart_actual_summer_peaks")
