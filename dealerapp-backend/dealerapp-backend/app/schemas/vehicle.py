"""Vehicle, analytics, service-status, and service-history schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.catalog import BikeModelOut
from app.schemas.common import ORMModel


class VehicleCreate(BaseModel):
    customer_id: uuid.UUID
    bike_model_id: uuid.UUID | None = None
    vin: str | None = Field(default=None, max_length=40)
    registration_no: str | None = Field(default=None, max_length=20)
    purchase_date: date | None = None
    odometer_km: int = Field(default=0, ge=0)


class VehicleOut(ORMModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    bike_model_id: uuid.UUID | None = None
    vin: str | None = None
    registration_no: str | None = None
    purchase_date: date | None = None
    odometer_km: int
    bike_model: BikeModelOut | None = None


class ServiceRecordOut(ORMModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    service_date: date
    odometer_km: int | None = None
    service_type: str | None = None
    cost: Decimal | None = None
    next_service_date: date | None = None
    next_service_km: int | None = None
    notes: str | None = None


class ServiceStatusOut(BaseModel):
    """Last service and next due, evaluated by BOTH time and distance."""

    vehicle_id: uuid.UUID
    odometer_km: int

    last_service_date: date | None = None
    last_service_km: int | None = None
    last_service_type: str | None = None

    next_service_date: date | None = None
    next_service_km: int | None = None

    days_until_due: int | None = None
    km_until_due: int | None = None

    is_due_by_date: bool = False
    is_due_by_km: bool = False
    is_overdue: bool = False
    # DUE_NOW | OVERDUE | UPCOMING | OK | UNKNOWN
    status: str
    message: str


class TelemetryPoint(BaseModel):
    recorded_at: datetime
    odometer_km: int | None = None
    battery_voltage: Decimal | None = None
    fuel_level: Decimal | None = None
    engine_temp: Decimal | None = None
    avg_speed: Decimal | None = None


class VehicleAnalyticsOut(BaseModel):
    vehicle_id: uuid.UUID
    window_days: int
    reading_count: int
    latest: TelemetryPoint | None = None
    odometer_series: list[TelemetryPoint] = Field(default_factory=list)
    avg_battery_voltage: Decimal | None = None
    avg_fuel_level: Decimal | None = None
    avg_engine_temp: Decimal | None = None
    avg_speed: Decimal | None = None
    distance_in_window_km: int | None = None
    active_dtc_codes: list[str] = Field(default_factory=list)
    health_flags: list[str] = Field(default_factory=list)


class ObdIngestIn(BaseModel):
    vehicle_id: uuid.UUID
    recorded_at: datetime | None = None
    odometer_km: int | None = None
    battery_voltage: Decimal | None = None
    fuel_level: Decimal | None = None
    engine_temp: Decimal | None = None
    avg_speed: Decimal | None = None
    dtc_codes: str | None = None


class ObdSeedRequest(BaseModel):
    vehicle_id: uuid.UUID
    days: int = Field(default=30, ge=1, le=365)
    readings_per_day: int = Field(default=1, ge=1, le=24)
