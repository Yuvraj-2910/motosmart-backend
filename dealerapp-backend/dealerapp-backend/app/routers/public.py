"""Unauthenticated public funnel: catalog, exchange value, test-ride booking.

No dependency on `get_current_user` anywhere in this module — a guest browsing
the app must reach all of it. The one write endpoint (`POST /public/test-rides`)
creates a booking *and* a lead, and routes that lead to a salesperson by
round-robin.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import func, select

from app.deps import SessionDep
from app.models.catalog import BikeModel
from app.models.engagement import TestRideBooking
from app.models.enums import (
    LeadSource,
    LeadStatus,
    NotificationType,
    RecipientType,
    StockStatus,
    TestRideStatus,
)
from app.models.lead import Lead
from app.models.org import Dealer
from app.schemas.catalog import AvailabilityOut, BikeModelOut
from app.schemas.org import DealerPublicOut
from app.schemas.public import (
    ExchangeEstimateOut,
    ExchangeValueRequest,
    TestRideBookingCreate,
    TestRideBookingOut,
    TestRideBookingResponse,
)
from app.services import assignment, exchange, notifications
from app.services.leads import duplicate_warnings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/models", response_model=list[BikeModelOut], summary="Browse the bike catalog")
async def list_models(
    session: SessionDep,
    category: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search by model name")] = None,
    available_only: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BikeModelOut]:
    stmt = select(BikeModel)
    if available_only:
        stmt = stmt.where(BikeModel.is_available.is_(True))
    if category:
        stmt = stmt.where(func.lower(BikeModel.category) == category.strip().lower())
    if q:
        stmt = stmt.where(BikeModel.name.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(BikeModel.price).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars()
    return [BikeModelOut.model_validate(r) for r in rows]


@router.get(
    "/models/{model_id}", response_model=BikeModelOut, summary="Bike detail with specs"
)
async def get_model(model_id: uuid.UUID, session: SessionDep) -> BikeModelOut:
    model = await session.get(BikeModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return BikeModelOut.model_validate(model)


@router.get(
    "/dealers",
    response_model=list[DealerPublicOut],
    summary="Dealers for the booking form's location picker",
)
async def list_dealers(
    session: SessionDep,
    city: Annotated[str | None, Query()] = None,
    pincode: Annotated[str | None, Query()] = None,
) -> list[DealerPublicOut]:
    """The test-ride form needs this so the backend can route the lead."""
    stmt = select(Dealer)
    if city:
        stmt = stmt.where(func.lower(Dealer.city) == city.strip().lower())
    if pincode:
        stmt = stmt.where(Dealer.pincode == pincode.strip())
    rows = (await session.execute(stmt.order_by(Dealer.name))).scalars()
    return [DealerPublicOut.model_validate(r) for r in rows]


@router.get(
    "/availability", response_model=AvailabilityOut, summary="Stock status for a model"
)
async def availability(
    session: SessionDep, model_id: Annotated[uuid.UUID, Query()]
) -> AvailabilityOut:
    model = await session.get(BikeModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")

    # Per-dealer stock isn't modelled in this phase; report the catalog-level
    # flag and the number of dealers who could show the bike.
    dealer_count = int(
        (await session.execute(select(func.count()).select_from(Dealer))).scalar_one()
    )
    return AvailabilityOut(
        bike_model_id=model.id,
        stock_status=StockStatus(model.stock_status),
        is_available=model.is_available,
        dealers_with_stock=dealer_count if model.is_available else 0,
    )


@router.post(
    "/exchange-value",
    response_model=ExchangeEstimateOut,
    summary="Estimate exchange value for a customer's current bike",
)
async def exchange_value(
    payload: ExchangeValueRequest, session: SessionDep
) -> ExchangeEstimateOut:
    return await exchange.estimate(session, payload)


@router.post(
    "/test-rides",
    response_model=TestRideBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a test ride (auto-creates and auto-assigns a lead)",
)
async def book_test_ride(
    payload: TestRideBookingCreate,
    session: SessionDep,
    background: BackgroundTasks,
) -> TestRideBookingResponse:
    """Booking, round-robin assignment, lead creation, and the notification row
    all happen in **one transaction** — a booking never exists without its lead.

    External SMS/email is deferred to a background task so a slow SNS call can't
    hold up the guest's confirmation screen or, worse, fail the booking.
    """
    dealer = await assignment.resolve_dealer(
        session, dealer_id=payload.dealer_id, pincode=payload.pincode
    )
    if dealer is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select a dealer location so we can arrange your test ride.",
        )

    model_name: str | None = None
    if payload.bike_model_id is not None:
        model = await session.get(BikeModel, payload.bike_model_id)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
            )
        model_name = model.name

    warnings = await duplicate_warnings(
        session, dealer_id=dealer.id, mobile=payload.mobile
    )

    booking = TestRideBooking(
        bike_model_id=payload.bike_model_id,
        name=payload.name,
        mobile=payload.mobile,
        preferred_date=payload.preferred_date,
        preferred_time=payload.preferred_time,
        dealer_id=dealer.id,
        status=TestRideStatus.REQUESTED,
    )
    session.add(booking)
    await session.flush()

    # Round-robin picks the assignee and advances the dealer's pointer under a
    # row lock, so two concurrent bookings can't land on the same person.
    assignee = await assignment.pick_next_employee(session, dealer.id)

    note_lines = [
        f"Test ride requested for {payload.preferred_date.isoformat()}"
        + (f" at {payload.preferred_time.isoformat()}" if payload.preferred_time else ""),
    ]
    if model_name:
        note_lines.append(f"Model: {model_name}")
    if payload.notes:
        note_lines.append(payload.notes)

    lead = Lead(
        dealer_id=dealer.id,
        assigned_employee_id=assignee.id if assignee else None,
        customer_name=payload.name,
        mobile=payload.mobile,
        source=LeadSource.TEST_RIDE,
        interested_model_id=payload.bike_model_id,
        tentative_purchase_date=None,
        status=LeadStatus.NEW,
        notes="\n".join(note_lines),
    )
    session.add(lead)
    await session.flush()

    booking.linked_lead_id = lead.id

    if assignee is not None:
        await notifications.notify(
            session,
            recipient_type=RecipientType.EMPLOYEE,
            recipient_id=assignee.id,
            type=NotificationType.NEW_LEAD,
            title=f"New test-ride lead: {payload.name}",
            body=(
                f"{model_name or 'Bike'} on {payload.preferred_date.isoformat()}. "
                f"Contact {payload.mobile}."
            ),
            payload={
                "lead_id": str(lead.id),
                "test_ride_id": str(booking.id),
                "route": f"/dealer/leads/{lead.id}",
            },
        )

    await session.commit()
    await session.refresh(booking)

    if assignee is not None:
        background.add_task(
            notifications.fan_out,
            type=NotificationType.NEW_LEAD,
            title=f"New test-ride lead: {payload.name}",
            body=f"Contact {payload.mobile} for {payload.preferred_date.isoformat()}.",
            phone=assignee.phone,
            email=assignee.email,
        )

    out = TestRideBookingOut.model_validate(booking)
    out.bike_model_name = model_name
    out.dealer_name = dealer.name

    return TestRideBookingResponse(
        booking=out,
        lead_id=lead.id,
        assigned_employee_id=assignee.id if assignee else None,
        warnings=warnings,
    )
