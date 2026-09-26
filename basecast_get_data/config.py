"""Settings read from the environment.

The defaults are production's (Cloud Run, project basecast-509812), so the deployed service needs only
`API_TOKEN` and `PG_PASSWORD`. A local `.env` overrides the rest: the lake on disk, Postgres through the
Cloud SQL proxy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# The resource groups production reads from the marts (MARTS_LIVE overrides it). A group goes live here, in
# code, once its marts are built and passed their checks.
MARTS_LIVE = ""


@dataclass(frozen=True)
class Settings:
    api_token: str
    # gs://bucket, file:///abs/path or a path relative to the repo root.
    lake_root: str
    gcp_project: str
    bq_dataset: str
    bq_location: str
    bq_max_bytes_billed: int
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str
    pg_schema: str
    # Service account that signs GCS URLs (Cloud Run's own); empty means "the credentials' own email".
    signer_email: str
    # Files larger than this are not parsed for a preview; they stay downloadable.
    preview_max_bytes: int
    # Where remote lake files are cached for parsing (Cloud Run: in-memory tmpfs).
    cache_dir: Path
    cache_max_bytes: int
    # How long the manifest index lives before a background refresh.
    index_ttl_s: int
    # Where the product resources read their marts: "fixtures" (data/fixtures) or "marts" (public.mart_*).
    data_mode: str
    # Resource groups that read the marts even when data_mode is "fixtures" (accounts, explorer, forecast,
    # backtest): production switches group by group as basecast-airflow publishes checked marts.
    marts_live: frozenset[str]
    # How often the loaded marts are checked for a new build (built_at, model_version).
    mart_ttl_s: int

    @property
    def db_configured(self) -> bool:
        return bool(self.pg_password)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)
    env = os.environ.get
    return Settings(
        api_token=env("API_TOKEN", ""),
        lake_root=env("LAKE_ROOT", "gs://basecast-509812-lake"),
        gcp_project=env("GCP_PROJECT") or "basecast-509812",
        bq_dataset=env("BQ_DATASET") or "basecast",
        bq_location=env("BQ_LOCATION", "us-central1"),
        bq_max_bytes_billed=_int("BQ_MAX_BYTES_BILLED", 2_000_000_000),
        pg_host=env("PG_HOST", "/cloudsql/basecast-509812:us-central1:basecast-pg"),
        pg_port=_int("PG_PORT", 5432),
        pg_db=env("PG_DB", "basecast"),
        pg_user=env("PG_USER", "basecast_reader"),
        pg_password=env("PG_PASSWORD", ""),
        pg_schema=env("PG_SCHEMA", "public"),
        signer_email=env("SIGNER_EMAIL", ""),
        preview_max_bytes=_int("PREVIEW_MAX_BYTES", 120_000_000),
        cache_dir=Path(env("CACHE_DIR", "/tmp/basecast-get-data")),
        cache_max_bytes=_int("CACHE_MAX_BYTES", 600_000_000),
        index_ttl_s=_int("INDEX_TTL_S", 600),
        data_mode=(env("DATA_MODE") or "fixtures").strip().lower(),
        marts_live=frozenset(g.strip() for g in env("MARTS_LIVE", MARTS_LIVE).split(",") if g.strip()),
        mart_ttl_s=_int("MART_TTL_S", 600),
    )
