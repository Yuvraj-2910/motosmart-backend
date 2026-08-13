"""Lead, follow-up, dashboard, and AI schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AiIntent, LeadSource, LeadStatus
from app.schemas.catalog import BikeModelOut
from app.schemas.common import ORMModel, Warning_


class LeadFollowupOut(ORMModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    employee_id: uuid.UUID | None = None
    next_action: str
    scheduled_date: date
    completed: bool
    outcome_note: str | None = None
    created_at: datetime


class LeadFollowupCreate(BaseModel):
    next_action: str = Field(min_length=1, max_length=400)
    scheduled_date: date
    outcome_note: str | None = None


class LeadFollowupUpdate(BaseModel):
    next_action: str | None = Field(default=None, min_length=1, max_length=400)
    scheduled_date: date | None = None
    completed: bool | None = None
    outcome_note: str | None = None


class LeadBase(BaseModel):
    customer_name: str = Field(min_length=1, max_length=160)
    mobile: str = Field(min_length=6, max_length=20)
    interested_model_id: uuid.UUID | None = None
    current_bike: str | None = Field(default=None, max_length=160)
    tentative_purchase_date: date | None = None
    notes: str | None = None

    @field_validator("mobile")
    @classmethod
    def _strip_mobile(cls, v: str) -> str:
        cleaned = "".join(ch for ch in v if ch.isdigit() or ch == "+")
        if len(cleaned) < 6:
            raise ValueError("mobile must contain at least 6 digits")
        return cleaned


class LeadCreate(LeadBase):
    source: LeadSource = LeadSource.WALK_IN
    # Optional: defaults to the caller's dealer. Present so a lead can be filed
    # for another branch when needed.
    dealer_id: uuid.UUID | None = None
    classify: bool = Field(
        default=False,
        description="Run AI intent classification inline and return the badge with the lead.",
    )


class LeadUpdate(BaseModel):
    customer_name: str | None = Field(default=None, min_length=1, max_length=160)
    mobile: str | None = Field(default=None, min_length=6, max_length=20)
    interested_model_id: uuid.UUID | None = None
    current_bike: str | None = None
    tentative_purchase_date: date | None = None
    status: LeadStatus | None = None
    notes: str | None = None
    assigned_employee_id: uuid.UUID | None = None


class LeadOut(ORMModel):
    id: uuid.UUID
    dealer_id: uuid.UUID
    assigned_employee_id: uuid.UUID | None = None
    customer_name: str
    mobile: str
    source: LeadSource
    interested_model_id: uuid.UUID | None = None
    current_bike: str | None = None
    tentative_purchase_date: date | None = None
    status: LeadStatus
    ai_intent: AiIntent | None = None
    notes: str | None = None
    converted_customer_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    # Enriched for list/detail rendering so the app avoids an N+1 fetch.
    interested_model: BikeModelOut | None = None
    assigned_employee_name: str | None = None
    next_followup_date: date | None = None
    is_followup_overdue: bool = False


class LeadDetailOut(LeadOut):
    followups: list[LeadFollowupOut] = Field(default_factory=list)


class LeadCreateResponse(BaseModel):
    """Creation wraps the lead so duplicate-mobile advisories can ride along."""

    lead: LeadOut
    warnings: list[Warning_] = Field(default_factory=list)


class LeadConvertRequest(BaseModel):
    name: str | None = Field(
        default=None, description="Defaults to the lead's customer_name."
    )
    phone: str | None = Field(default=None, description="Defaults to the lead's mobile.")
    email: str | None = None
    invite: bool = Field(
        default=False, description="Provision a Cognito CUSTOMER login and send an OTP invite."
    )


class LeadConvertResponse(BaseModel):
    lead: LeadOut
    customer_id: uuid.UUID
    invited: bool = False


class DashboardSummaryOut(BaseModel):
    dealer_id: uuid.UUID
    todays_followups: int
    overdue_followups: int
    open_leads: int
    new_leads: int
    leads_by_status: dict[str, int]
    leads_by_intent: dict[str, int]
    pending_test_rides: int = 0
    unread_notifications: int = 0
    my_open_leads: int = 0


class ClassifyLeadRequest(BaseModel):
    """Either pass a `lead_id` (persisted) or raw text (ad-hoc, not persisted)."""

    lead_id: uuid.UUID | None = None
    notes: str | None = None
    tentative_purchase_date: date | None = None


class ClassifyLeadResponse(BaseModel):
    intent: AiIntent
    lead_id: uuid.UUID | None = None
    persisted: bool = False
    source: str = Field(description="'bedrock' or 'fallback'")
    rationale: str | None = None
