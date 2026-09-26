"""Processed tables in Postgres: catalog, columns and blocks of rows.

Identifiers never come from the request as text: the table must exist in `pg_class` and every sort or
filter column in `pg_attribute`, and they are quoted with `sql.Identifier`. Values always go as
parameters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from psycopg import errors, sql

from basecast_get_data.config import get_settings
from basecast_get_data.db import pg
from basecast_get_data.lake.readers import to_cell
from basecast_get_data.schemas.common import Cell, ColumnType, GridColumn
from basecast_get_data.tables.filters import Filter, like_pattern

# Hidden from the catalog: PostGIS's own tables and views.
POSTGIS = {"spatial_ref_sys", "geography_columns", "geometry_columns", "raster_columns", "raster_overviews"}
# Shown, but as pipeline bookkeeping rather than datasets.
SYSTEM = {"etl_run", "lake_processed", "dataset_registry"}

NUMBER = {"int2", "int4", "int8", "float4", "float8", "numeric", "oid", "money"}
DATE = {"date", "timestamp", "timestamptz", "time", "timetz", "interval"}


@dataclass
class PgTable:
    name: str
    relkind: str
    rows_estimate: int | None
    bytes: int


@dataclass
class PgColumn:
    name: str
    type: ColumnType
    source_type: str
    nullable: bool
    position: int


def _type(typname: str) -> ColumnType:
    if typname in NUMBER:
        return "number"
    if typname in DATE:
        return "date"
    if typname == "bool":
        return "boolean"
    if typname in ("json", "jsonb") or typname.startswith("_"):
        return "json"
    if typname in ("geometry", "geography"):
        return "geometry"
    return "text"


_tables_cache: tuple[float, dict[str, PgTable]] | None = None


def tables() -> dict[str, PgTable]:
    global _tables_cache
    if _tables_cache and time.monotonic() - _tables_cache[0] < 30:
        return _tables_cache[1]
    rows = pg.fetch_all(
        """
        SELECT c.relname AS name, c.relkind AS relkind, c.reltuples::bigint AS reltuples,
               pg_total_relation_size(c.oid) AS bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'm', 'v') AND NOT c.relispartition
        """,
        (get_settings().pg_schema,),
    )
    out = {
        r["name"]: PgTable(
            name=r["name"],
            relkind=r["relkind"],
            # -1 means the table was never analyzed.
            rows_estimate=r["reltuples"] if r["reltuples"] is not None and r["reltuples"] >= 0 else None,
            bytes=int(r["bytes"] or 0),
        )
        for r in rows
        if r["name"] not in POSTGIS
    }
    _tables_cache = (time.monotonic(), out)
    return out


def columns(table: str) -> list[PgColumn]:
    rows = pg.fetch_all(
        """
        SELECT a.attname AS name, t.typname AS typname, format_type(a.atttypid, a.atttypmod) AS source_type,
               NOT a.attnotnull AS nullable, a.attnum AS position
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (get_settings().pg_schema, table),
    )
    return [
        PgColumn(r["name"], _type(r["typname"]), r["source_type"], r["nullable"], r["position"]) for r in rows
    ]


def _ident(table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(get_settings().pg_schema), sql.Identifier(table))


def _select_expr(col: PgColumn) -> sql.Composable:
    c = sql.Identifier(col.name)
    if col.type == "geometry":
        # Geometries are summarized: the grid shows the type and size, the map reads them elsewhere.
        return sql.SQL(
            "CASE WHEN {c} IS NULL THEN NULL ELSE GeometryType({c}::geometry) || ' · '"
            " || ST_NPoints({c}::geometry) || ' points' END AS {c}"
        ).format(c=c)
    if col.source_type == "bytea":
        return sql.SQL("'<' || length({c}) || ' bytes>' AS {c}").format(c=c)
    if col.type == "json":
        return sql.SQL("{c}::text AS {c}").format(c=c)
    return c


