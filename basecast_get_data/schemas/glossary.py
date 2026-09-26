"""Labels for the codes the account resources send: triggers, flags and next actions (contract §5).

Like the caveat catalog, the app shows these texts and never writes its own. The entries come from
basecast-airflow's trigger config through `mart_meta` (mart `glossary`), so a trigger that changes strength
changes here too.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GlossaryKind = Literal["trigger", "flag", "next_action"]


class GlossaryItem(BaseModel):
    kind: GlossaryKind
    code: str
    label: str = Field(description="Short, for a chip or a column")
    text: str = Field(description="What the code means, for a tooltip")
    strength: Literal["strong", "context"] | None = Field(None, description="Triggers only")


class GlossaryResponse(BaseModel):
    """Not an envelope: the labels of every trigger, flag and next-action code."""

    items: list[GlossaryItem]
