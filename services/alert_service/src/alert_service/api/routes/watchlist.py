from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from alert_service.api.deps import current_user_id
from alert_service.api.schemas.watchlist import (
    WatchlistAddIn,
    WatchlistItemOut,
    WatchlistPatchIn,
)
from alert_service.repository.watchlist import WatchlistDuplicateError, WatchlistRepository


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _repo(request: Request) -> WatchlistRepository:
    return request.app.state.watchlist_repo


@router.get("", response_model=list[WatchlistItemOut])
async def list_watchlist(
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> list[WatchlistItemOut]:
    repo = _repo(request)
    items = await repo.list_for_user(user_id)
    return [WatchlistItemOut.model_validate(i, from_attributes=True) for i in items]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WatchlistItemOut)
async def add_watchlist(
    payload: WatchlistAddIn,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> WatchlistItemOut:
    repo = _repo(request)
    try:
        item = await repo.add(user_id, payload.symbol)
    except WatchlistDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WatchlistItemOut.model_validate(item, from_attributes=True)


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(
    symbol: str,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> None:
    repo = _repo(request)
    removed = await repo.remove(user_id, symbol)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="symbol not in watchlist")


@router.patch("/{symbol}", response_model=WatchlistItemOut | None)
async def patch_watchlist(
    symbol: str,
    payload: WatchlistPatchIn,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
):
    repo = _repo(request)
    updated = await repo.set_notifications_enabled(user_id, symbol, payload.notifications_enabled)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="symbol not in watchlist")
    items = [i for i in await repo.list_for_user(user_id) if i.symbol == symbol]
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="symbol disappeared")
    return WatchlistItemOut.model_validate(items[0], from_attributes=True)
