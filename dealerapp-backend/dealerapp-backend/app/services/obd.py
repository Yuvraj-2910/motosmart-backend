"""Mock OBD telemetry.

IoT Core is deliberately **not** wired. This module generates plausible
readings so the customer analytics screens have something real-looking to chart,
and `POST /internal/obd` lets the team push rows on demand during a demo.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vehicle import ObdTelemetry, Vehicle

# Codes chosen to look realistic for a two-wheeler; injected rarely.
_DTC_POOL = ["P0134", "P0301", "P0420", "P0562", "P0128"]


def _q(value: float, places: str = "0.01") -> Decimal:
    return Decimal(str(round(value, 2))).quantize(Decimal(places))


def generate_readings(
    vehicle_id: uuid.UUID,
    *,
    start_odometer: int,
    days: int = 30,
    readings_per_day: int = 1,
    seed: int | None = None,
) -> list[ObdTelemetry]:
    """Produce a monotonically increasing odometer trace with noisy sensors."""
    rng = random.Random(seed if seed is not None else uuid.uuid4().int)
    now = datetime.now(UTC)
    total = days * readings_per_day
    interval = timedelta(days=days) / max(total, 1)

    odometer = float(start_odometer)
    # Realistic Indian urban commute: ~15-45 km/day.
    daily_km = rng.uniform(15, 45)
    per_reading_km = daily_km / readings_per_day

    # A slowly declining battery makes the health flags demo-able.
    battery = rng.uniform(12.4, 12.8)
    battery_drift = rng.uniform(-0.012, 0.004)

    rows: list[ObdTelemetry] = []
    for i in range(total):
        recorded_at = now - interval * (total - 1 - i)
        odometer += per_reading_km * rng.uniform(0.6, 1.4)
        battery = max(11.2, min(14.2, battery + battery_drift + rng.uniform(-0.06, 0.06)))

        rows.append(
            ObdTelemetry(
                vehicle_id=vehicle_id,
                recorded_at=recorded_at,
                odometer_km=int(odometer),
                battery_voltage=_q(battery),
                fuel_level=_q(rng.uniform(8, 100)),
                engine_temp=_q(rng.uniform(72, 104)),
                avg_speed=_q(rng.uniform(22, 48)),
                dtc_codes=(rng.choice(_DTC_POOL) if rng.random() < 0.04 else None),
                raw_json={"src": "mock-generator", "seq": i},
            )
        )
    return rows


async def seed_vehicle(
    session: AsyncSession,
    vehicle_id: uuid.UUID,
    *,
    days: int = 30,
    readings_per_day: int = 1,
) -> int:
    """Generate and persist telemetry for one vehicle. Caller commits."""
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None:
        return 0

    rows = generate_readings(
        vehicle_id,
        start_odometer=max(vehicle.odometer_km - int(days * 30), 0),
        days=days,
        readings_per_day=readings_per_day,
    )
    session.add_all(rows)

    # Keep the vehicle's headline odometer consistent with the newest reading.
    if rows and rows[-1].odometer_km:
        vehicle.odometer_km = max(vehicle.odometer_km, rows[-1].odometer_km)

    await session.flush()
    return len(rows)


async def latest_reading(
    session: AsyncSession, vehicle_id: uuid.UUID
) -> ObdTelemetry | None:
    return (
        await session.execute(
            select(ObdTelemetry)
            .where(ObdTelemetry.vehicle_id == vehicle_id)
            .order_by(ObdTelemetry.recorded_at.desc())
            .limit(1)
        )
    ).scalars().first()


def health_flags(reading: ObdTelemetry | None) -> list[str]:
    """Human-readable warnings the app renders next to the charts."""
    if reading is None:
        return []
    flags: list[str] = []
    if reading.battery_voltage is not None and reading.battery_voltage < Decimal("12.0"):
        flags.append("Battery voltage is low - get it checked at your next service.")
    if reading.engine_temp is not None and reading.engine_temp > Decimal("100"):
        flags.append("Engine temperature is running high.")
    if reading.fuel_level is not None and reading.fuel_level < Decimal("15"):
        flags.append("Fuel level is low.")
    if reading.dtc_codes:
        flags.append(f"Diagnostic codes present: {reading.dtc_codes}")
    return flags