def where_clause(filters: list[Filter], cols: dict[str, PgColumn]) -> tuple[sql.Composable, list]:
    parts: list[sql.Composable] = []
    params: list = []
    for f in filters:
        col = cols[f.column]
        c = sql.Identifier(f.column)
        as_text = sql.SQL("{}::text").format(c)
        typed = as_text if col.type in ("json", "geometry") else c
        if f.op == "eq":
            parts.append(sql.SQL("{} = %s").format(typed))
            params.append(f.value)
        elif f.op == "ne":
            parts.append(sql.SQL("{} IS DISTINCT FROM %s").format(typed))
            params.append(f.value)
        elif f.op in ("contains", "starts"):
            parts.append(sql.SQL("{} ILIKE %s").format(as_text))
            params.append(like_pattern(f.value, prefix_only=f.op == "starts"))
        elif f.op == "in":
            parts.append(sql.SQL("{} = ANY(%s)").format(as_text))
            params.append(list(f.values))
        elif f.op in ("gte", "lte", "gt", "lt"):
            symbol = {"gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[f.op]
            parts.append(sql.SQL("{} " + symbol + " %s").format(typed))
            params.append(f.value)
        elif f.op == "between":
            parts.append(sql.SQL("{} BETWEEN %s AND %s").format(typed))
            params.extend(f.values)
        elif f.op == "null":
            parts.append(sql.SQL("{} IS NULL").format(c))
        elif f.op == "notnull":
            parts.append(sql.SQL("{} IS NOT NULL").format(c))
    if not parts:
        return sql.SQL(""), params
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


def order_clause(
    sort: str | None, desc: bool, key_columns: list[str], cols: dict[str, PgColumn]
) -> sql.Composable:
    """The requested sort, then the table key as a tiebreak. Without a sort, the key (an index scan for
    by_key tables); without a key either, the physical order, which synchronize_seqscans=off keeps
    stable between calls."""
    keys = [k for k in key_columns if k in cols]
    items: list[sql.Composable] = []
    if sort:
        items.append(
            sql.SQL("{} {} NULLS LAST").format(sql.Identifier(sort), sql.SQL("DESC" if desc else "ASC"))
        )
    items.extend(sql.Identifier(k) for k in keys if k != sort)
    if not items:
        return sql.SQL("")
    return sql.SQL(" ORDER BY ") + sql.SQL(", ").join(items)


def rows_query(
    table: str,
    cols: list[PgColumn],
    filters: list[Filter],
    sort: str | None,
    desc: bool,
    key_columns: list[str],
) -> tuple[sql.Composed, list]:
    by_name = {c.name: c for c in cols}
    where, params = where_clause(filters, by_name)
    query = sql.SQL("SELECT {cols} FROM {table}{where}{order} LIMIT %s OFFSET %s").format(
        cols=sql.SQL(", ").join(_select_expr(c) for c in cols),
        table=_ident(table),
        where=where,
        order=order_clause(sort, desc, key_columns, by_name),
    )
    return query, params


def count(table: str, filters: list[Filter], cols: list[PgColumn], estimate: int | None) -> tuple[int, bool]:
    """Exact count, bounded in time; past the bound, the planner's estimate (flagged as such)."""
    where, params = where_clause(filters, {c.name: c for c in cols})
    query = sql.SQL("SELECT count(*) FROM {table}{where}").format(table=_ident(table), where=where)
    try:
        return int(pg.fetch_value(query, params, timeout_ms=6000)), False
    except errors.QueryCanceled:
        return int(estimate or 0), True


def rows(
    table: str,
    cols: list[PgColumn],
    filters: list[Filter],
    sort: str | None,
    desc: bool,
    key_columns: list[str],
    offset: int,
    limit: int,
) -> list[list[Cell]]:
    query, params = rows_query(table, cols, filters, sort, desc, key_columns)
    _, data = pg.fetch_rows(query, [*params, limit, offset])
    return [[to_cell(v) for v in r] for r in data]


def grid_columns(cols: list[PgColumn]) -> list[GridColumn]:
    return [GridColumn(name=c.name, type=c.type, source_type=c.source_type) for c in cols]
