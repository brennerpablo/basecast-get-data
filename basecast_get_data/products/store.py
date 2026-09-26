"""Where the product endpoints read their marts: the fixtures or `public.mart_*`.

Every product endpoint reads mart-shaped frames through `frame()` and builds its response in Polars, so the
same code serves both. `DATA_MODE=marts` reads every mart from Postgres; with `DATA_MODE=fixtures` only the
groups in `MARTS_LIVE` do, and the rest come from `data/fixtures/` (small, invented copies of the marts made
by `scripts/make_contract_fixtures.py`), so every response built from a fixture says `simulated: true`.

A mart read from Postgres is loaded whole into memory on first use (or at startup) and checked in the
background every `MART_TTL_S`: it is reloaded only when its row count, `built_at`, `model_version` or `as_of`
change. A mart that is not there answers 503 `mart_not_built`; the API never falls back to a fixture.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from typing import Any

import polars as pl

from basecast_get_data.config import get_settings
from basecast_get_data.db import pg
from basecast_get_data.products import marts
from basecast_get_data.schemas.caveats import CODES

log = logging.getLogger(__name__)

FIXTURES = marts.FIXTURES
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A mart found missing is looked up again after this long, not on every request.
MISSING_RECHECK_S = 30


class MartNotBuilt(Exception):
    """The mart behind a resource does not exist yet: the API answers 503 `mart_not_built`."""

    def __init__(self, mart: str) -> None:
        super().__init__(mart)
        self.mart = mart


def live(name: str) -> bool:
    """Whether a mart (or a `mart_meta` key) is read from Postgres rather than from its fixture."""
    s = get_settings()
    group = marts.META_GROUPS[name] if name in marts.META_GROUPS else marts.group_of(name)
    return s.data_mode == "marts" or group in s.marts_live


def event(name: str, message: str, **context: Any) -> None:
    """One structured stdout line in the ops.log shape (contract §7); Cloud Logging parses the JSON."""
    line = {"service": "get-data", "level": "info", "event": name, "message": message, "context": context}
    log.info(json.dumps(line, default=str))


# --- fixtures ---------------------------------------------------------------------------------------------


@cache
def _fixture(name: str) -> dict[str, Any]:
    path = FIXTURES / f"{name}.json"
    if not path.is_file():
        raise MartNotBuilt(name)
    return json.loads(path.read_text())


def _dates(df: pl.DataFrame) -> pl.DataFrame:
    """JSON has no date type: turn the text columns that only hold ISO dates into dates, as in the marts."""
    casts = []
    for column, dtype in df.schema.items():
        if dtype != pl.String:
            continue
        values = df[column].drop_nulls()
        if len(values) and all(_ISO_DATE.match(v) for v in values):
            casts.append(pl.col(column).str.to_date())
    return df.with_columns(casts) if casts else df


@cache
def _fixture_frame(name: str) -> pl.DataFrame:
    return _dates(pl.DataFrame(_fixture(name)["rows"], infer_schema_length=None))


# --- marts in Postgres ------------------------------------------------------------------------------------


@dataclass
class Loaded:
    name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    version: tuple
    loaded_at: float = field(default_factory=time.monotonic)
    _frame: pl.DataFrame | None = None

    def frame(self) -> pl.DataFrame:
        if self._frame is None:
            if self.rows:
                self._frame = pl.DataFrame(self.rows, infer_schema_length=None)
            else:
                self._frame = pl.DataFrame({c: [] for c in self.columns})
        return self._frame


_loaded: dict[str, Loaded] = {}
_missing: dict[str, float] = {}
_lock = threading.Lock()


def _table(name: str) -> str:
    # Names come from the registry in marts.py, never from a request.
    return f'{get_settings().pg_schema}."{name}"'


def _columns(name: str) -> list[str] | None:
    """The table's columns, or None when it does not exist."""
    rows = pg.fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s"
        " ORDER BY ordinal_position",
        (get_settings().pg_schema, name),
    )
    return [r["column_name"] for r in rows] or None


