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


class TelemetrySample(BaseModel):
    """One reading from the rolling window the app keeps while monitoring."""

    # Seconds before "now" that this sample was taken, so the server needs no
    # clock agreement with the device.
    age_seconds: Decimal = Field(ge=0, le=3600)
    rpm: int | None = Field(default=None, ge=0, le=30000)
    coolant_temp_c: Decimal | None = None
    speed_kph: Decimal | None = Field(default=None, ge=0)
    battery_voltage: Decimal | None = Field(default=None, ge=0)
    throttle_position_pct: Decimal | None = Field(default=None, ge=0, le=100)
    fuel_level_pct: Decimal | None = Field(default=None, ge=0, le=100)


class TelemetrySummaryRequest(BaseModel):
    """The readings currently on the rider's dashboard.

    Sent by the app rather than read from `obd_telemetry` because the dashboard
    can be driven by a live ELM327 device whose readings were never persisted —
    the summary must describe exactly what the rider is looking at.
    """

    rpm: int | None = Field(default=None, ge=0, le=30000)
    coolant_temp_c: Decimal | None = None
    speed_kph: Decimal | None = Field(default=None, ge=0)
    battery_voltage: Decimal | None = Field(default=None, ge=0)
    throttle_position_pct: Decimal | None = Field(default=None, ge=0, le=100)
    fuel_level_pct: Decimal | None = Field(default=None, ge=0, le=100)
    odometer_km: int | None = Field(default=None, ge=0)
    dtc_codes: list[str] = Field(default_factory=list, max_length=20)
    # The rule engine's own verdict, so the summary agrees with the badge the
    # rider can already see instead of second-guessing it.
    health_level: str | None = Field(default=None, max_length=20)
    health_reasons: list[str] = Field(default_factory=list, max_length=20)

    # The last ~minute of readings the app buffered while the monitor screen was
    # open. Summarising a window rather than one instant lets the model talk about
    # trends ("coolant climbed 12 C") instead of a single snapshot.
    samples: list[TelemetrySample] = Field(default_factory=list, max_length=600)
    window_seconds: int | None = Field(default=None, ge=0, le=3600)


class TelemetrySummaryOut(BaseModel):
    summary: str
    source: str = Field(description="'bedrock' or 'fallback'")
    # Echoed back so the UI can say what the summary was based on.
    samples_used: int = 0
    window_seconds: int | None = None
    # True when the readings warrant opening a service request, which is what
    # gates the "raise a ticket" button in the app.
    is_actionable: bool = False
    suggested_type: str | None = None
    suggested_description: str | None = None
    obd_context: str | None = Field(
        default=None,
        description="Readings formatted for attaching to a service request.",
    )


class DtcExplanationRequest(TelemetrySummaryRequest):
    """One fault code plus the readings it appeared with."""

    dtc_code: str = Field(min_length=2, max_length=16)
    technical_description: str | None = Field(default=None, max_length=400)


class DtcExplanationOut(BaseModel):
    explanation: str
    source: str = Field(description="'bedrock' or 'fallback'")


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
