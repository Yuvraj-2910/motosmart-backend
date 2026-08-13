"""Service-request thread schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import (
    SenderType,
    ServiceRequestStatus,
    TicketCategory,
    TicketPriority,
)
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
    # Set when the request was raised from the OBD dashboard after a fault: the
    # codes and readings are appended to the thread so the desk sees the evidence,
    # and they feed the AI triage.
    obd_context: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional diagnostic context captured from the bike.",
    )


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

    # AI triage of the customer's description, assigned on creation. Null when
    # classification could not run - the ticket is still valid.
    ai_category: TicketCategory | None = None
    ai_priority: TicketPriority | None = None
    ai_summary: str | None = None

    # Who closed it, when. Null until somebody resolves it.
    resolved_by_employee_id: uuid.UUID | None = None
    resolved_at: datetime | None = None
    resolved_by_name: str | None = None

    # Denormalised identity of who raised it and on what. The dealer queue needs
    # a name and a vehicle to be usable, and joining it here saves the app an
    # N+1 lookup it has no way to do (it cannot read the customers table).
    customer_name: str | None = None
    customer_phone: str | None = None
    vehicle_label: str | None = None
    vehicle_registration: str | None = None


class ServiceRequestDetailOut(ServiceRequestOut):
    messages: list[ServiceMessageOut] = Field(default_factory=list)


class ServiceRequestUpdate(BaseModel):
    status: ServiceRequestStatus
