"""The `meta` of a product response, taken from the mart rows behind it (data-contract.md, "Envelope")."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import polars as pl

from basecast_get_data.products import store
from basecast_get_data.schemas.common import Meta, meta


def _latest(df: pl.DataFrame) -> str | None:
    for column in ("as_of", "as_of_month"):
        if column in df.columns and df[column].drop_nulls().len():
            value = df[column].drop_nulls().max()
            return value.isoformat() if isinstance(value, date) else str(value)
    return None


def _model_version(df: pl.DataFrame) -> str | None:
    if "model_version" not in df.columns:
        return None
    versions = df["model_version"].drop_nulls().unique().sort().to_list()
    return ", ".join(versions) or None


def all_verified(*frames: pl.DataFrame) -> bool:
    return all(bool(df["verified"].all()) for df in frames if "verified" in df.columns and df.height)


def product_meta(
    *marts: str,
    rows: pl.DataFrame | None = None,
    verified: bool = True,
    caveats: Iterable[str] = (),
    drop: Iterable[str] = (),
) -> Meta:
    """`marts` are the ones the response read; `rows` is its main frame, whose `as_of` and `model_version` go
    to the envelope. A response that read any fixture says `simulated` and carries the `fixture` caveat."""
    dropped = set(drop)
    codes = [c for c in [*caveats, *(c for m in marts for c in store.mart_caveats(m))] if c not in dropped]
    if not verified:
        codes.insert(0, "machine_read_unverified")
    fixture = any(not store.live(m) for m in marts)
    if fixture:
        codes.append("fixture")
    simulated = fixture or bool(rows is not None and "simulated" in rows.columns and rows["simulated"].any())
    return meta(
        sources=list(marts),
        data_as_of=_latest(rows) if rows is not None else None,
        model_version=_model_version(rows) if rows is not None else None,
        simulated=simulated,
        verified=verified,
        caveats=codes,
    )
