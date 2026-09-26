"""The insights page: the video's numbers as cards, each with its caveat (docs/data-contract.md §8)."""

from __future__ import annotations

import logging

import polars as pl
from fastapi import APIRouter, Depends

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.products.envelope import product_meta
from basecast_get_data.schemas.caveats import CODES
from basecast_get_data.schemas.common import PRODUCT_ERRORS, caveat
from basecast_get_data.schemas.insights import InsightCard, InsightsData, InsightsResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["insights"], dependencies=[Depends(require_token)])

INSIGHTS = "mart_insights"


@router.get("/insights", response_model=InsightsResponse, responses=PRODUCT_ERRORS)
def insights() -> InsightsResponse:
    """The video's numbers (lines graded A and B), in page order, each with its caveat and a link to the
    screen that shows the evidence."""
    df = store.frame(INSIGHTS).filter(pl.col("grade").is_in(["A", "B"])).sort("rank")
    cards = []
    for row in df.to_dicts():
        codes = row.pop("caveat_codes", None) or []
        known = [c for c in codes if c in CODES]
        if len(known) < len(codes):
            log.warning("insight %s has caveat codes the contract does not have: %s", row["id"], codes)
        cards.append(InsightCard(**row, caveats=[caveat(c) for c in known]))
    return InsightsResponse(
        # A card's `verified` is X6's re-derivation; the envelope's is about machine-read values.
        meta=product_meta(
            INSIGHTS,
            rows=df,
            verified=not any(c.code == "machine_read_unverified" for card in cards for c in card.caveats),
        ),
        data=InsightsData(cards=cards),
    )
