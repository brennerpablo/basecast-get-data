"""A fake Postgres for the marts: tables are lists of rows, typed as Postgres returns them."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from basecast_get_data.products import marts

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
NUMERIC = {"score", "meters", "priority", "capacity_mw"}
SAMPLES = Path(__file__).parent / "fixtures" / "marts"


def typed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """As Postgres types them: a column of ISO dates is a date column, one of ISO timestamps a timestamp
    column, and numeric is Decimal."""
    columns = {c for r in rows for c in r}

    def all_match(column: str, pattern: re.Pattern) -> bool:
        values = [r[column] for r in rows if r.get(column) is not None]
        return bool(values) and all(isinstance(v, str) and pattern.match(v) for v in values)

    dates = {c for c in columns if all_match(c, ISO_DATE)}
    stamps = {c for c in columns if all_match(c, ISO_TIMESTAMP)}

    def value(column: str, v: Any) -> Any:
        if v is None:
            return None
        if column in dates:
            return date.fromisoformat(v)
        if column in stamps:
            return datetime.fromisoformat(v)
        if column in NUMERIC and isinstance(v, float):
            return Decimal(str(v))
        return v

    return [{k: value(k, v) for k, v in r.items()} for r in rows]


def fixture_tables() -> dict[str, list[dict[str, Any]]]:
    out = {}
    for name in marts.MARTS:
        out[name] = typed(json.loads((marts.FIXTURES / f"{name}.json").read_text())["rows"])
    meta = json.loads((marts.FIXTURES / "mart_meta.json").read_text())["marts"]
    out["mart_meta"] = [
        {"mart": m, "key": k, "value": v} for m, values in meta.items() for k, v in values.items()
    ]
    return out


def samples() -> dict[str, list[dict[str, Any]]]:
    """The real marts trimmed by scripts/sample_marts.py (tests/fixtures/marts/<mart>.json)."""
    return {p.stem: typed(json.loads(p.read_text())["rows"]) for p in sorted(SAMPLES.glob("*.json"))}


class FakePostgres:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables
        self.full_reads: Counter[str] = Counter()

    def fetch_all(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        if "information_schema.columns" in query:
            rows = self.tables.get(params[1])
            columns = dict.fromkeys(c for r in rows for c in r) if rows is not None else {}
            return [{"column_name": c} for c in columns]
        name = re.search(r'FROM public\."(\w+)"', query).group(1)
        rows = self.tables[name]
        if query.startswith("SELECT count(*)"):
            out: dict[str, Any] = {"n": len(rows)}
            for column in ("built_at", "model_version", "as_of"):
                if f"max({column})" in query:
                    out[column] = max(
                        (str(r[column]) for r in rows if r.get(column) is not None), default=None
                    )
            return [out]
        self.full_reads[name] += 1
        return [dict(r) for r in rows]
