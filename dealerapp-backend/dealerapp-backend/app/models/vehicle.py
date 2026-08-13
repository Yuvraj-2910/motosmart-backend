"""Phase 3: vehicles, service history, and OBD telemetry."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    bike_model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bike_models.id", ondelete="SET NULL")
    )
    vin: Mapped[str | None] = mapped_column(String(40), unique=True)
    registration_no: Mapped[str | None] = mapped_column(String(20), index=True)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    odometer_km: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    service_records: Mapped[list["ServiceRecord"]] = relationship(
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="ServiceRecord.service_date.desc()",
    )


class ServiceRecord(Base, TimestampMixin):
    __tablename__ = "service_records"
    __table_args__ = (Index("ix_service_records_vehicle_date", "vehicle_id", "service_date"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    odometer_km: Mapped[int | None] = mapped_column(Integer)
    service_type: Mapped[str | None] = mapped_column(String(80))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    next_service_date: Mapped[date | None] = mapped_column(Date)
    next_service_km: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    vehicle: Mapped["Vehicle"] = relationship(back_populates="service_records")


class ObdTelemetry(Base):
    """Mock OBD readings. Seeded by services/obd.py + POST /internal/obd.

    IoT Core is deliberately not wired — the generator is enough for the demo.
    """

    __tablename__ = "obd_telemetry"
    __table_args__ = (Index("ix_obd_vehicle_recorded", "vehicle_id", "recorded_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odometer_km: Mapped[int | None] = mapped_column(Integer)
    battery_voltage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    fuel_level: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    engine_temp: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    avg_speed: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # Comma-separated diagnostic trouble codes, e.g. "P0301,P0420".
    dtc_codes: Mapped[str | None] = mapped_column(String(200))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
