"""Phase 4: incentive rules and computed per-employee incentives."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import IncentivePeriod


class IncentiveRule(Base, TimestampMixin):
    __tablename__ = "incentive_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IncentivePeriod.MONTHLY, server_default="MONTHLY"
    )


class EmployeeIncentive(Base):
    """One row per employee per month, produced by services/incentives.recompute."""

    __tablename__ = "employee_incentives"
    __table_args__ = (
        UniqueConstraint("employee_id", "period_month", name="uq_employee_incentive_period"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    # "YYYY-MM"
    period_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    leads_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    conversions_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    test_rides_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    sales_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_incentive: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
