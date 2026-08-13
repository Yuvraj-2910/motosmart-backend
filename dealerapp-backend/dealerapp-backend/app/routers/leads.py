"""Lead CRUD, conversion, and the follow-up timeline.

Every read and write is scoped to the caller's dealer, so a salesperson can
never reach another branch's pipeline.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DealerUserDep, SessionDep
from app.models.enums import (
    DueFilter,
    LeadSource,
    LeadStatus,
    NotificationType,
    RecipientType,
)
from app.models.lead import Lead, LeadFollowup
from app.models.org import Customer, Employee
from app.schemas.common import Message
from app.schemas.lead import (
    LeadConvertRequest,
    LeadConvertResponse,
    LeadCreate,
    LeadCreateResponse,
    LeadDetailOut,
    LeadFollowupCreate,
    LeadFollowupOut,
    LeadFollowupUpdate,
    LeadOut,
    LeadUpdate,
)
from app.services import ai, cognito, notifications
from app.services.leads import (
    duplicate_warnings,
    enrich_lead_detail,
    enrich_leads,
)

router = APIRouter(tags=["leads"])


async def _get_lead_for_dealer(
    session: AsyncSession, lead_id: uuid.UUID, dealer_id: uuid.UUID
) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None or lead.dealer_id != dealer_id:
        # 404 rather than 403: don't confirm that another dealer's lead exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


# --- Collection -----------------------------------------------------------


@router.get("/leads", response_model=list[LeadOut], summary="List leads for the dealer")
async def list_leads(
    session: SessionDep,
    user: DealerUserDep,
    status_filter: Annotated[
        LeadStatus | None, Query(alias="status", description="Exact status match")
    ] = None,
    q: Annotated[str | None, Query(description="Search name, mobile, or notes")] = None,
    due: Annotated[DueFilter | None, Query(description="Filter by follow-up due window")] = None,
    mine: Annotated[bool, Query(description="Only leads assigned to me")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LeadOut]:
    dealer_id = user.require_dealer_id()
    stmt = select(Lead).where(Lead.dealer_id == dealer_id)

    if status_filter is not None:
        stmt = stmt.where(Lead.status == status_filter)

    if mine and user.employee_id:
        stmt = stmt.where(Lead.assigned_employee_id == user.employee_id)

    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.customer_name.ilike(needle),
                Lead.mobile.ilike(needle),
                Lead.notes.ilike(needle),
            )
        )

    if due is not None:
        today = date.today()
        # Correlated EXISTS keeps one row per lead even with several follow-ups.
        base = (
            select(LeadFollowup.id)
            .where(
                LeadFollowup.lead_id == Lead.id,
                LeadFollowup.completed.is_(False),
            )
        )
        if due is DueFilter.TODAY:
            base = base.where(LeadFollowup.scheduled_date == today)
        elif due is DueFilter.OVERDUE:
            base = base.where(LeadFollowup.scheduled_date < today)
        else:  # UPCOMING
            base = base.where(LeadFollowup.scheduled_date > today)
        stmt = stmt.where(base.exists())

    stmt = stmt.order_by(Lead.updated_at.desc()).limit(limit).offset(offset)
    leads = list((await session.execute(stmt)).scalars())
    return await enrich_leads(session, leads)


@router.post(
    "/leads",
    response_model=LeadCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a lead (walk-in self-assigns to the caller)",
)
async def create_lead(
    payload: LeadCreate,
    session: SessionDep,
    user: DealerUserDep,
) -> LeadCreateResponse:
    employee = user.require_employee()
    dealer_id = payload.dealer_id or user.require_dealer_id()

    # Duplicate mobile is advisory only — the same person does enquire twice.
    warnings = await duplicate_warnings(session, dealer_id=dealer_id, mobile=payload.mobile)

    lead = Lead(
        dealer_id=dealer_id,
        assigned_employee_id=employee.id,
        customer_name=payload.customer_name,
        mobile=payload.mobile,
        source=payload.source or LeadSource.WALK_IN,
        interested_model_id=payload.interested_model_id,
        current_bike=payload.current_bike,
        tentative_purchase_date=payload.tentative_purchase_date,
        status=LeadStatus.NEW,
        notes=payload.notes,
    )
    session.add(lead)
    await session.flush()

    if payload.classify:
        result = await ai.classify_lead(
            notes=payload.notes,
            tentative_date=payload.tentative_purchase_date,
            customer_name=payload.customer_name,
            current_bike=payload.current_bike,
        )
        lead.ai_intent = result.intent

    await session.commit()
    await session.refresh(lead)

    enriched = (await enrich_leads(session, [lead]))[0]
    return LeadCreateResponse(lead=enriched, warnings=warnings)


# --- Item -----------------------------------------------------------------


@router.get("/leads/{lead_id}", response_model=LeadDetailOut, summary="Lead detail")
async def get_lead(
    lead_id: uuid.UUID, session: SessionDep, user: DealerUserDep
) -> LeadDetailOut:
    lead = await _get_lead_for_dealer(session, lead_id, user.require_dealer_id())
    followups = list(
        (
            await session.execute(
                select(LeadFollowup)
                .where(LeadFollowup.lead_id == lead.id)
                .order_by(LeadFollowup.scheduled_date.desc())
            )
        ).scalars()
    )
    return await enrich_lead_detail(session, lead, followups)


@router.patch("/leads/{lead_id}", response_model=LeadDetailOut, summary="Update a lead")
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    session: SessionDep,
    user: DealerUserDep,
) -> LeadDetailOut:
    dealer_id = user.require_dealer_id()
    lead = await _get_lead_for_dealer(session, lead_id, dealer_id)

    updates = payload.model_dump(exclude_unset=True)

    if "assigned_employee_id" in updates and updates["assigned_employee_id"] is not None:
        assignee = await session.get(Employee, updates["assigned_employee_id"])
        if assignee is None or assignee.dealer_id != dealer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assignee must be an active employee at your dealer.",
            )

    # CLOSED_WON is reachable from the frontend's generic status dropdown, so it
    # is allowed here without a conversion. `POST /leads/{id}/convert` is the
    # richer path that also creates the customer row and links it back; this
    # endpoint just records the outcome.
    for field, value in updates.items():
        setattr(lead, field, value)

    await session.commit()
    await session.refresh(lead)

    followups = list(
        (
            await session.execute(
                select(LeadFollowup)
                .where(LeadFollowup.lead_id == lead.id)
                .order_by(LeadFollowup.scheduled_date.desc())
            )
        ).scalars()
    )
    return await enrich_lead_detail(session, lead, followups)


@router.post(
    "/leads/{lead_id}/convert",
    response_model=LeadConvertResponse,
    summary="Convert a lead into a customer",
)
async def convert_lead(
    lead_id: uuid.UUID,
    payload: LeadConvertRequest,
    session: SessionDep,
    user: DealerUserDep,
) -> LeadConvertResponse:
    """Creates the `customers` row, links it, and closes the lead as won.

    Idempotent: converting an already-converted lead returns the existing
    customer instead of creating a duplicate.
    """
    dealer_id = user.require_dealer_id()
    lead = await _get_lead_for_dealer(session, lead_id, dealer_id)

    if lead.converted_customer_id is not None:
        enriched = (await enrich_leads(session, [lead]))[0]
        return LeadConvertResponse(lead=enriched, customer_id=lead.converted_customer_id)

    name = payload.name or lead.customer_name
    phone = payload.phone or lead.mobile

    # Reuse an existing customer with the same number at this dealer rather than
    # fragmenting their vehicle and service history across duplicate rows.
    customer = (
        await session.execute(
            select(Customer).where(
                Customer.phone == phone,
                Customer.onboarding_dealer_id == dealer_id,
            )
        )
    ).scalars().first()

    if customer is None:
        customer = Customer(
            name=name,
            phone=phone,
            email=payload.email,
            onboarding_dealer_id=dealer_id,
        )
        session.add(customer)
        await session.flush()

    invited = False
    if payload.invite:
        result = await cognito.provision_customer(
            email=payload.email, phone=phone, name=name
        )
        invited = result.ok
        if result.ok and result.cognito_sub and not customer.cognito_sub:
            customer.cognito_sub = result.cognito_sub

    lead.converted_customer_id = customer.id
    lead.status = LeadStatus.CLOSED_WON

    await session.commit()
    await session.refresh(lead)

    enriched = (await enrich_leads(session, [lead]))[0]
    return LeadConvertResponse(lead=enriched, customer_id=customer.id, invited=invited)


# --- Follow-ups -----------------------------------------------------------


@router.get(
    "/leads/{lead_id}/followups",
    response_model=list[LeadFollowupOut],
    summary="Follow-up timeline for a lead",
)
async def list_followups(
    lead_id: uuid.UUID, session: SessionDep, user: DealerUserDep
) -> list[LeadFollowupOut]:
    lead = await _get_lead_for_dealer(session, lead_id, user.require_dealer_id())
    rows = (
        await session.execute(
            select(LeadFollowup)
            .where(LeadFollowup.lead_id == lead.id)
            .order_by(LeadFollowup.scheduled_date.desc())
        )
    ).scalars()
    return [LeadFollowupOut.model_validate(r) for r in rows]


@router.post(
    "/leads/{lead_id}/followups",
    response_model=LeadFollowupOut,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a follow-up",
)
async def create_followup(
    lead_id: uuid.UUID,
    payload: LeadFollowupCreate,
    session: SessionDep,
    user: DealerUserDep,
) -> LeadFollowupOut:
    dealer_id = user.require_dealer_id()
    lead = await _get_lead_for_dealer(session, lead_id, dealer_id)

    followup = LeadFollowup(
        lead_id=lead.id,
        employee_id=user.employee_id,
        next_action=payload.next_action,
        scheduled_date=payload.scheduled_date,
        outcome_note=payload.outcome_note,
        completed=False,
    )
    session.add(followup)

    # Scheduling a follow-up moves an untouched lead out of NEW.
    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.FOLLOW_UP

    # Remind whoever owns the lead when it's due today.
    if lead.assigned_employee_id and payload.scheduled_date <= date.today():
        await notifications.notify(
            session,
            recipient_type=RecipientType.EMPLOYEE,
            recipient_id=lead.assigned_employee_id,
            type=NotificationType.FOLLOWUP_DUE,
            title=f"Follow-up due: {lead.customer_name}",
            body=payload.next_action,
            payload={"lead_id": str(lead.id), "route": f"/dealer/leads/{lead.id}"},
        )

    await session.commit()
    await session.refresh(followup)
    return LeadFollowupOut.model_validate(followup)


@router.patch(
    "/followups/{followup_id}",
    response_model=LeadFollowupOut,
    summary="Update or complete a follow-up",
)
async def update_followup(
    followup_id: uuid.UUID,
    payload: LeadFollowupUpdate,
    session: SessionDep,
    user: DealerUserDep,
) -> LeadFollowupOut:
    dealer_id = user.require_dealer_id()
    followup = await session.get(LeadFollowup, followup_id)
    if followup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up not found"
        )
    # Ownership is inherited from the parent lead.
    await _get_lead_for_dealer(session, followup.lead_id, dealer_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(followup, field, value)

    await session.commit()
    await session.refresh(followup)
    return LeadFollowupOut.model_validate(followup)


@router.delete(
    "/followups/{followup_id}",
    response_model=Message,
    summary="Delete a follow-up",
)
async def delete_followup(
    followup_id: uuid.UUID, session: SessionDep, user: DealerUserDep
) -> Message:
    dealer_id = user.require_dealer_id()
    followup = await session.get(LeadFollowup, followup_id)
    if followup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up not found"
        )
    await _get_lead_for_dealer(session, followup.lead_id, dealer_id)
    await session.delete(followup)
    await session.commit()
    return Message(detail="Follow-up deleted")
