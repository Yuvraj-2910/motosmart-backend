"""Vehicle analytics and service-status computation."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vehicle import ObdTelemetry, ServiceRecord, Vehicle
from app.schemas.vehicle import (
    ServiceStatusOut,
    TelemetryPoint,
    VehicleAnalyticsOut,
)
from app.services import obd

# Yamaha-style default interval when a service record doesn't state the next due.
DEFAULT_SERVICE_INTERVAL_DAYS = 180
DEFAULT_SERVICE_INTERVAL_KM = 3000
# First service after purchase.
FIRST_SERVICE_DAYS = 60
FIRST_SERVICE_KM = 1000
# Window in which we say "upcoming" rather than "ok".
UPCOMING_DAYS = 30
UPCOMING_KM = 300


def _avg(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    total = sum(values, Decimal("0"))
    return (total / Decimal(len(values))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def build_analytics(
    session: AsyncSession, vehicle_id: uuid.UUID, *, window_days: int = 30
) -> VehicleAnalyticsOut:
    """Aggregate OBD telemetry into the shape the fl_chart dashboards expect."""
    since = datetime.now(UTC) - timedelta(days=window_days)

    rows = list(
        (
            await session.execute(
                select(ObdTelemetry)
                .where(
                    ObdTelemetry.vehicle_id == vehicle_id,
                    ObdTelemetry.recorded_at >= since,
                )
                .order_by(ObdTelemetry.recorded_at)
            )
        ).scalars()
    )

    points = [
        TelemetryPoint(
            recorded_at=r.recorded_at,
            odometer_km=r.odometer_km,
            battery_voltage=r.battery_voltage,
            fuel_level=r.fuel_level,
            engine_temp=r.engine_temp,
            avg_speed=r.avg_speed,
        )
        for r in rows
    ]

    latest_row = rows[-1] if rows else await obd.latest_reading(session, vehicle_id)
    latest = (
        TelemetryPoint(
            recorded_at=latest_row.recorded_at,
            odometer_km=latest_row.odometer_km,
            battery_voltage=latest_row.battery_voltage,
            fuel_level=latest_row.fuel_level,
            engine_temp=latest_row.engine_temp,
            avg_speed=latest_row.avg_speed,
        )
        if latest_row
        else None
    )

    odometers = [r.odometer_km for r in rows if r.odometer_km is not None]
    distance = (max(odometers) - min(odometers)) if len(odometers) >= 2 else None

    dtcs: list[str] = []
    for r in rows:
        if r.dtc_codes:
            for code in r.dtc_codes.split(","):
                code = code.strip()
                if code and code not in dtcs:
                    dtcs.append(code)

    return VehicleAnalyticsOut(
        vehicle_id=vehicle_id,
        window_days=window_days,
        reading_count=len(rows),
        latest=latest,
        odometer_series=points,
        avg_battery_voltage=_avg([r.battery_voltage for r in rows if r.battery_voltage is not None]),
        avg_fuel_level=_avg([r.fuel_level for r in rows if r.fuel_level is not None]),
        avg_engine_temp=_avg([r.engine_temp for r in rows if r.engine_temp is not None]),
        avg_speed=_avg([r.avg_speed for r in rows if r.avg_speed is not None]),
        distance_in_window_km=distance,
        active_dtc_codes=dtcs,
        health_flags=obd.health_flags(latest_row),
    )


async def build_service_status(
    session: AsyncSession, vehicle: Vehicle
) -> ServiceStatusOut:
    """Compute last service and next due by **both** time and distance.

    Precedence for "next due":
      1. what the last service record explicitly scheduled,
      2. last service date/km plus the standard interval,
      3. for a vehicle with no service history, purchase date/odometer plus the
         first-service interval.
    """
    last = (
        await session.execute(
            select(ServiceRecord)
            .where(ServiceRecord.vehicle_id == vehicle.id)
            .order_by(ServiceRecord.service_date.desc(), ServiceRecord.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    # Prefer the freshest telemetry odometer over the stored headline value.
    latest_reading = await obd.latest_reading(session, vehicle.id)
    odometer = max(
        vehicle.odometer_km or 0,
        (latest_reading.odometer_km or 0) if latest_reading else 0,
    )

    today = date.today()

    if last is not None:
        next_date = last.next_service_date or (
            last.service_date + timedelta(days=DEFAULT_SERVICE_INTERVAL_DAYS)
        )
        next_km = last.next_service_km or (
            (last.odometer_km or odometer) + DEFAULT_SERVICE_INTERVAL_KM
        )
        last_date: date | None = last.service_date
        last_km = last.odometer_km
        last_type = last.service_type
    elif vehicle.purchase_date is not None:
        next_date = vehicle.purchase_date + timedelta(days=FIRST_SERVICE_DAYS)
        next_km = FIRST_SERVICE_KM
        last_date = last_km = last_type = None
    else:
        return ServiceStatusOut(
            vehicle_id=vehicle.id,
            odometer_km=odometer,
            status="UNKNOWN",
            message=(
                "No service history or purchase date on file. Ask your dealer to update "
                "your vehicle record."
            ),
        )

    days_until = (next_date - today).days
    km_until = next_km - odometer

    due_by_date = days_until <= 0
    due_by_km = km_until <= 0
    overdue = due_by_date or due_by_km

    if overdue:
        status = "OVERDUE"
        reasons = []
        if due_by_date:
            reasons.append(f"{abs(days_until)} day(s) past the due date")
        if due_by_km:
            reasons.append(f"{abs(km_until)} km past the due reading")
        message = f"Service is overdue - {' and '.join(reasons)}. Please book a visit."
    elif days_until <= UPCOMING_DAYS or km_until <= UPCOMING_KM:
        status = "DUE_NOW"
        message = (
            f"Service due soon - in {days_until} day(s) or {km_until} km, "
            "whichever comes first."
        )
    else:
        status = "OK"
        message = (
            f"Next service due on {next_date.isoformat()} or at {next_km} km "
            f"({days_until} day(s) / {km_until} km away)."
        )

    return ServiceStatusOut(
        vehicle_id=vehicle.id,
        odometer_km=odometer,
        last_service_date=last_date,
        last_service_km=last_km,
        last_service_type=last_type,
        next_service_date=next_date,
        next_service_km=next_km,
        days_until_due=days_until,
        km_until_due=km_until,
        is_due_by_date=due_by_date,
        is_due_by_km=due_by_km,
        is_overdue=overdue,
        status=status,
        message=message,
    )


async def owns_vehicle(
    session: AsyncSession, vehicle_id: uuid.UUID, customer_id: uuid.UUID
) -> bool:
    count = (
        await session.execute(
            select(func.count())
            .select_from(Vehicle)
            .where(Vehicle.id == vehicle_id, Vehicle.customer_id == customer_id)
        )
    ).scalar_one()
    return bool(count)
