"""BigQuery (`basecast-509812.basecast`), where the large series live."""

from __future__ import annotations

from functools import lru_cache

from google.cloud import bigquery

from basecast_get_data.config import get_settings


@lru_cache(maxsize=1)
def client() -> bigquery.Client:
    s = get_settings()
    return bigquery.Client(project=s.gcp_project, location=s.bq_location)


def table_ref(name: str) -> str:
    s = get_settings()
    return f"{s.gcp_project}.{s.bq_dataset}.{name}"
