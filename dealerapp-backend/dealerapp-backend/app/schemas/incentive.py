"""Incentive schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import IncentiveEventType, IncentivePeriod
from app.schemas.common import ORMModel


class EmployeeIncentiveOut(ORMModel):
    id: uuid.UUID | None = None
    employee_id: uuid.UUID
    employee_name: str | None = None
    period_month: str
    leads_count: int
    conversions_count: int
    test_rides_count: int
    sales_count: int
    total_incentive: Decimal
    computed_at: datetime | None = None


class IncentiveRuleOut(ORMModel):
    id: uuid.UUID
    dealer_id: uuid.UUID
    name: str
    event_type: IncentiveEventType
    amount: Decimal
    period: IncentivePeriod


class IncentiveSummaryOut(BaseModel):
    dealer_id: uuid.UUID
    period_month: str
    employees: list[EmployeeIncentiveOut] = Field(default_factory=list)
    dealer_total: Decimal = Decimal("0")
    rules: list[IncentiveRuleOut] = Field(default_factory=list)
    computed_at: datetime | None = None


class RecomputeRequest(BaseModel):
    month: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; defaults to current month."
    )
    dealer_id: uuid.UUID | None = None


class RecomputeResponse(BaseModel):
    period_month: str
    employees_processed: int
    total_incentive: Decimal
