"""Unauthenticated funnel schemas: exchange value and test-ride booking."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TestRideStatus
from app.schemas.common import Warning_


class ExchangeValueRequest(BaseModel):
    brand: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=1980, le=2100)
    condition: str = Field(
        default="GOOD",
        description="EXCELLENT | GOOD | FAIR | POOR",
    )
    odometer_km: int | None = Field(default=None, ge=0)

    @field_validator("condition")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class ExchangeEstimateOut(BaseModel):
    brand: str
    model: str
    year: int
    condition: str
    base_value: Decimal
    condition_factor: Decimal
    age_factor: Decimal
    odometer_factor: Decimal
    estimated_value: Decimal
    estimate_low: Decimal
    estimate_high: Decimal
    currency: str = "INR"
    is_reference_match: bool = Field(
        description="False when no exchange_values row matched and a heuristic was used."
    )
    disclaimer: str = (
        "Indicative only. Final exchange value is confirmed after physical inspection."
    )


class TestRideBookingCreate(BaseModel):
    bike_model_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    mobile: str = Field(min_length=6, max_length=20)
    preferred_date: date
    preferred_time: time | None = None
    # Required so the backend can route the generated lead to a dealer. The app
    # sends the selected dealer; pincode is the fallback path.
    dealer_id: uuid.UUID | None = None
    pincode: str | None = Field(default=None, max_length=10)
    notes: str | None = None

    @field_validator("mobile")
    @classmethod
    def _clean_mobile(cls, v: str) -> str:
        cleaned = "".join(ch for ch in v if ch.isdigit() or ch == "+")
        if len(cleaned) < 6:
            raise ValueError("mobile must contain at least 6 digits")
        return cleaned


class TestRideBookingOut(BaseModel):
    id: uuid.UUID
    bike_model_id: uuid.UUID | None = None
    bike_model_name: str | None = None
    name: str
    mobile: str
    preferred_date: date
    preferred_time: time | None = None
    dealer_id: uuid.UUID
    dealer_name: str | None = None
    status: TestRideStatus
    linked_lead_id: uuid.UUID | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TestRideBookingResponse(BaseModel):
    booking: TestRideBookingOut
    lead_id: uuid.UUID | None = None
    assigned_employee_id: uuid.UUID | None = None
    warnings: list[Warning_] = Field(default_factory=list)


class TestRideUpdate(BaseModel):
    status: str = Field(description="CONFIRMED | COMPLETED | CANCELLED")

    @field_validator("status")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()
