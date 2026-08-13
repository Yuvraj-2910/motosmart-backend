"""Leads and their follow-up timeline."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TimestampsMixin, uuid_pk
from app.models.enums import LeadSource, LeadStatus


class Lead(Base, TimestampsMixin):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_dealer_assignee_status", "dealer_id", "assigned_employee_id", "status"),
        Index("ix_leads_dealer_mobile", "dealer_id", "mobile"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealers.id", ondelete="CASCADE"), nullable=False
    )
    assigned_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL")
    )
    customer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    mobile: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LeadSource.WALK_IN, server_default="WALK_IN"
    )
    interested_model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bike_models.id", ondelete="SET NULL")
    )
    current_bike: Mapped[str | None] = mapped_column(String(160))
    tentative_purchase_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LeadStatus.NEW, server_default="NEW"
    )
    ai_intent: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)
    converted_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )

    followups: Mapped[list["LeadFollowup"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="LeadFollowup.scheduled_date",
    )


class LeadFollowup(Base, TimestampMixin):
    __tablename__ = "lead_followups"
    __table_args__ = (
        Index("ix_lead_followups_scheduled", "scheduled_date", "completed"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL")
    )
    next_action: Mapped[str] = mapped_column(String(400), nullable=False)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    outcome_note: Mapped[str | None] = mapped_column(Text)

    lead: Mapped["Lead"] = relationship(back_populates="followups")
