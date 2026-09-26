"""Processed tables in Postgres and BigQuery: catalog, schema, rows and lineage."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg import errors as pg_errors

from basecast_get_data.auth import require_token
from basecast_get_data.db import pg
from basecast_get_data.schemas.common import RowsPage, RowsResponse, meta
from basecast_get_data.schemas.tables import (
    LineagePage,
    LineageResponse,
    LineageRow,
    TableColumn,
    TableDetail,
    TableResponse,
    TablesData,
    TablesResponse,
    TableSummary,
)
from basecast_get_data.tables import bigquery, catalog, postgres, registry
from basecast_get_data.tables.filters import FilterError, parse_filters

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tables", tags=["tables"], dependencies=[Depends(require_token)])

MAX_LIMIT = 1000
# The grid lets people scroll this far; past it they sort or filter (the CNPJ grid's rule).
MAX_WINDOW = 100_000
TABLE_NAME = Path(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _summary(name: str) -> TableSummary:
    t = catalog.find(name)
    if t is None:
        raise HTTPException(404, f"no such table: {name}")
    if not t.loaded:
        raise HTTPException(409, f"{name} is declared by the pipeline but not loaded yet")
    return t


@router.get("", response_model=TablesResponse)
def list_tables() -> TablesResponse:
    return TablesResponse(meta=meta(), data=TablesData(items=catalog.summaries()))


@router.get("/{name}", response_model=TableResponse)
def table_detail(name: str = TABLE_NAME) -> TableResponse:
    t = catalog.find(name)
    if t is None:
        raise HTTPException(404, f"no such table: {name}")
    d = registry.load().get(name)
    keys = d.key_columns if d else []
    cols: list[TableColumn] = []
    partition, cluster = (d.partition_field, d.cluster_fields) if d else (None, [])
    if t.loaded and t.engine == "postgres":
        cols = [
            TableColumn(
                name=c.name,
                type=c.type,
                source_type=c.source_type,
                position=c.position,
                nullable=c.nullable,
                is_key=c.name in keys,
            )
            for c in postgres.columns(name)
        ]
    elif t.loaded:
        bt = bigquery.tables()[name]
        partition, cluster = bt.partition_field or partition, bt.cluster_fields or cluster
        cols = [
            TableColumn(
                **gc.model_dump(), position=i + 1, nullable=f.mode != "REQUIRED", is_key=f.name in keys
            )
            for i, (gc, f) in enumerate(zip(bigquery.grid_columns(bt), bt.schema, strict=True))
        ]
    detail = TableDetail(
        **t.model_dump(),
        columns=cols,
        key_columns=keys,
        partition_field=partition,
        cluster_fields=cluster,
        derived=bool(d and d.mode == "sql"),
    )
    return TableResponse(meta=meta(sources=t.sources), data=detail)


@router.get("/{name}/rows", response_model=RowsResponse)
def table_rows(
    name: str = TABLE_NAME,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=MAX_LIMIT),
    sort: str | None = Query(None, description="Column to sort by"),
    desc: bool = Query(False),
    filter: list[str] = Query(  # noqa: A002 - the query param is called filter
        default=[],
        description="column:op:value, repeatable. Ops: eq ne contains starts in gte lte gt lt between null"
        " notnull; `in` and `between` join values with U+001F",
    ),
    with_summary: bool = Query(False, description="Also return the total (exact when it fits in 6 s)"),
) -> RowsResponse:
    if offset + limit > MAX_WINDOW + MAX_LIMIT:
        raise HTTPException(422, f"rows past {MAX_WINDOW:,} are out of reach: sort or filter to narrow down")
    t = _summary(name)
    d = registry.load().get(name)
    if t.engine == "postgres":
        cols = postgres.columns(name)
        names = {c.name for c in cols}
        if sort and sort not in names:
            raise HTTPException(422, f"unknown sort column {sort!r}")
        try:
            filters = parse_filters(filter, names)
        except FilterError as e:
            raise HTTPException(422, str(e)) from e
        keys = d.key_columns if d else []
        try:
            data = postgres.rows(name, cols, filters, sort, desc, keys, offset, limit)
            total, estimated = postgres.count(name, filters, cols, t.rows) if with_summary else (None, False)
        except pg_errors.QueryCanceled as e:
            raise HTTPException(504, "The query took too long: filter or sort by an indexed column.") from e
        except (pg_errors.DataError, pg_errors.UndefinedFunction) as e:
            raise HTTPException(422, f"invalid filter value: {str(e).splitlines()[0]}") from e
        page = RowsPage(
            columns=postgres.grid_columns(cols),
            rows=data,
            offset=offset,
            limit=limit,
            total=total,
            total_estimated=estimated,
        )
    else:
        bt = bigquery.tables().get(name)
        if bt is None:
            raise HTTPException(404, f"no such BigQuery table: {name}")
        names = {f.name for f in bt.schema}
        if sort and sort not in names:
            raise HTTPException(422, f"unknown sort column {sort!r}")
        try:
            filters = parse_filters(filter, names)
            data, total = bigquery.rows(bt, filters, sort, desc, offset, limit, with_summary)
        except FilterError as e:
            raise HTTPException(422, str(e)) from e
        except bigquery.QueryTooExpensive as e:
            hint = f" Filter on {bt.partition_field} first." if bt.partition_field else ""
            raise HTTPException(422, "This query would scan more than the configured limit." + hint) from e
        page = RowsPage(columns=bigquery.grid_columns(bt), rows=data, offset=offset, limit=limit, total=total)
    return RowsResponse(meta=meta(sources=t.sources), data=page)


@router.get("/{name}/lineage", response_model=LineageResponse)
def table_lineage(
    name: str = TABLE_NAME,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=MAX_LIMIT),
) -> LineageResponse:
    """The raw files that fed a table (`lake_processed`); SQL datasets have none of their own."""
    t = catalog.find(name)
    if t is None:
        raise HTTPException(404, f"no such table: {name}")
    if not pg.db_available():
        raise HTTPException(503, "database not configured")
    rows = pg.fetch_all(
        "SELECT raw_key, sha256, version, rows, processed_at, count(*) OVER () AS total"
        " FROM lake_processed WHERE dataset = %s ORDER BY raw_key LIMIT %s OFFSET %s",
        (name, limit, offset),
    )
    total = rows[0]["total"] if rows else 0
    if not rows and offset:
        total = pg.fetch_value("SELECT count(*) FROM lake_processed WHERE dataset = %s", (name,))
    page = LineagePage(
        items=[
            LineageRow(**{k: r[k] for k in ("raw_key", "sha256", "version", "rows", "processed_at")})
            for r in rows
        ],
        total=int(total or 0),
        offset=offset,
        limit=limit,
    )
    return LineageResponse(meta=meta(sources=t.sources), data=page)
