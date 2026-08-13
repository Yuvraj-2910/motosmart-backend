"""Lead helpers: duplicate detection and response enrichment."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import BikeModel
from app.models.enums import LeadStatus
from app.models.lead import Lead, LeadFollowup
from app.models.org import Employee
from app.schemas.catalog import BikeModelOut
from app.schemas.common import Warning_
from app.schemas.lead import LeadDetailOut, LeadFollowupOut, LeadOut

OPEN_STATUSES = (LeadStatus.NEW, LeadStatus.FOLLOW_UP)

DUPLICATE_CODE = "DUPLICATE_MOBILE"


def normalise_mobile(mobile: str) -> str:
    """Keep digits (and a leading +) so '+91 98765-43210' matches '9876543210'."""
    digits = "".join(ch for ch in mobile if ch.isdigit())
    # Indian numbers arrive with and without the 91 country code; compare on the
    # last 10 digits, which is the part that actually identifies the subscriber.
    return digits[-10:] if len(digits) >= 10 else digits


async def find_duplicate(
    session: AsyncSession,
    *,
    dealer_id: uuid.UUID,
    mobile: str,
    exclude_lead_id: uuid.UUID | None = None,
) -> Lead | None:
    """Find an existing open lead at this dealer with the same mobile number.

    Used to *warn*, never to block — the same person legitimately enquires twice,
    and a hard block would lose a real lead.
    """
    tail = normalise_mobile(mobile)
    if not tail:
        return None

    stmt = (
        select(Lead)
        .where(
            Lead.dealer_id == dealer_id,
            Lead.status.in_(OPEN_STATUSES),
            func.right(func.regexp_replace(Lead.mobile, r"\D", "", "g"), len(tail)) == tail,
        )
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    if exclude_lead_id is not None:
        stmt = stmt.where(Lead.id != exclude_lead_id)

    return (await session.execute(stmt)).scalars().first()


async def duplicate_warnings(
    session: AsyncSession,
    *,
    dealer_id: uuid.UUID,
    mobile: str,
    exclude_lead_id: uuid.UUID | None = None,
) -> list[Warning_]:
    existing = await find_duplicate(
        session, dealer_id=dealer_id, mobile=mobile, exclude_lead_id=exclude_lead_id
    )
    if existing is None:
        return []
    return [
        Warning_(
            code=DUPLICATE_CODE,
            message=(
                f"An open lead for this mobile number already exists "
                f"({existing.customer_name}, created "
                f"{existing.created_at.date().isoformat()}). Check before duplicating."
            ),
        )
    ]


# --- Enrichment -----------------------------------------------------------


async def _followup_index(
    session: AsyncSession, lead_ids: list[uuid.UUID]
) -> dict[uuid.UUID, date]:
    """Earliest incomplete follow-up date per lead."""
    if not lead_ids:
        return {}
    rows = await session.execute(
        select(LeadFollowup.lead_id, func.min(LeadFollowup.scheduled_date))
        .where(
            LeadFollowup.lead_id.in_(lead_ids),
            LeadFollowup.completed.is_(False),
        )
        .group_by(LeadFollowup.lead_id)
    )
    return {lead_id: due for lead_id, due in rows.all()}


async def enrich_leads(
    session: AsyncSession, leads: list[Lead]
) -> list[LeadOut]:
    """Attach model, assignee name, and follow-up due state in bulk.

    One query per relationship rather than per lead — the leads list is the
    dealer's most-hit screen.
    """
    if not leads:
        return []

    model_ids = {l.interested_model_id for l in leads if l.interested_model_id}
    employee_ids = {l.assigned_employee_id for l in leads if l.assigned_employee_id}

    models: dict[uuid.UUID, BikeModel] = {}
    if model_ids:
        models = {
            m.id: m
            for m in (
                await session.execute(select(BikeModel).where(BikeModel.id.in_(model_ids)))
            ).scalars()
        }

    employees: dict[uuid.UUID, str] = {}
    if employee_ids:
        employees = {
            e.id: e.name
            for e in (
                await session.execute(select(Employee).where(Employee.id.in_(employee_ids)))
            ).scalars()
        }

    due_map = await _followup_index(session, [l.id for l in leads])
    today = date.today()

    out: list[LeadOut] = []
    for lead in leads:
        dto = LeadOut.model_validate(lead)
        model = models.get(lead.interested_model_id) if lead.interested_model_id else None
        dto.interested_model = BikeModelOut.model_validate(model) if model else None
        dto.assigned_employee_name = (
            employees.get(lead.assigned_employee_id) if lead.assigned_employee_id else None
        )
        due = due_map.get(lead.id)
        dto.next_followup_date = due
        dto.is_followup_overdue = bool(due and due < today)
        out.append(dto)
    return out


async def enrich_lead_detail(
    session: AsyncSession, lead: Lead, followups: list[LeadFollowup]
) -> LeadDetailOut:
    base = (await enrich_leads(session, [lead]))[0]
    return LeadDetailOut(
        **base.model_dump(),
        followups=[LeadFollowupOut.model_validate(f) for f in followups],
    )
