"""Internal ops and demo hooks.

Guarded by `X-Internal-Key`, not by Cognito — these are operator tools, not app
surface. They exist so the team can drive a demo without a shell into the box.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import SessionDep, require_internal_key
from app.models.vehicle import ObdTelemetry, Vehicle
from app.schemas.common import Message
from app.schemas.incentive import RecomputeRequest, RecomputeResponse
from app.schemas.vehicle import ObdIngestIn, ObdSeedRequest
from app.services import incentives as incentive_service
from app.services import obd as obd_service

router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_key)]
)


@router.post(
    "/obd",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a single OBD reading (fake device endpoint)",
)
async def ingest_obd(payload: ObdIngestIn, session: SessionDep) -> Message:
    """Stands in for a real telematics pipeline. IoT Core is not wired."""
    vehicle = await session.get(Vehicle, payload.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

    reading = ObdTelemetry(
        vehicle_id=vehicle.id,
        recorded_at=payload.recorded_at or datetime.now(UTC),
        odometer_km=payload.odometer_km,
        battery_voltage=payload.battery_voltage,
        fuel_level=payload.fuel_level,
        engine_temp=payload.engine_temp,
        avg_speed=payload.avg_speed,
        dtc_codes=payload.dtc_codes,
        raw_json={"src": "internal-ingest"},
    )
    session.add(reading)

    # Odometer only ever moves forward.
    if payload.odometer_km and payload.odometer_km > (vehicle.odometer_km or 0):
        vehicle.odometer_km = payload.odometer_km

    await session.commit()
    return Message(detail="Reading stored")


@router.post(
    "/obd/seed",
    response_model=Message,
    summary="Generate a window of mock telemetry for one vehicle",
)
async def seed_obd(payload: ObdSeedRequest, session: SessionDep) -> Message:
    count = await obd_service.seed_vehicle(
        session,
        payload.vehicle_id,
        days=payload.days,
        readings_per_day=payload.readings_per_day,
    )
    if count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    await session.commit()
    return Message(detail=f"Generated {count} reading(s)")


@router.post(
    "/incentives/recompute",
    response_model=RecomputeResponse,
    summary="Recompute employee incentives for a month",
)
async def recompute_incentives(
    payload: RecomputeRequest, session: SessionDep
) -> RecomputeResponse:
    try:
        period, processed, total = await incentive_service.recompute(
            session, month=payload.month, dealer_id=payload.dealer_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    await session.commit()
    return RecomputeResponse(
        period_month=period, employees_processed=processed, total_incentive=total
    )
