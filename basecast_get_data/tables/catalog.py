"""One catalog over the registry, Postgres and BigQuery.

A dataset is "declared" when the registry lists it and "loaded" when its table exists where the registry
says it goes. Tables that exist but are not declared are shown too: bookkeeping (`etl_run`...) or
unregistered.
"""

from __future__ import annotations

import logging

from basecast_get_data.config import get_settings
from basecast_get_data.db import pg
from basecast_get_data.schemas.tables import TableSummary
from basecast_get_data.tables import bigquery, postgres, registry

log = logging.getLogger(__name__)


def _pg_tables() -> dict[str, postgres.PgTable]:
    if not pg.db_available():
        return {}
    try:
        return postgres.tables()
    except Exception:
        log.exception("Postgres catalog unavailable")
        return {}


def summaries() -> list[TableSummary]:
    s = get_settings()
    reg = registry.load()
    pgt = _pg_tables()
    bqt = bigquery.tables()
    out: list[TableSummary] = []
    for d in reg.values():
        if d.target == "bigquery":
            t = bqt.get(d.name)
            rows, size, estimated = (t.rows, t.bytes, False) if t else (None, None, False)
            loaded, location = t is not None, s.bq_dataset
        else:
            p = pgt.get(d.name)
            rows, size, estimated = (p.rows_estimate, p.bytes, True) if p else (None, None, False)
            loaded, location = p is not None, s.pg_schema
        out.append(
            TableSummary(
                name=d.name,
                engine="bigquery" if d.target == "bigquery" else "postgres",
                location=location,
                kind="dataset",
                sources=d.sources,
                mode=d.mode,
                description=d.description,
                inputs=d.inputs,
                declared=True,
                loaded=loaded,
                rows=rows,
                rows_estimated=estimated and rows is not None,
                bytes=size,
            )
        )
    for name, p in pgt.items():
        if name in reg:
            continue
        out.append(
            TableSummary(
                name=name,
                engine="postgres",
                location=s.pg_schema,
                kind="system" if name in postgres.SYSTEM else "unregistered",
                sources=[],
                declared=False,
                loaded=True,
                rows=p.rows_estimate,
                rows_estimated=p.rows_estimate is not None,
                bytes=p.bytes,
            )
        )
    for name, t in bqt.items():
        if name in reg:
            continue
        out.append(
            TableSummary(
                name=name,
                engine="bigquery",
                location=s.bq_dataset,
                kind="unregistered",
                sources=[],
                declared=False,
                loaded=True,
                rows=t.rows,
                bytes=t.bytes,
            )
        )
    out.sort(key=lambda t: (t.kind != "dataset", not t.loaded, t.name))
    return out


def find(name: str) -> TableSummary | None:
    return next((t for t in summaries() if t.name == name), None)
