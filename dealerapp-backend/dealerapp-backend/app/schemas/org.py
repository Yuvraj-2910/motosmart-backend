"""Profile, dealer, employee, and customer schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import Role
from app.schemas.common import ORMModel


class DealerOut(ORMModel):
    id: uuid.UUID
    name: str
    code: str
    city: str | None = None
    address: str | None = None
    phone: str | None = None
    pincode: str | None = None


class DealerPublicOut(ORMModel):
    """Dealer info safe for the unauthenticated booking form."""

    id: uuid.UUID
    name: str
    city: str | None = None
    address: str | None = None
    phone: str | None = None


class EmployeeOut(ORMModel):
    id: uuid.UUID
    dealer_id: uuid.UUID
    name: str
    phone: str | None = None
    email: str | None = None
    is_active: bool


class CustomerOut(ORMModel):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None = None
    onboarding_dealer_id: uuid.UUID | None = None
    created_at: datetime


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    phone: str = Field(min_length=6, max_length=20)
    email: EmailStr | None = None
    invite: bool = Field(
        default=False,
        description="Also provision a Cognito user in the CUSTOMER group and send an OTP invite.",
    )


class MeOut(BaseModel):
    """`GET /me` — the app uses `role` to pick its post-login shell."""

    role: Role
    cognito_sub: str
    employee: EmployeeOut | None = None
    customer: CustomerOut | None = None
    dealer: DealerOut | None = None
