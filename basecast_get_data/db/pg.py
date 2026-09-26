"""Postgres (Cloud SQL `basecast`), read as `basecast_reader`, which only has SELECT on `public`.

On Cloud Run the instance is attached with `--add-cloudsql-instances` and reached through its unix socket
(`/cloudsql/<connection name>`); locally through the Cloud SQL proxy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from basecast_get_data.config import get_settings


class DatabaseUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def _pool() -> ConnectionPool:
    s = get_settings()
    if not s.db_configured:
        raise DatabaseUnavailable("PG_PASSWORD is not set")
    conninfo = psycopg.conninfo.make_conninfo(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_db,
        user=s.pg_user,
        password=s.pg_password,
        application_name="basecast-get-data",
        connect_timeout=10,
        # A plain sequential scan must return rows in the same order on every call, or offset
        # pagination skips and repeats rows; synchronized scans start wherever another scan is.
        options="-c statement_timeout=15000 -c synchronize_seqscans=off -c default_transaction_read_only=on",
    )
    # The instance is shared with the pipelines: this API keeps to a handful of connections.
    return ConnectionPool(conninfo, min_size=0, max_size=3, open=True, kwargs={"autocommit": True})


def db_available() -> bool:
    return get_settings().db_configured


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    try:
        pool = _pool()
    except DatabaseUnavailable:
        raise
    with pool.connection(timeout=20) as conn:
        yield conn


def fetch_all(query: Any, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def fetch_rows(query: Any, params: Any = None) -> tuple[list[str], list[tuple]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        names = [d.name for d in cur.description or []]
        return names, list(cur.fetchall())


def fetch_value(query: Any, params: Any = None, *, timeout_ms: int | None = None) -> Any:
    with connection() as conn, conn.cursor() as cur:
        if timeout_ms:
            cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
        try:
            cur.execute(query, params)
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            if timeout_ms:
                cur.execute("SET statement_timeout = 15000")
