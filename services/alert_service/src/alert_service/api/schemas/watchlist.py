from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


_SYMBOL_RE = re.compile(r"^[A-Z0-9]{6}$")


class WatchlistItemOut(BaseModel):
    symbol: str
    notifications_enabled: bool
    created_at: datetime


class WatchlistAddIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=6)

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, v: str) -> str:
        if not _SYMBOL_RE.match(v):
            raise ValueError("symbol must be 6 uppercase alphanumeric characters")
        return v


class WatchlistPatchIn(BaseModel):
    notifications_enabled: bool
