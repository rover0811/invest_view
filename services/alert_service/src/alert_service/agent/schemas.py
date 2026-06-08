from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AnalysisResponse(BaseModel):
    summary: str
    evidence: list[str]
    data_freshness: datetime
    coverage_note: str | None = None
