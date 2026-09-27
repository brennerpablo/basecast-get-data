"""Writes tests/fixtures/marts/: the real marts in Postgres, trimmed, for tests/test_real_marts.py.

    uv run python scripts/sample_marts.py [mart ...]

Reads `public.mart_*` as basecast_reader (the proxy in .env) and keeps every row of a small mart; a mart past
MAX_ROWS needs a trimming rule in TRIM first, so a sample never grows by accident. The `mart_meta` rows of the
sampled marts go to mart_meta.json. The account marts hold only the scored universe (the validation lock),
so a sample of them holds no held-out account either.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from basecast_get_data.db import pg
from basecast_get_data.products import marts

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "marts"
MAX_ROWS = 400
# Marts kept whole past MAX_ROWS because their golden numbers are statewide sums.
KEEP_WHOLE = {
    "mart_queue_adjusted_county": 800,
    "mart_large_load_in_service": 900,
    "mart_four_cp_dispatch_curve": 500,
}
# mart → SQL WHERE clause that trims it (fixed values, never user input).
# The three accounts of X9's worked diagnoses: #1 (a muni), the median co-op and a short-form muni.
X9_ACCOUNTS = "account_id IN ('30123', '30120', '30012')"
TRIM: dict[str, str] = {
    "mart_account_detail": X9_ACCOUNTS,
    "mart_account_events": X9_ACCOUNTS,
    "mart_account_counties": X9_ACCOUNTS,
    "mart_load_normalized_monthly": "weather_zone IN ('ERCOT', 'WEST') AND month >= '2019-01-01'",
    "mart_queue_stage_curves": "stratum IN ('all', 'storage') AND month % 6 = 0",
    # The five counties with the most raw queue.
    "mart_queue_project_scores": "county_fips IN (SELECT county_fips FROM public.mart_queue_adjusted_county"
    " WHERE stratum = 'all' ORDER BY raw_mw DESC LIMIT 5)",
}


def _json(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value))


def sample(name: str) -> int | None:
    exists = pg.fetch_all("SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{name}",))[0]["present"]
    if not exists:
        return None
    where = f" WHERE {TRIM[name]}" if name in TRIM else ""
    rows = pg.fetch_all(f'SELECT * FROM public."{name}"{where}')
    if len(rows) > KEEP_WHOLE.get(name, MAX_ROWS):
        raise SystemExit(f"{name}: {len(rows)} rows; add a trimming rule to TRIM first")
    doc = {"note": f"public.{name}, trimmed by scripts/sample_marts.py", "rows": rows}
    (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1, default=_json, ensure_ascii=False) + "\n")
    return len(rows)


def main(names: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sampled = []
    for name in names or [m for m in marts.MARTS if m.startswith("mart_")]:
        n = sample(name)
        if n is not None:
            sampled.append(name)
            print(f"{name}: {n} rows")
    keys = [m.removeprefix("mart_") for m in sampled] + ["glossary"]
    meta = pg.fetch_all("SELECT mart, key, value FROM public.mart_meta WHERE mart = ANY(%s)", (keys,))
    doc = {"note": "public.mart_meta rows of the sampled marts", "rows": meta}
    (OUT / "mart_meta.json").write_text(json.dumps(doc, indent=1, default=_json, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
