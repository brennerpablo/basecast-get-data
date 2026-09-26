"""Tests run against the trimmed lake in tests/fixtures/lake, with no database and no BigQuery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "lake"
TOKEN = "test-token"

# Set before the app reads its settings; load_dotenv never overrides what is already set.
os.environ.update(
    API_TOKEN=TOKEN,
    LAKE_ROOT=str(FIXTURES),
    PG_PASSWORD="",
    INDEX_TTL_S="3600",
    DATA_MODE="fixtures",
    MARTS_LIVE="",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from basecast_get_data import config
    from basecast_get_data.lake import service
    from basecast_get_data.products import store
    from basecast_get_data.tables import bigquery, registry

    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    config.get_settings.cache_clear()
    service.get_lake.cache_clear()
    monkeypatch.setattr(bigquery, "tables", lambda: {})
    monkeypatch.setattr(registry, "_cache", None)
    # No background mart loader in tests: they load on demand.
    monkeypatch.setattr(store, "_started", True)
    store.reset()
    yield
    config.get_settings.cache_clear()
    service.get_lake.cache_clear()
    store.reset()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from basecast_get_data.main import create_app

    with TestClient(create_app()) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c
