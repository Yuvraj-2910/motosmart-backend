"""Dealer-side onboarding: customers, vehicles, and asset uploads."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DealerUserDep, SessionDep
from app.models.catalog import BikeModel
from app.models.org import Customer
from app.models.vehicle import Vehicle
from app.schemas.catalog import BikeModelOut
from app.schemas.org import CustomerCreate, CustomerOut
from app.schemas.storage import (
    PresignDownloadResponse,
    PresignUploadRequest,
    PresignUploadResponse,
)
from app.schemas.vehicle import VehicleCreate, VehicleOut
from app.services import cognito, storage

router = APIRouter(tags=["onboarding"])


@router.get(
    "/customers",
    response_model=list[CustomerOut],
    summary="Customers onboarded by this dealer",
)
async def list_customers(
    session: SessionDep,
    user: DealerUserDep,
    q: Annotated[str | None, Query(description="Search name or phone")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CustomerOut]:
    dealer_id = user.require_dealer_id()
    stmt = select(Customer).where(Customer.onboarding_dealer_id == dealer_id)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(Customer.name.ilike(needle) | Customer.phone.ilike(needle))
    stmt = stmt.order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars()
    return [CustomerOut.model_validate(r) for r in rows]


@router.post(
    "/customers",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a customer directly",
)
async def create_customer(
    payload: CustomerCreate, session: SessionDep, user: DealerUserDep
) -> CustomerOut:
    """Creates a customer outside the lead-conversion flow.

    Reuses an existing record with the same phone at this dealer so service
    history doesn't fragment across duplicates.
    """
    dealer_id = user.require_dealer_id()

    existing = (
        await session.execute(
            select(Customer).where(
                Customer.phone == payload.phone,
                Customer.onboarding_dealer_id == dealer_id,
            )
        )
    ).scalars().first()

    if existing is not None:
        return CustomerOut.model_validate(existing)

    customer = Customer(
        name=payload.name,
        phone=payload.phone,
        email=str(payload.email) if payload.email else None,
        onboarding_dealer_id=dealer_id,
    )
    session.add(customer)
    await session.flush()

    if payload.invite:
        result = await cognito.provision_customer(
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
            name=payload.name,
        )
        if result.ok and result.cognito_sub:
            customer.cognito_sub = result.cognito_sub

    await session.commit()
    await session.refresh(customer)
    return CustomerOut.model_validate(customer)


@router.post(
    "/vehicles",
    response_model=VehicleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a vehicle to a customer",
)
async def create_vehicle(
    payload: VehicleCreate, session: SessionDep, user: DealerUserDep
) -> VehicleOut:
    dealer_id = user.require_dealer_id()

    customer = await session.get(Customer, payload.customer_id)
    if customer is None or customer.onboarding_dealer_id != dealer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found at your dealer",
        )

    model: BikeModel | None = None
    if payload.bike_model_id is not None:
        model = await session.get(BikeModel, payload.bike_model_id)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Bike model not found"
            )

    if payload.vin:
        clash = (
            await session.execute(select(Vehicle).where(Vehicle.vin == payload.vin))
        ).scalars().first()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A vehicle with this VIN already exists.",
            )

    vehicle = Vehicle(
        customer_id=payload.customer_id,
        bike_model_id=payload.bike_model_id,
        vin=payload.vin,
        registration_no=payload.registration_no,
        purchase_date=payload.purchase_date,
        odometer_km=payload.odometer_km,
    )
    session.add(vehicle)
    await session.commit()
    await session.refresh(vehicle)

    dto = VehicleOut.model_validate(vehicle)
    dto.bike_model = BikeModelOut.model_validate(model) if model else None
    return dto


# --- Asset uploads --------------------------------------------------------


@router.post(
    "/uploads/presign",
    response_model=PresignUploadResponse,
    summary="Presigned S3 PUT URL for images, brochures, and attachments",
)
async def presign_upload(
    payload: PresignUploadRequest, user: DealerUserDep
) -> PresignUploadResponse:
    """The app PUTs the bytes straight to S3, so files never transit this API."""
    try:
        url, key, expires = await storage.presign_upload(
            filename=payload.filename,
            content_type=payload.content_type,
            category=payload.category,
        )
    except storage.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return PresignUploadResponse(
        upload_url=url, key=key, public_url=storage.public_url(key), expires_in=expires
    )


@router.get(
    "/uploads/presign-download",
    response_model=PresignDownloadResponse,
    summary="Presigned S3 GET URL for a private object",
)
async def presign_download(
    user: DealerUserDep, key: Annotated[str, Query(min_length=1, max_length=500)]
) -> PresignDownloadResponse:
    if ".." in key or key.startswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid key")
    try:
        url, expires = await storage.presign_download(key)
    except storage.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return PresignDownloadResponse(download_url=url, key=key, expires_in=expires)
