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
    ServiceRecordOut,
    ServiceStatusOut,
    VehicleAnalyticsOut,
    VehicleOut,
)
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
