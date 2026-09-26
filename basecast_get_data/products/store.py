"""Where the product endpoints read their marts.

Every product endpoint reads mart-shaped frames through `frame()` and builds its response in Polars, so the
same code serves the fixtures now and the marts later. The fixtures in `data/fixtures/` are small, invented
copies of the marts `basecast-airflow` writes to `public.mart_*` (same names and columns), made by
`scripts/make_contract_fixtures.py`; every response built from them says `simulated: true`.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import polars as pl

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MartNotBuilt(Exception):
    """The mart behind a resource does not exist yet: the API answers 503 `mart_not_built`."""

    def __init__(self, mart: str) -> None:
        super().__init__(mart)
        self.mart = mart


def simulated() -> bool:
    """Whether the frames are fixtures (always, until the marts are read)."""
    return True


@cache
def _document(name: str) -> dict[str, Any]:
    path = FIXTURES / f"{name}.json"
    if not path.is_file():
        raise MartNotBuilt(name)
    return json.loads(path.read_text())


def records(name: str) -> list[dict[str, Any]]:
    return _document(name)["rows"]


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
def frame(name: str) -> pl.DataFrame:
    return _dates(pl.DataFrame(records(name), infer_schema_length=None))


def mart_meta(mart: str) -> dict[str, Any]:
    """Build-level values of a mart that are not rows: legend breaks, weights, the default variant, labels."""
    return _document("mart_meta")["marts"].get(mart, {})
