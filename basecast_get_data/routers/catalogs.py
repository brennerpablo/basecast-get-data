"""Static catalogs the app renders from: the caveat texts and the labels of the account codes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from basecast_get_data.auth import require_token
from basecast_get_data.products import store
from basecast_get_data.schemas.caveats import CODES, CaveatsResponse
from basecast_get_data.schemas.common import PRODUCT_ERRORS, caveat
from basecast_get_data.schemas.glossary import GlossaryItem, GlossaryResponse

router = APIRouter(tags=["catalogs"], dependencies=[Depends(require_token)])


@router.get("/caveats", response_model=CaveatsResponse)
def caveats() -> CaveatsResponse:
    """Every code that `meta.caveats` can carry, with the label and text the app shows."""
    return CaveatsResponse(items=[caveat(code) for code in CODES])


@router.get("/glossary", response_model=GlossaryResponse, responses=PRODUCT_ERRORS)
def glossary() -> GlossaryResponse:
    """The label and meaning of every trigger, flag and next-action code the account resources send."""
    items = [GlossaryItem(**i) for i in store.mart_meta("glossary").get("items", [])]
    order = {"next_action": 0, "trigger": 1, "flag": 2}
    return GlossaryResponse(items=sorted(items, key=lambda i: order[i.kind]))
