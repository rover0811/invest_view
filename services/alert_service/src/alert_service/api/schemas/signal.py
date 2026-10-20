from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SignalCauseOut(BaseModel):
    rank: int
    explanation: str
    source_count: int
    source_document_ids: list[UUID]


class SignalOut(BaseModel):
    symbol: str
    symbol_name: str
    move_id: UUID
    move_version: int
    version: int
    move_window_end: datetime
    status: str
    headline_label: str | None
    headline: str | None
    generation_method: str | None
    updated_at: datetime
    causes: list[SignalCauseOut]
