"""The catalog of caveat codes with their standard text (docs/data-contract.md, "Ressalvas")."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from basecast_get_data.auth import require_token
from basecast_get_data.schemas.caveats import CODES, CaveatsResponse
from basecast_get_data.schemas.common import caveat

router = APIRouter(tags=["caveats"], dependencies=[Depends(require_token)])


@router.get("/caveats", response_model=CaveatsResponse)
def caveats() -> CaveatsResponse:
    """Every code that `meta.caveats` can carry, with the label and text the app shows."""
    return CaveatsResponse(items=[caveat(code) for code in CODES])
