"""Dealer-side test-ride queue."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DealerUserDep, SessionDep
from app.models.catalog import BikeModel
from app.models.engagement import TestRideBooking
from app.models.enums import LeadStatus, TestRideStatus
from app.models.lead import Lead
from app.schemas.public import TestRideBookingOut, TestRideUpdate

router = APIRouter(tags=["test-rides"])

# A completed or cancelled ride is terminal. COMPLETED is reachable directly
# from REQUESTED because a walk-in can ride without the request ever being
# confirmed in the app - the frontend offers confirm and complete side by side.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TestRideStatus.REQUESTED: {
        TestRideStatus.CONFIRMED,
        TestRideStatus.COMPLETED,
        TestRideStatus.CANCELLED,
    },
    TestRideStatus.CONFIRMED: {TestRideStatus.COMPLETED, TestRideStatus.CANCELLED},
    TestRideStatus.COMPLETED: set(),
    TestRideStatus.CANCELLED: set(),
}


@router.get(
    "/test-rides",
    response_model=list[TestRideBookingOut],
    summary="Test-ride requests for the dealer",
)
async def list_test_rides(
    session: SessionDep,
    user: DealerUserDep,
    status_filter: Annotated[TestRideStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TestRideBookingOut]:
    dealer_id = user.require_dealer_id()
    stmt = (
        select(TestRideBooking, BikeModel.name)
        .outerjoin(BikeModel, BikeModel.id == TestRideBooking.bike_model_id)
        .where(TestRideBooking.dealer_id == dealer_id)
    )
    if status_filter is not None:
        stmt = stmt.where(TestRideBooking.status == status_filter)
    stmt = (
        stmt.order_by(TestRideBooking.preferred_date.desc(), TestRideBooking.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    out: list[TestRideBookingOut] = []
    for booking, model_name in (await session.execute(stmt)).all():
        dto = TestRideBookingOut.model_validate(booking)
        dto.bike_model_name = model_name
        dto.dealer_name = user.dealer.name if user.dealer else None
        out.append(dto)
    return out


@router.patch(
    "/test-rides/{booking_id}",
    response_model=TestRideBookingOut,
    summary="Confirm, complete, or cancel a test ride",
)
async def update_test_ride(
    booking_id: uuid.UUID,
    payload: TestRideUpdate,
    session: SessionDep,
    user: DealerUserDep,
) -> TestRideBookingOut:
    dealer_id = user.require_dealer_id()
    booking = await session.get(TestRideBooking, booking_id)
    if booking is None or booking.dealer_id != dealer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Test ride not found"
        )

    try:
        target = TestRideStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown status '{payload.status}'",
        ) from exc

    current = TestRideStatus(booking.status)
    if target != current and target not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot move a test ride from {current} to {target}.",
        )

    booking.status = target

    # Confirming the ride means real engagement — nudge the linked lead out of
    # NEW so it shows up in the follow-up pipeline.
    if target is TestRideStatus.CONFIRMED and booking.linked_lead_id:
        lead = await session.get(Lead, booking.linked_lead_id)
        if lead is not None and lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.FOLLOW_UP

    await session.commit()
    await session.refresh(booking)

    dto = TestRideBookingOut.model_validate(booking)
    dto.dealer_name = user.dealer.name if user.dealer else None
    if booking.bike_model_id:
        model = await session.get(BikeModel, booking.bike_model_id)
        dto.bike_model_name = model.name if model else None
    return dto
