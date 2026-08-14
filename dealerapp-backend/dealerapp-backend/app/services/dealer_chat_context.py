"""Builds the branch-pipeline context fed to the dealer chatbot.

The customer chatbot only ever needs one vehicle's state (see
`app/routers/chatbot.py:_vehicle_context`); a dealer can ask about *any* lead
or ticket at their branch, and there is no single row to hand the model. So
instead this assembles a bounded snapshot: status counts, the most recently
updated leads/tickets, and — since the branch's full pipeline will not always
fit, and an older record could still be exactly what's asked about — a
keyword search over whatever names or numbers the dealer's message mentions.
"""

from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import BikeModel
from app.models.lead import Lead, LeadFollowup
from app.models.org import Customer, Employee
from app.models.service import ServiceRequest
from app.models.vehicle import Vehicle

RECENT_LEAD_LIMIT = 15
RECENT_TICKET_LIMIT = 15
SEARCH_LIMIT = 8

# Words too generic to search on — matching them would pull in half the
# pipeline instead of narrowing to what the dealer actually asked about.
_STOP_WORDS = {
    "lead", "leads", "ticket", "tickets", "status", "what", "when", "who",
    "the", "has", "have", "with", "about", "please", "latest", "customer",
    "customers", "give", "show", "tell", "update", "current", "any", "there",
    "are", "for", "and", "this", "that", "how", "many", "list", "all",
}


def _search_terms(message: str) -> list[str]:
    words = re.findall(r"[A-Za-z]{3,}|\d{4,}", message)
    seen: dict[str, None] = {}
    for w in words:
        if w.lower() not in _STOP_WORDS:
            seen.setdefault(w, None)
    return list(seen)[:6]


async def _status_counts(session: AsyncSession, model, dealer_id: uuid.UUID) -> dict[str, int]:
    rows = await session.execute(
        select(model.status, func.count())
        .where(model.dealer_id == dealer_id)
        .group_by(model.status)
    )
    return {row[0]: row[1] for row in rows.all()}


async def _followup_counts(session: AsyncSession, dealer_id: uuid.UUID) -> tuple[int, int]:
    today = date.today()
    overdue = (
        await session.execute(
            select(func.count())
            .select_from(LeadFollowup)
            .join(Lead, Lead.id == LeadFollowup.lead_id)
            .where(
                Lead.dealer_id == dealer_id,
                LeadFollowup.completed.is_(False),
                LeadFollowup.scheduled_date < today,
            )
        )
    ).scalar_one()
    due_today = (
        await session.execute(
            select(func.count())
            .select_from(LeadFollowup)
            .join(Lead, Lead.id == LeadFollowup.lead_id)
            .where(
                Lead.dealer_id == dealer_id,
                LeadFollowup.completed.is_(False),
                LeadFollowup.scheduled_date == today,
            )
        )
    ).scalar_one()
    return overdue, due_today


def _format_lead(
    lead: Lead,
    employees: dict[uuid.UUID, Employee],
    models: dict[uuid.UUID, BikeModel],
) -> str:
    employee = employees.get(lead.assigned_employee_id) if lead.assigned_employee_id else None
    model = models.get(lead.interested_model_id) if lead.interested_model_id else None
    model_name = f"{model.name} {model.variant or ''}".strip() if model else "not set"

    parts = [
        f"Lead — {lead.customer_name} ({lead.mobile})",
        f"status={lead.status}",
        f"ai_intent={lead.ai_intent or 'n/a'}",
        f"interested_model={model_name}",
        f"assigned_to={employee.name if employee else 'unassigned'}",
    ]
    if lead.tentative_purchase_date:
        parts.append(f"tentative_purchase_date={lead.tentative_purchase_date.isoformat()}")
    if lead.notes:
        note = lead.notes.strip().replace("\n", " ")
        parts.append(f"notes=\"{note[:150]}\"")
    parts.append(f"last_updated={lead.updated_at.date().isoformat()}")
    return " | ".join(parts)


def _format_ticket(
    ticket: ServiceRequest,
    customers: dict[uuid.UUID, Customer],
    vehicles: dict[uuid.UUID, Vehicle],
    models: dict[uuid.UUID, BikeModel],
) -> str:
    customer = customers.get(ticket.customer_id)
    vehicle = vehicles.get(ticket.vehicle_id)
    model = models.get(vehicle.bike_model_id) if vehicle and vehicle.bike_model_id else None
    vehicle_bits = [b for b in ((model.name if model else None), (vehicle.registration_no if vehicle else None)) if b]
    vehicle_label = " ".join(vehicle_bits) if vehicle_bits else "vehicle not on file"

    parts = [
        f"Ticket — {customer.name if customer else 'unknown customer'} ({vehicle_label})",
        f"status={ticket.status}",
        f"category={ticket.ai_category or 'n/a'}",
        f"priority={ticket.ai_priority or 'n/a'}",
    ]
    if ticket.type:
        parts.append(f"type={ticket.type}")
    if ticket.ai_summary:
        parts.append(f"summary=\"{ticket.ai_summary[:150]}\"")
    if ticket.preferred_date:
        parts.append(f"preferred_date={ticket.preferred_date.isoformat()}")
    parts.append(f"opened={ticket.created_at.date().isoformat()}")
    if ticket.resolved_at:
        parts.append(f"resolved={ticket.resolved_at.date().isoformat()}")
    return " | ".join(parts)