def _version(name: str, columns: list[str]) -> tuple:
    parts = ["count(*) AS n"] + [
        f"max({c})::text AS {c}" for c in ("built_at", "model_version", "as_of") if c in columns
    ]
    row = pg.fetch_all(f"SELECT {', '.join(parts)} FROM {_table(name)}")[0]
    return tuple(row.values())


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    """numeric arrives as Decimal; the API works in floats."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


def _load(name: str) -> Loaded:
    started = time.perf_counter()
    columns = _columns(name)
    if columns is None:
        raise MartNotBuilt(name)
    if name != marts.META:
        missing = marts.required(name) - set(columns)
        if missing:
            log.error("%s lacks the columns the API reads: %s", name, ", ".join(sorted(missing)))
            raise MartNotBuilt(name)
    version = _version(name, columns)
    rows = [_plain(r) for r in pg.fetch_all(f"SELECT * FROM {_table(name)}")]
    loaded = Loaded(name=name, columns=columns, rows=rows, version=version)
    duration_ms = round((time.perf_counter() - started) * 1000)
    event(
        "mart.load",
        f"loaded {name}: {len(rows)} rows in {duration_ms} ms",
        mart=name,
        rows=len(rows),
        version=version,
        duration_ms=duration_ms,
    )
    return loaded


def _get(name: str) -> Loaded:
    with _lock:
        hit = _loaded.get(name)
        checked = _missing.get(name)
    if hit is not None:
        return hit
    if checked is not None and time.monotonic() - checked < MISSING_RECHECK_S:
        raise MartNotBuilt(name)
    try:
        loaded = _load(name)
    except MartNotBuilt:
        with _lock:
            _missing[name] = time.monotonic()
        raise
    with _lock:
        _loaded[name] = loaded
        _missing.pop(name, None)
    return loaded


def refresh() -> list[str]:
    """Reload the loaded marts whose build changed; drop the ones that disappeared. Returns what changed."""
    changed = []
    for name, current in list(_loaded.items()):
        try:
            columns = _columns(name)
            if columns is None:
                raise MartNotBuilt(name)
            if _version(name, columns) == current.version:
                continue
            fresh = _load(name)
            with _lock:
                _loaded[name] = fresh
        except MartNotBuilt:
            with _lock:
                _loaded.pop(name, None)
        except Exception:
            log.exception("could not refresh %s; the loaded build stays", name)
            continue
        changed.append(name)
    return changed


_started = False


def start() -> None:
    """Load the live marts in the background and keep checking them for new builds."""
    global _started
    live_marts = [m for m in marts.MARTS if live(m)]
    if _started or not live_marts:
        return
    _started = True
    ttl = get_settings().mart_ttl_s

    def run() -> None:
        for name in [*live_marts, marts.META]:
            try:
                _get(name)
            except MartNotBuilt:
                pass
            except Exception:
                log.exception("could not load %s at startup", name)
        while True:
            time.sleep(ttl)
            refresh()

    threading.Thread(target=run, name="marts", daemon=True).start()


def reset() -> None:
    """Forget every loaded mart and fixture (tests)."""
    with _lock:
        _loaded.clear()
        _missing.clear()
    _fixture.cache_clear()
    _fixture_frame.cache_clear()


def loaded() -> dict[str, int]:
    """Rows of each mart in memory, for /health."""
    with _lock:
        return {name: len(m.rows) for name, m in sorted(_loaded.items())}


# --- what the endpoints call ------------------------------------------------------------------------------


def frame(name: str) -> pl.DataFrame:
    return _get(name).frame() if live(name) else _fixture_frame(name)


def records(name: str) -> list[dict[str, Any]]:
    return _get(name).rows if live(name) else _fixture(name)["rows"]


def mart_caveats(table: str) -> list[str]:
    """The caveat codes a live mart declares in `mart_meta` (key `caveats`); unknown codes are dropped."""
    if table not in marts.MARTS or not live(table):
        return []
    try:
        rows = _get(marts.META).rows
    except MartNotBuilt:
        return []
    name = table.removeprefix("mart_")
    codes = next((r["value"] for r in rows if r["mart"] == name and r["key"] == "caveats"), None) or []
    unknown = [c for c in codes if c not in CODES]
    if unknown:
        log.warning("%s declares caveat codes the contract does not have: %s", table, ", ".join(unknown))
    return [c for c in codes if c in CODES]


def mart_meta(mart: str) -> dict[str, Any]:
    """Build-level values of a mart that are not rows: legend breaks, weights, the default variant, labels."""
    if not live(mart):
        return _fixture(marts.META)["marts"].get(mart, {})
    return {r["key"]: r["value"] for r in _get(marts.META).rows if r["mart"] == mart}
