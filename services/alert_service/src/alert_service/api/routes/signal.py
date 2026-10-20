"""Public read-only AI signal API (doc 22 15.3). Logic lives in ``signal_query``."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.api.schemas.signal import SignalOut
from alert_service.signal_query import get_current_signal, get_signal_history


router = APIRouter(prefix="/api", tags=["signal"])

Symbol = Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")]


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


@router.get("/stocks/{symbol}/signal", response_model=SignalOut)
async def read_current_signal(request: Request, symbol: Symbol) -> SignalOut:
    signal = await get_current_signal(_session_factory(request), symbol)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="signal not found")
    return signal


@router.get("/stocks/{symbol}/signal/history", response_model=list[SignalOut])
async def read_signal_history(
    request: Request,
    symbol: Symbol,
    day: Annotated[date, Query(alias="date")],
) -> list[SignalOut]:
    return await get_signal_history(_session_factory(request), symbol, day)
