from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from alert_service.api.deps import current_user_id
from alert_service.api.schemas.notification import NotificationOut
from alert_service.repository.notifications import NotificationRepository


router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _repo(request: Request) -> NotificationRepository:
    return request.app.state.notification_repo


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[NotificationOut]:
    repo = _repo(request)
    rows = await repo.list_for_user(user_id, since=since, limit=limit)
    return [NotificationOut.model_validate(r, from_attributes=True) for r in rows]
