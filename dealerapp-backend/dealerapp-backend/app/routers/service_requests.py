"""Service requests and their message threads.

Both roles share these endpoints; visibility is scoped by role — a customer sees
their own requests, dealer staff see their branch's queue. A dealer reply
notifies the customer (`SERVICE_REPLY`) and vice versa.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.deps import AnyUserDep, CurrentUser, DealerUserDep, SessionDep
from app.models.catalog import BikeModel
from app.models.enums import (
    NotificationType,
    RecipientType,
    Role,
    SenderType,
    ServiceRequestStatus,
)
from app.models.org import Customer, Employee
from app.models.service import ServiceRequest, ServiceRequestMessage
from app.models.vehicle import Vehicle
from app.schemas.service import (
    ServiceMessageCreate,
    ServiceMessageOut,
    ServiceRequestCreate,
    ServiceRequestDetailOut,
    ServiceRequestOut,
    ServiceRequestUpdate,
)
from app.services import ai, notifications

logger = logging.getLogger(__name__)

router = APIRouter(tags=["service-requests"])


async def _authorised_request(
    session: AsyncSession, request_id: uuid.UUID, user: CurrentUser
) -> ServiceRequest:
    request = await session.get(ServiceRequest, request_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service request not found"
        )

    if user.role is Role.CUSTOMER:
        if request.customer_id != user.require_customer().id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Service request not found"
            )
    elif request.dealer_id != user.require_dealer_id():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service request not found"
        )

    return request


def _detail_out(
    request: ServiceRequest,
    messages: list[ServiceMessageOut],
) -> ServiceRequestDetailOut:
    """Build the detail DTO without touching `request.messages`.

    `ServiceRequestDetailOut.model_validate(request)` would try to read the lazy
    `messages` relationship during validation, which raises `MissingGreenlet`
    under asyncio. `ServiceRequestOut` has no `messages` field, so validating
    against that first is safe; we then attach the rows we already loaded.
    """
    base = ServiceRequestOut.model_validate(request)
    base.message_count = len(messages)
    base.last_message_at = messages[-1].created_at if messages else None
    return ServiceRequestDetailOut(**base.model_dump(), messages=messages)


async def _message_stats(
    session: AsyncSession, request_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, object]]:
    if not request_ids:
        return {}
    rows = await session.execute(
        select(
            ServiceRequestMessage.service_request_id,
            func.count(),
            func.max(ServiceRequestMessage.created_at),
        )
        .where(ServiceRequestMessage.service_request_id.in_(request_ids))
        .group_by(ServiceRequestMessage.service_request_id)
    )
    return {rid: (int(count), last) for rid, count, last in rows.all()}


async def _enrich(
    session: AsyncSession, requests: list[ServiceRequest], dtos: list[ServiceRequestOut]
) -> None:
    """Fill in customer and vehicle identity on the DTOs, in bulk.

    Three queries regardless of how long the queue is - the dealer list would
    otherwise be one lookup per row, and the app cannot join these itself.
    """
    if not requests:
        return

    customers = {
        c.id: c
        for c in (
            await session.execute(
                select(Customer).where(
                    Customer.id.in_({r.customer_id for r in requests})
                )
            )
        ).scalars()
    }
    vehicles = {
        v.id: v
        for v in (
            await session.execute(
                select(Vehicle).where(Vehicle.id.in_({r.vehicle_id for r in requests}))
            )
        ).scalars()
    }
    model_ids = {v.bike_model_id for v in vehicles.values() if v.bike_model_id}
    models = {}
    if model_ids:
        models = {
            m.id: m
            for m in (
                await session.execute(
                    select(BikeModel).where(BikeModel.id.in_(model_ids))
                )
            ).scalars()
        }

    for request, dto in zip(requests, dtos):
        customer = customers.get(request.customer_id)
        if customer is not None:
            dto.customer_name = customer.name
            dto.customer_phone = customer.phone
        vehicle = vehicles.get(request.vehicle_id)
        if vehicle is not None:
            dto.vehicle_registration = vehicle.registration_no
            model = models.get(vehicle.bike_model_id) if vehicle.bike_model_id else None
            name = " ".join(
                part for part in ((model.name if model else None), (model.variant if model else None)) if part
            )
            dto.vehicle_label = " · ".join(
                part for part in (name or None, vehicle.registration_no) if part
            ) or None


@router.get(
    "/service-requests",
    response_model=list[ServiceRequestOut],
    summary="List service requests (scoped by role)",
)
async def list_service_requests(
    session: SessionDep,
    user: AnyUserDep,
    status_filter: Annotated[ServiceRequestStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ServiceRequestOut]:
    stmt = select(ServiceRequest)
    if user.role is Role.CUSTOMER:
        stmt = stmt.where(ServiceRequest.customer_id == user.require_customer().id)
    else:
        stmt = stmt.where(ServiceRequest.dealer_id == user.require_dealer_id())

    if status_filter is not None:
        stmt = stmt.where(ServiceRequest.status == status_filter)

    stmt = stmt.order_by(ServiceRequest.created_at.desc()).limit(limit).offset(offset)
    rows = list((await session.execute(stmt)).scalars())

    stats = await _message_stats(session, [r.id for r in rows])
    out: list[ServiceRequestOut] = []
    for request in rows:
        dto = ServiceRequestOut.model_validate(request)
        count, last = stats.get(request.id, (0, None))
        dto.message_count = count
        dto.last_message_at = last  # type: ignore[assignment]
        out.append(dto)

    await _enrich(session, rows, out)
    return out


async def _refine_triage(
    request_id: uuid.UUID,
    *,
    type_: str | None,
    description: str | None,
    bike_model_id: uuid.UUID | None,
    odometer_km: int | None,
) -> None:
    """Upgrade a ticket's heuristic triage to the model's verdict.

    Runs after the response has been sent, on its own session (the request's is
    already closed). Failure is logged and ignored - the heuristic values stay,
    which is why the ticket is never left unclassified.
    """
    try:
        async with SessionLocal() as session:
            # Looked up here, not on the request path: it only enriches the prompt.
            model_name = None
            if bike_model_id is not None:
                model = await session.get(BikeModel, bike_model_id)
                model_name = model.name if model else None

        result = await ai.triage_service_request(
            type_=type_,
            description=description,
            vehicle_label=model_name,
            odometer_km=odometer_km,
        )
        if result.source != "bedrock":
            return  # nothing better to store

        async with SessionLocal() as session:
            request = await session.get(ServiceRequest, request_id)
            if request is None:
                return
            request.ai_category = result.category
            request.ai_priority = result.priority
            request.ai_summary = result.summary
            await session.commit()
        logger.info(
            "Refined triage for %s -> %s/%s", request_id, result.category, result.priority
        )
    except Exception:  # noqa: BLE001 - a background task must never crash the worker
        logger.exception("Background triage failed for %s", request_id)


@router.post(
    "/service-requests",
    response_model=ServiceRequestDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a service request",
)
async def create_service_request(
    payload: ServiceRequestCreate,
    session: SessionDep,
    user: AnyUserDep,
    background: BackgroundTasks,
) -> ServiceRequestDetailOut:
    """Customers raise requests for their own vehicles.

    The request is routed to the customer's onboarding dealer; the description
    becomes the thread's first message so the conversation reads in order.
    """
    customer = user.require_customer()

    vehicle = await session.get(Vehicle, payload.vehicle_id)
    if vehicle is None or vehicle.customer_id != customer.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

    # Without a dealer the request would be created but invisible to every
    # branch queue (the dealer list filters on dealer_id), so it would silently
    # never be worked. Fail loudly instead of accepting an orphan.
    if customer.onboarding_dealer_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Your profile is not linked to a dealer yet, so we cannot route "
                "this request. Please contact your dealer."
            ),
        )

    # Triage from the customer's own words plus any diagnostics the app captured.
    # The keyword heuristic runs inline so the ticket is categorised the instant
    # it is stored; the model refines it in the background, because a Bedrock
    # round trip takes several seconds and nobody should watch a spinner for that.
    triage_text = (
        "\n".join(part for part in (payload.description, payload.obd_context) if part)
        or None
    )
    triage = ai.heuristic_triage(payload.type, triage_text)

    request = ServiceRequest(
        vehicle_id=vehicle.id,
        customer_id=customer.id,
        dealer_id=customer.onboarding_dealer_id,
        type=payload.type,
        description=payload.description,
        status=ServiceRequestStatus.OPEN,
        preferred_date=payload.preferred_date,
        ai_category=triage.category,
        ai_priority=triage.priority,
        ai_summary=triage.summary,
    )
    session.add(request)
    await session.flush()

    messages: list[ServiceRequestMessage] = []
    if payload.description:
        first = ServiceRequestMessage(
            service_request_id=request.id,
            sender_type=SenderType.CUSTOMER,
            sender_id=customer.id,
            message=payload.description,
        )
        session.add(first)
        messages.append(first)

    # Diagnostics ride along as their own message so the desk can see the raw
    # evidence without it being buried in the customer's prose.
    if payload.obd_context:
        diagnostics = ServiceRequestMessage(
            service_request_id=request.id,
            sender_type=SenderType.CUSTOMER,
            sender_id=customer.id,
            message=payload.obd_context,
        )
        session.add(diagnostics)
        messages.append(diagnostics)

    # Tell the branch a request has arrived — otherwise a new ticket sits in the
    # queue with nothing prompting anyone to open it.
    await notifications.notify_dealer_staff(
        session,
        dealer_id=request.dealer_id,
        type=NotificationType.SERVICE_REPLY,
        title=f"New service request from {customer.name}",
        body=(payload.description or payload.type or "Service request")[:200],
        payload={
            "service_request_id": str(request.id),
            "route": f"/dealer/tickets/{request.id}",
        },
    )

    await session.commit()
    # No refresh needed: the sessionmaker sets expire_on_commit=False, so the
    # instances keep their loaded values. Each refresh would be another round
    # trip to a database in a different region.

    # Refine the triage once the customer has their confirmation screen.
    background.add_task(
        _refine_triage,
        request.id,
        type_=payload.type,
        description=triage_text,
        bike_model_id=vehicle.bike_model_id,
        odometer_km=vehicle.odometer_km,
    )

    out = [ServiceMessageOut.model_validate(m) for m in messages]
    for m in out:
        m.sender_name = customer.name
    detail = _detail_out(request, out)
    await _enrich(session, [request], [detail])
    return detail


@router.get(
    "/service-requests/{request_id}",
    response_model=ServiceRequestDetailOut,
    summary="Service request with its full thread",
)
async def get_service_request(
    request_id: uuid.UUID, session: SessionDep, user: AnyUserDep
) -> ServiceRequestDetailOut:
    request = await _authorised_request(session, request_id, user)
    messages = list(
        (
            await session.execute(
                select(ServiceRequestMessage)
                .where(ServiceRequestMessage.service_request_id == request.id)
                .order_by(ServiceRequestMessage.created_at)
            )
        ).scalars()
    )

    dto_messages = await _with_sender_names(session, messages)
    detail = _detail_out(request, dto_messages)
    await _enrich(session, [request], [detail])
    return detail


@router.patch(
    "/service-requests/{request_id}",
    response_model=ServiceRequestOut,
    summary="Update request status (dealer only)",
)
async def update_service_request(
    request_id: uuid.UUID,
    payload: ServiceRequestUpdate,
    session: SessionDep,
    user: DealerUserDep,
) -> ServiceRequestOut:
    request = await _authorised_request(session, request_id, user)
    request.status = payload.status
    await session.commit()
    dto = ServiceRequestOut.model_validate(request)
    await _enrich(session, [request], [dto])
    return dto


# --- Thread ---------------------------------------------------------------


async def _with_sender_names(
    session: AsyncSession, messages: list[ServiceRequestMessage]
) -> list[ServiceMessageOut]:
    """Resolve display names in bulk so a long thread stays one query per type."""
    customer_ids = {
        m.sender_id for m in messages if m.sender_type == SenderType.CUSTOMER and m.sender_id
    }
    employee_ids = {
        m.sender_id for m in messages if m.sender_type == SenderType.DEALER and m.sender_id
    }

    names: dict[uuid.UUID, str] = {}
    if customer_ids:
        names.update(
            {
                c.id: c.name
                for c in (
                    await session.execute(
                        select(Customer).where(Customer.id.in_(customer_ids))
                    )
                ).scalars()
            }
        )
    if employee_ids:
        names.update(
            {
                e.id: e.name
                for e in (
                    await session.execute(
                        select(Employee).where(Employee.id.in_(employee_ids))
                    )
                ).scalars()
            }
        )

    out: list[ServiceMessageOut] = []
    for message in messages:
        dto = ServiceMessageOut.model_validate(message)
        dto.sender_name = names.get(message.sender_id) if message.sender_id else None
        out.append(dto)
    return out


@router.get(
    "/service-requests/{request_id}/messages",
    response_model=list[ServiceMessageOut],
    summary="Thread messages",
)
async def list_messages(
    request_id: uuid.UUID, session: SessionDep, user: AnyUserDep
) -> list[ServiceMessageOut]:
    request = await _authorised_request(session, request_id, user)
    messages = list(
        (
            await session.execute(
                select(ServiceRequestMessage)
                .where(ServiceRequestMessage.service_request_id == request.id)
                .order_by(ServiceRequestMessage.created_at)
            )
        ).scalars()
    )
    return await _with_sender_names(session, messages)


@router.post(
    "/service-requests/{request_id}/messages",
    response_model=ServiceMessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post a message to the thread",
)
async def create_message(
    request_id: uuid.UUID,
    payload: ServiceMessageCreate,
    session: SessionDep,
    user: AnyUserDep,
) -> ServiceMessageOut:
    """A dealer reply notifies the customer; a customer reply notifies the desk."""
    request = await _authorised_request(session, request_id, user)

    if user.role is Role.CUSTOMER:
        sender_type = SenderType.CUSTOMER
        sender_id = user.require_customer().id
        sender_name = user.require_customer().name
    else:
        sender_type = SenderType.DEALER
        employee = user.require_employee()
        sender_id = employee.id
        sender_name = employee.name

    message = ServiceRequestMessage(
        service_request_id=request.id,
        sender_type=sender_type,
        sender_id=sender_id,
        message=payload.message,
    )
    session.add(message)

    # A dealer replying to an untouched request means work has started.
    if sender_type == SenderType.DEALER and request.status == ServiceRequestStatus.OPEN:
        request.status = ServiceRequestStatus.IN_PROGRESS

    if sender_type == SenderType.DEALER:
        await notifications.notify(
            session,
            recipient_type=RecipientType.CUSTOMER,
            recipient_id=request.customer_id,
            type=NotificationType.SERVICE_REPLY,
            title="Your dealer replied",
            body=payload.message[:200],
            payload={
                "service_request_id": str(request.id),
                "route": f"/customer/service/{request.id}",
            },
        )
    elif request.dealer_id is not None:
        # The other direction: the desk needs to know the customer answered,
        # otherwise a thread only ever notifies one way and replies sit unseen.
        # There is no assigned employee on a service request, so the branch's
        # active staff are all told.
        await notifications.notify_dealer_staff(
            session,
            dealer_id=request.dealer_id,
            type=NotificationType.SERVICE_REPLY,
            title=f"{sender_name} replied on a service request",
            body=payload.message[:200],
            payload={
                "service_request_id": str(request.id),
                "route": f"/dealer/tickets/{request.id}",
            },
        )

    await session.commit()
    # expire_on_commit=False, so the row keeps its values without another
    # round trip - which matters here, this is the chat send path.
    dto = ServiceMessageOut.model_validate(message)
    dto.sender_name = sender_name
    return dto
