from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    notification_id: uuid.UUID
    alert_event_id: uuid.UUID
    symbol: str
    delivery_status: str
    delivered_at: datetime | None
    failure_reason: str | None
    created_at: datetime
