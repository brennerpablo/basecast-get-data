"""Display metadata of the raw sources (data/sources.yaml) and their schedule in words."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG = Path(__file__).resolve().parent.parent / "data" / "sources.yaml"
DAYS = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"]


@dataclass(frozen=True)
class SourceMeta:
    source_id: str
    name: str
    group: str
    publisher: str | None = None
    catalog_id: str | None = None
    description: str | None = None
    upstream_url: str | None = None
    cron: str | None = None

    @property
    def schedule(self) -> str:
        return describe_cron(self.cron)


@lru_cache(maxsize=1)
def catalog() -> dict[str, SourceMeta]:
    doc = yaml.safe_load(CATALOG.read_text())
    return {sid: SourceMeta(source_id=sid, **fields) for sid, fields in (doc.get("sources") or {}).items()}


def groups() -> list[str]:
    return list(yaml.safe_load(CATALOG.read_text()).get("groups") or [])


def meta_for(source_id: str) -> SourceMeta:
    return catalog().get(source_id) or SourceMeta(source_id=source_id, name=source_id, group="Other")


def describe_cron(cron: str | None) -> str:
    """The DAG crons as people say them: "Mondays 07:00 CT", "Monthly, day 10 07:00 CT"."""
    if not cron:
        return "Manual"
    minute, hour, dom, month, dow = cron.split()
    at = f"{int(hour):02d}:{int(minute):02d} CT" if hour.isdigit() and minute.isdigit() else cron
    if dom == "*" and month == "*" and dow == "*":
        return f"Daily {at}"
    if dom == "*" and month == "*" and dow.isdigit():
        return f"{DAYS[int(dow) % 7]} {at}"
    if dom.isdigit() and month == "*" and dow == "*":
        return f"Monthly, day {dom} {at}"
    if dom.isdigit() and "," in month and dow == "*":
        return f"Quarterly, day {dom} {at}"
    return f"cron {cron} (CT)"