async def _bulk_lookups(
    session: AsyncSession, leads: list[Lead], tickets: list[ServiceRequest]
) -> tuple[
    dict[uuid.UUID, Employee],
    dict[uuid.UUID, BikeModel],
    dict[uuid.UUID, Customer],
    dict[uuid.UUID, Vehicle],
]:
    employee_ids = {l.assigned_employee_id for l in leads if l.assigned_employee_id}
    model_ids = {l.interested_model_id for l in leads if l.interested_model_id}
    customer_ids = {t.customer_id for t in tickets}
    vehicle_ids = {t.vehicle_id for t in tickets}

    employees: dict[uuid.UUID, Employee] = {}
    if employee_ids:
        employees = {
            e.id: e
            for e in (
                await session.execute(select(Employee).where(Employee.id.in_(employee_ids)))
            ).scalars()
        }

    customers: dict[uuid.UUID, Customer] = {}
    if customer_ids:
        customers = {
            c.id: c
            for c in (
                await session.execute(select(Customer).where(Customer.id.in_(customer_ids)))
            ).scalars()
        }

    vehicles: dict[uuid.UUID, Vehicle] = {}
    if vehicle_ids:
        vehicles = {
            v.id: v
            for v in (
                await session.execute(select(Vehicle).where(Vehicle.id.in_(vehicle_ids)))
            ).scalars()
        }

    all_model_ids = set(model_ids) | {
        v.bike_model_id for v in vehicles.values() if v.bike_model_id
    }
    models: dict[uuid.UUID, BikeModel] = {}
    if all_model_ids:
        models = {
            m.id: m
            for m in (
                await session.execute(select(BikeModel).where(BikeModel.id.in_(all_model_ids)))
            ).scalars()
        }

    return employees, models, customers, vehicles


async def build_dealer_context(
    session: AsyncSession, *, dealer_id: uuid.UUID, message: str
) -> str:
    """Plain-text snapshot of this dealer's leads and tickets for the model's
    system prompt. Bounded on purpose — a Bedrock call is not a database query,
    so this trades completeness for a predictable prompt size, and leans on the
    keyword search below to still surface anything not in the "recent" window.
    """
    lead_counts = await _status_counts(session, Lead, dealer_id)
    ticket_counts = await _status_counts(session, ServiceRequest, dealer_id)
    overdue, due_today = await _followup_counts(session, dealer_id)

    recent_leads = list(
        (
            await session.execute(
                select(Lead)
                .where(Lead.dealer_id == dealer_id)
                .order_by(Lead.updated_at.desc())
                .limit(RECENT_LEAD_LIMIT)
            )
        ).scalars()
    )
    recent_tickets = list(
        (
            await session.execute(
                select(ServiceRequest)
                .where(ServiceRequest.dealer_id == dealer_id)
                .order_by(ServiceRequest.created_at.desc())
                .limit(RECENT_TICKET_LIMIT)
            )
        ).scalars()
    )

    matched_leads: list[Lead] = []
    matched_tickets: list[ServiceRequest] = []
    terms = _search_terms(message)
    if terms:
        known_lead_ids = {l.id for l in recent_leads}
        known_ticket_ids = {t.id for t in recent_tickets}

        lead_clauses = [Lead.customer_name.ilike(f"%{t}%") for t in terms] + [
            Lead.mobile.ilike(f"%{t}%") for t in terms if t.isdigit()
        ]
        matched_leads = list(
            (
                await session.execute(
                    select(Lead)
                    .where(Lead.dealer_id == dealer_id, or_(*lead_clauses))
                    .order_by(Lead.updated_at.desc())
                    .limit(SEARCH_LIMIT)
                )
            ).scalars()
        )
        matched_leads = [l for l in matched_leads if l.id not in known_lead_ids]

        ticket_stmt = (
            select(ServiceRequest)
            .join(Customer, Customer.id == ServiceRequest.customer_id)
            .outerjoin(Vehicle, Vehicle.id == ServiceRequest.vehicle_id)
            .where(
                ServiceRequest.dealer_id == dealer_id,
                or_(
                    *[Customer.name.ilike(f"%{t}%") for t in terms],
                    *[Vehicle.registration_no.ilike(f"%{t}%") for t in terms],
                    *[ServiceRequest.description.ilike(f"%{t}%") for t in terms],
                ),
            )
            .order_by(ServiceRequest.created_at.desc())
            .limit(SEARCH_LIMIT)
        )
        matched_tickets = list((await session.execute(ticket_stmt)).scalars())
        matched_tickets = [t for t in matched_tickets if t.id not in known_ticket_ids]

    employees, models, customers, vehicles = await _bulk_lookups(
        session, recent_leads + matched_leads, recent_tickets + matched_tickets
    )

    lines: list[str] = []
    lines.append("=== Branch pipeline snapshot ===")
    lines.append(
        "Lead counts by status: "
        + (", ".join(f"{k}={v}" for k, v in lead_counts.items()) or "no leads yet")
    )
    lines.append(
        "Ticket counts by status: "
        + (", ".join(f"{k}={v}" for k, v in ticket_counts.items()) or "no tickets yet")
    )
    lines.append(f"Follow-ups overdue: {overdue}, due today: {due_today}")

    if matched_leads or matched_tickets:
        lines.append("\n=== Specifically matching the dealer's question ===")
        for lead in matched_leads:
            lines.append(_format_lead(lead, employees, models))
        for ticket in matched_tickets:
            lines.append(_format_ticket(ticket, customers, vehicles, models))

    lines.append(f"\n=== Most recently updated leads (up to {RECENT_LEAD_LIMIT}) ===")
    if recent_leads:
        for lead in recent_leads:
            lines.append(_format_lead(lead, employees, models))
    else:
        lines.append("None yet.")

    lines.append(f"\n=== Most recent tickets (up to {RECENT_TICKET_LIMIT}) ===")
    if recent_tickets:
        for ticket in recent_tickets:
            lines.append(_format_ticket(ticket, customers, vehicles, models))
    else:
        lines.append("None yet.")

    return "\n".join(lines)
