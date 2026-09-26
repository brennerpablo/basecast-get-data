"""The pipeline's dataset registry (`public.dataset_registry`, published by basecast-airflow).

One row per (source_id, dataset): a SQL dataset built from several sources appears once per source, so
the registry is grouped by dataset here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from psycopg import errors as pg_errors

from basecast_get_data.db import pg

log = logging.getLogger(__name__)

TTL_S = 60


@dataclass
class Dataset:
    name: str
    target: str
    mode: str
    sources: list[str] = field(default_factory=list)
    version: int | None = None
    description: str | None = None
    key_columns: list[str] = field(default_factory=list)
    partition_field: str | None = None
    cluster_fields: list[str] = field(default_factory=list)
    # The tables a derived dataset (a mart) reads; None for the parsers' datasets.
    inputs: list[str] | None = None


_cache: tuple[float, dict[str, Dataset]] | None = None


COLUMNS = (
    "source_id, dataset, target, mode, version, description, key_columns, partition_field, cluster_fields"
)


def _rows() -> list[dict]:
    """The registry rows; `inputs` only once basecast-airflow has added the column."""
    try:
        return pg.fetch_all(f"SELECT {COLUMNS}, inputs FROM dataset_registry ORDER BY dataset, source_id")
    except pg_errors.UndefinedColumn:
        return pg.fetch_all(f"SELECT {COLUMNS} FROM dataset_registry ORDER BY dataset, source_id")


def load() -> dict[str, Dataset]:
    """Datasets by name; empty when the database or the table is not there."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < TTL_S:
        return _cache[1]
    out: dict[str, Dataset] = {}
    if pg.db_available():
        try:
            rows = _rows()
        except Exception:
            log.exception("dataset_registry is not readable")
            rows = []
        for r in rows:
            d = out.get(r["dataset"])
            if d is None:
                d = out[r["dataset"]] = Dataset(
                    name=r["dataset"],
                    target=r["target"],
                    mode=r["mode"],
                    version=r["version"],
                    description=r["description"],
                    key_columns=list(r["key_columns"] or []),
                    partition_field=r["partition_field"],
                    cluster_fields=list(r["cluster_fields"] or []),
                    inputs=list(r["inputs"]) if r.get("inputs") else None,
                )
            if r["source_id"] not in d.sources:
                d.sources.append(r["source_id"])
    _cache = (time.monotonic(), out)
    return out


def by_source() -> dict[str, list[Dataset]]:
    out: dict[str, list[Dataset]] = {}
    for d in load().values():
        for s in d.sources:
            out.setdefault(s, []).append(d)
    return out
