"""Customer-facing vehicle endpoints: garage, analytics, service status/history."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import AnyUserDep, CurrentUser, CustomerUserDep, SessionDep
from app.models.catalog import BikeModel
from app.models.enums import Role
from app.models.vehicle import ServiceRecord, Vehicle
from app.schemas.catalog import BikeModelOut
from app.schemas.vehicle import (
    DtcExplanationOut,
    DtcExplanationRequest,
    ServiceRecordOut,
    ServiceStatusOut,
    TelemetrySummaryOut,
    TelemetrySummaryRequest,
    VehicleAnalyticsOut,
    VehicleOut,
)
from app.services import ai
from app.services import vehicles as vehicle_service

router = APIRouter(tags=["vehicles"])


async def _authorised_vehicle(
    session: AsyncSession, vehicle_id: uuid.UUID, user: CurrentUser
) -> Vehicle:
    """A customer may read their own vehicles; dealer staff may read vehicles
    belonging to customers they onboarded (needed for the service desk)."""
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

    if user.role is Role.CUSTOMER:
        if vehicle.customer_id != user.require_customer().id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found"
            )
        return vehicle

    from app.models.org import Customer  # local import avoids a cycle at module load

    owner = await session.get(Customer, vehicle.customer_id)
    if owner is None or owner.onboarding_dealer_id != user.require_dealer_id():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    return vehicle


@router.get("/me/vehicles", response_model=list[VehicleOut], summary="The customer's garage")
async def my_vehicles(session: SessionDep, user: CustomerUserDep) -> list[VehicleOut]:
    customer = user.require_customer()
    rows = list(
        (
            await session.execute(
                select(Vehicle)
                .where(Vehicle.customer_id == customer.id)
                .order_by(Vehicle.created_at.desc())
            )
        ).scalars()
    )

    model_ids = {v.bike_model_id for v in rows if v.bike_model_id}
    models: dict[uuid.UUID, BikeModel] = {}
    if model_ids:
        models = {
            m.id: m
            for m in (
                await session.execute(select(BikeModel).where(BikeModel.id.in_(model_ids)))
            ).scalars()
        }

    out: list[VehicleOut] = []
    for vehicle in rows:
        dto = VehicleOut.model_validate(vehicle)
        model = models.get(vehicle.bike_model_id) if vehicle.bike_model_id else None
        dto.bike_model = BikeModelOut.model_validate(model) if model else None
        out.append(dto)
    return out


@router.get(
    "/vehicles/{vehicle_id}/analytics",
    response_model=VehicleAnalyticsOut,
    summary="OBD analytics for the charts",
)
async def vehicle_analytics(
    vehicle_id: uuid.UUID,
    session: SessionDep,
    user: AnyUserDep,
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> VehicleAnalyticsOut:
    vehicle = await _authorised_vehicle(session, vehicle_id, user)
    return await vehicle_service.build_analytics(
        session, vehicle.id, window_days=window_days
    )


@router.get(
    "/vehicles/{vehicle_id}/service-status",
    response_model=ServiceStatusOut,
    summary="Last service and next due, by time and by distance",
)
async def service_status(
    vehicle_id: uuid.UUID, session: SessionDep, user: AnyUserDep
) -> ServiceStatusOut:
    vehicle = await _authorised_vehicle(session, vehicle_id, user)
    return await vehicle_service.build_service_status(session, vehicle)


@router.get(
    "/vehicles/{vehicle_id}/service-history",
    response_model=list[ServiceRecordOut],
    summary="Past service records",
)
async def service_history(
    vehicle_id: uuid.UUID,
    session: SessionDep,
    user: AnyUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ServiceRecordOut]:
    vehicle = await _authorised_vehicle(session, vehicle_id, user)
    rows = (
        await session.execute(
            select(ServiceRecord)
            .where(ServiceRecord.vehicle_id == vehicle.id)
            .order_by(ServiceRecord.service_date.desc())
            .limit(limit)
        )
    ).scalars()
    return [ServiceRecordOut.model_validate(r) for r in rows]


# --- OBD dashboard summary ------------------------------------------------

# Thresholds for "worth raising a ticket". Deliberately conservative: a false
# "all good" is worse than a false prompt, but nagging on every reading would
# train the rider to ignore the button.
_HOT_COOLANT_C = 105.0
_LOW_BATTERY_V = 12.0
_HIGH_BATTERY_V = 15.0
_LOW_FUEL_PCT = 10.0


@router.post(
    "/vehicles/{vehicle_id}/telemetry-summary",
    response_model=TelemetrySummaryOut,
    summary="Plain-language AI summary of the readings on the rider's dashboard",
)
async def telemetry_summary(
    vehicle_id: uuid.UUID,
    payload: TelemetrySummaryRequest,
    session: SessionDep,
    user: AnyUserDep,
) -> TelemetrySummaryOut:
    """Summarises the readings the app sends, and says whether they justify a ticket.

    The app posts what is on screen (live device or simulator) rather than the
    server reading `obd_telemetry`, so the summary always matches the gauges the
    rider is looking at. Never fails on AI trouble: a deterministic rule-based
    summary is returned with `source="fallback"`.
    """
    await _authorised_vehicle(session, vehicle_id, user)

    readings = payload.model_dump()
    # Decimals are fine for JSON but the prompt and thresholds want plain floats.
    for key in (
        "coolant_temp_c",
        "speed_kph",
        "battery_voltage",
        "throttle_position_pct",
        "fuel_level_pct",
    ):
        if readings.get(key) is not None:
            readings[key] = float(readings[key])

    # Statistics from the buffered window, so the summary can describe how the
    # bike behaved over the minute rather than at one instant. Oldest first.
    samples = sorted(
        (s.model_dump() for s in payload.samples),
        key=lambda s: s["age_seconds"],
        reverse=True,
    )
    readings["window_stats"] = ai.summarise_window(samples)
    readings["window_seconds"] = payload.window_seconds

    result = await ai.summarise_telemetry(readings)

    dtcs = [c.strip() for c in payload.dtc_codes if c and c.strip()]
    faults: list[str] = []
    if dtcs:
        faults.append(f"active fault code(s): {', '.join(dtcs)}")
    coolant = readings.get("coolant_temp_c")
    if coolant is not None and coolant >= _HOT_COOLANT_C:
        faults.append(f"coolant at {coolant:.0f}°C")
    battery = readings.get("battery_voltage")
    if battery is not None and (battery < _LOW_BATTERY_V or battery > _HIGH_BATTERY_V):
        faults.append(f"battery at {battery:.2f}V")
    fuel = readings.get("fuel_level_pct")
    if fuel is not None and fuel < _LOW_FUEL_PCT:
        faults.append(f"fuel at {fuel:.0f}%")
    # The rule engine's own verdict counts even if no threshold here tripped.
    if (payload.health_level or "").lower() in {"red", "amber"}:
        for reason in payload.health_reasons:
            if reason and reason not in faults:
                faults.append(reason)

    context_lines = ["Captured from the bike's OBD port:"]
    for label, key, suffix in (
        ("Engine speed", "rpm", " rpm"),
        ("Coolant", "coolant_temp_c", " °C"),
        ("Road speed", "speed_kph", " km/h"),
        ("Battery", "battery_voltage", " V"),
        ("Throttle", "throttle_position_pct", " %"),
        ("Fuel level", "fuel_level_pct", " %"),
        ("Odometer", "odometer_km", " km"),
    ):
        value = readings.get(key)
        if value is not None:
            context_lines.append(f"- {label}: {value}{suffix}")
    context_lines.append(f"- Fault codes: {', '.join(dtcs) if dtcs else 'none'}")
    if payload.health_level:
        context_lines.append(f"- On-device verdict: {payload.health_level}")

    suggested_type = None
    suggested_description = None
    if faults:
        suggested_type = "Engine noise" if dtcs else "General service"
        suggested_description = (
            "Raised from the bike's health dashboard. Detected: "
            + "; ".join(faults)
            + "."
        )

    return TelemetrySummaryOut(
        summary=result.summary,
        source=result.source,
        samples_used=len(samples),
        window_seconds=payload.window_seconds,
        is_actionable=bool(faults),
        suggested_type=suggested_type,
        suggested_description=suggested_description,
        obd_context="\n".join(context_lines),
    )


@router.post(
    "/vehicles/{vehicle_id}/dtc-explanation",
    response_model=DtcExplanationOut,
    summary="Rider-facing explanation of one active fault code",
)
async def dtc_explanation(
    vehicle_id: uuid.UUID,
    payload: DtcExplanationRequest,
    session: SessionDep,
    user: AnyUserDep,
) -> DtcExplanationOut:
    """Explains a single DTC in plain language, in the context of the readings.

    The OBD dashboard's alert card calls this instead of talking to a model
    directly, which is what keeps AI credentials out of the app bundle. Falls
    back to the code's technical description if the model is unreachable.
    """
    await _authorised_vehicle(session, vehicle_id, user)

    readings = payload.model_dump()
    for key in (
        "coolant_temp_c",
        "speed_kph",
        "battery_voltage",
        "throttle_position_pct",
        "fuel_level_pct",
    ):
        if readings.get(key) is not None:
            readings[key] = float(readings[key])

    explanation, source = await ai.explain_dtc(
        dtc_code=payload.dtc_code,
        technical_description=payload.technical_description,
        readings=readings,
    )
    return DtcExplanationOut(explanation=explanation, source=source)
