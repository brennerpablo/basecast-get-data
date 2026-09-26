"""The insights page: the video's numbers as cards (contract §8).

Only the lines of basecast-airflow's docs/analysis/video-candidates.md graded A or B become cards. Every value
comes from a mart build (`mart_insights`), never from text written in the app, and every card carries the
caveat its line requires. A card about a ~438 GW queue always says which queue it is.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from basecast_get_data.schemas.caveats import Caveat
from basecast_get_data.schemas.common import Meta


class InsightFigure(BaseModel):
    label: str
    value: float
    unit: str


class InsightCard(BaseModel):
    id: str = Field(description="The line of video-candidates.md (A1, B3…)")
    grade: Literal["A", "B"] = Field(
        description="A: every number verified; B: the headline is a model result"
    )
    rank: int = Field(description="Order on the page, 1 first")
    title: str
    caption: str = Field(description="The line, built from the mart values")
    value: float = Field(description="The headline number")
    unit: str
    figures: list[InsightFigure] = Field(default_factory=list, description="The line's other numbers")
    caveat: str = Field(description="The caveat the line requires; always shown with the card")
    caveats: list[Caveat] = Field(default_factory=list)
    queue: Literal["generation", "large_load"] | None = Field(
        None, description="Which queue a queue number is about; never null on a queue card"
    )
    verified: bool = Field(description="Every number re-derived from the raw files (X6)")
    depends_on: list[str] = Field(default_factory=list, description="Pending review items (R3, R13…)")
    source_doc: str = Field(description="The analysis doc and section behind the numbers")
    link: str | None = Field(None, description="The app screen that shows the evidence")


class InsightsData(BaseModel):
    cards: list[InsightCard]


class InsightsResponse(BaseModel):
    meta: Meta
    data: InsightsData
