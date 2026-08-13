"""Phase 2: test-ride bookings and in-app notifications."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Time, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import TestRideStatus


class TestRideBooking(Base, TimestampMixin):
    __tablename__ = "test_ride_bookings"
    __table_args__ = (
        Index("ix_test_rides_dealer_status", "dealer_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bike_model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bike_models.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    mobile: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    preferred_date: Mapped[date] = mapped_column(Date, nullable=False)
    preferred_time: Mapped[time | None] = mapped_column(Time)
    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TestRideStatus.REQUESTED, server_default="REQUESTED"
    )
    linked_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL")
    )


class Notification(Base, TimestampMixin):
    """Source of truth for the in-app notification centre.

    The Flutter app polls `GET /notifications`; there is no device-token
    registration and no Firebase. SMS/email fan-out via SNS/SES is a
    best-effort side effect layered on top of these rows.

    `recipient_id` is polymorphic over `recipient_type`, so it deliberately
    carries no foreign key.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_recipient", "recipient_type", "recipient_id", "is_read"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    recipient_type: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(String(1000))
    # Deep-link payload consumed by the app's local-notification tap handler.
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
