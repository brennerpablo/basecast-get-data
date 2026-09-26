"""Processed tables in BigQuery: catalog, schema and blocks of rows.

A block with no filter and no sort is read with `tabledata.list` (`list_rows(start_index=...)`), which
runs no query. Filters and sorts need a query; every query carries `maximum_bytes_billed`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from google.api_core import exceptions as gexc
from google.cloud import bigquery

from basecast_get_data.config import get_settings
from basecast_get_data.db import bq
from basecast_get_data.lake.readers import to_cell
from basecast_get_data.schemas.common import Cell, ColumnType, GridColumn
from basecast_get_data.tables.filters import Filter, like_pattern

log = logging.getLogger(__name__)

NUMBER = {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
DATE = {"DATE", "DATETIME", "TIMESTAMP", "TIME"}
# Legacy field type → standard SQL type, for SAFE_CAST.
SQL_TYPE = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL", "RECORD": "STRUCT"}


class QueryTooExpensive(Exception):
    pass


@dataclass
class BqTable:
    name: str
    rows: int
    bytes: int
    schema: list[bigquery.SchemaField]
    partition_field: str | None
    cluster_fields: list[str]


def _type(field: bigquery.SchemaField) -> ColumnType:
    if field.mode == "REPEATED" or field.field_type in ("RECORD", "STRUCT", "JSON"):
        return "json"
    if field.field_type in NUMBER:
        return "number"
    if field.field_type in DATE:
        return "date"
    if field.field_type in ("BOOL", "BOOLEAN"):
        return "boolean"
    if field.field_type == "GEOGRAPHY":
        return "geometry"
    return "text"


_cache: tuple[float, dict[str, BqTable]] | None = None


def tables() -> dict[str, BqTable]:
    """Tables in the dataset, with metadata from the tables API (no query)."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < 300:
        return _cache[1]
    s = get_settings()
    out: dict[str, BqTable] = {}
    try:
        client = bq.client()
        for item in client.list_tables(f"{s.gcp_project}.{s.bq_dataset}"):
            t = client.get_table(item.reference)
            out[t.table_id] = BqTable(
                name=t.table_id,
                rows=int(t.num_rows or 0),
                bytes=int(t.num_bytes or 0),
                schema=list(t.schema),
                partition_field=t.time_partitioning.field if t.time_partitioning else None,
                cluster_fields=list(t.clustering_fields or []),
            )
    except Exception:
        log.exception("BigQuery catalog unavailable")
        return _cache[1] if _cache else {}
    _cache = (time.monotonic(), out)
    return out


def grid_columns(table: BqTable) -> list[GridColumn]:
    return [GridColumn(name=f.name, type=_type(f), source_type=f.field_type) for f in table.schema]


def _where(filters: list[Filter], fields: dict[str, bigquery.SchemaField]):
    parts, params = [], []
    for i, f in enumerate(filters):
        field = fields[f.column]
        c = f"`{f.column}`"
        p = f"p{i}"
        sql_type = SQL_TYPE.get(field.field_type, field.field_type)
        as_text = f"CAST({c} AS STRING)"
        typed_param = (
            f"SAFE_CAST(@{p} AS {sql_type})" if sql_type not in ("STRING", "STRUCT", "JSON") else f"@{p}"
        )
        if f.op in ("eq", "ne", "gte", "lte", "gt", "lt"):
            symbol = {"eq": "=", "ne": "!=", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[f.op]
            parts.append(f"{c} {symbol} {typed_param}")
            params.append(bigquery.ScalarQueryParameter(p, "STRING", f.value))
        elif f.op in ("contains", "starts"):
            parts.append(f"LOWER({as_text}) LIKE LOWER(@{p})")
            params.append(
                bigquery.ScalarQueryParameter(
                    p, "STRING", like_pattern(f.value, prefix_only=f.op == "starts")
                )
            )
        elif f.op == "in":
            parts.append(f"{as_text} IN UNNEST(@{p})")
            params.append(bigquery.ArrayQueryParameter(p, "STRING", list(f.values)))
        elif f.op == "between":
            lo = f"SAFE_CAST(@{p}a AS {sql_type})" if sql_type != "STRING" else f"@{p}a"
            hi = f"SAFE_CAST(@{p}b AS {sql_type})" if sql_type != "STRING" else f"@{p}b"
            parts.append(f"{c} BETWEEN {lo} AND {hi}")
            params.append(bigquery.ScalarQueryParameter(f"{p}a", "STRING", f.values[0]))
            params.append(bigquery.ScalarQueryParameter(f"{p}b", "STRING", f.values[1]))
        elif f.op == "null":
            parts.append(f"{c} IS NULL")
        elif f.op == "notnull":
            parts.append(f"{c} IS NOT NULL")
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def _run(query: str, params: list) -> bigquery.table.RowIterator:
    config = bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=get_settings().bq_max_bytes_billed, use_query_cache=True
    )
    try:
        return bq.client().query(query, job_config=config).result()
    except gexc.BadRequest as e:
        if "bytesBilledLimitExceeded" in str(e) or "billed" in str(e).lower():
            raise QueryTooExpensive(str(e)) from e
        raise


def rows(
    table: BqTable,
    filters: list[Filter],
    sort: str | None,
    desc: bool,
    offset: int,
    limit: int,
    with_total: bool,
) -> tuple[list[list[Cell]], int | None]:
    client = bq.client()
    names = [f.name for f in table.schema]
    if not filters and not sort:
        it = client.list_rows(
            bq.table_ref(table.name), selected_fields=table.schema, start_index=offset, max_results=limit
        )
        data = [[to_cell(r[n]) for n in names] for r in it]
        return data, (table.rows if with_total else None)
    where, params = _where(filters, {f.name: f for f in table.schema})
    order = f" ORDER BY `{sort}` {'DESC' if desc else 'ASC'} NULLS LAST" if sort else ""
    query = (
        f"SELECT * FROM `{bq.table_ref(table.name)}`{where}{order} LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    data = [[to_cell(r[n]) for n in names] for r in _run(query, params)]
    total = None
    if with_total:
        if filters:
            result = _run(f"SELECT COUNT(*) AS n FROM `{bq.table_ref(table.name)}`{where}", params)
            total = int(next(iter(result))["n"])
        else:
            total = table.rows
    return data, total
