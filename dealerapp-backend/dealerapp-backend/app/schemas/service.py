"""Service-request thread schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import SenderType, ServiceRequestStatus
from app.schemas.common import ORMModel


class ServiceMessageOut(ORMModel):
    id: uuid.UUID
    service_request_id: uuid.UUID
    sender_type: SenderType
    sender_id: uuid.UUID | None = None
    sender_name: str | None = None
    message: str
    created_at: datetime


class ServiceMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ServiceRequestCreate(BaseModel):
    vehicle_id: uuid.UUID
    type: str | None = Field(default=None, max_length=80)
    description: str | None = None
    preferred_date: date | None = None


class ServiceRequestOut(ORMModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    customer_id: uuid.UUID
    dealer_id: uuid.UUID | None = None
    type: str | None = None
    description: str | None = None
    status: ServiceRequestStatus
    preferred_date: date | None = None
    created_at: datetime
    message_count: int = 0
    last_message_at: datetime | None = None


class ServiceRequestDetailOut(ServiceRequestOut):
    messages: list[ServiceMessageOut] = Field(default_factory=list)


class ServiceRequestUpdate(BaseModel):
    status: ServiceRequestStatus
