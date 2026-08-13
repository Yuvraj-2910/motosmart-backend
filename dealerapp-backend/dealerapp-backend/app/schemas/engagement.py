"""Notification schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.models.enums import NotificationType, RecipientType
from app.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: uuid.UUID
    recipient_type: RecipientType
    recipient_id: uuid.UUID
    type: NotificationType
    title: str
    body: str | None = None
    payload_json: dict[str, Any] | None = None
    is_read: bool
    created_at: datetime


class NotificationListOut(ORMModel):
    items: list[NotificationOut]
    unread_count: int
