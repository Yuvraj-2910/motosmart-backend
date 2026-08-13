"""Round-robin lead auto-assignment.

The rotation pointer lives on `dealers.last_assigned_employee_id`. To pick the
next salesperson we:

  1. lock the dealer row (`SELECT ... FOR UPDATE`) so concurrent bookings can't
     both read the same pointer and hand the lead to one person twice,
  2. load active employees in a stable order (`created_at`, `id` as tiebreak),
  3. find the pointer's index, advance by one modulo the roster size,
  4. write the pointer back.

The rotation is **self-healing**: if the stored pointer references an employee
who has since been deactivated or deleted, the index lookup returns -1 and
rotation restarts at the first candidate.

The caller owns the transaction — nothing here commits. That keeps booking
insert + assignment + lead insert atomic.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import Dealer, Employee

logger = logging.getLogger(__name__)


async def pick_next_employee(
    session: AsyncSession, dealer_id: uuid.UUID
) -> Employee | None:
    """Advance the dealer's round-robin pointer and return the assignee.

    Returns `None` when the dealer has no active staff — the lead then lands in
    the unassigned pool rather than being dropped.
    """
    # Lock the pointer row for the remainder of the transaction.
    dealer = (
        await session.execute(
            select(Dealer).where(Dealer.id == dealer_id).with_for_update()
        )
    ).scalar_one_or_none()

    if dealer is None:
        logger.warning("Assignment requested for unknown dealer %s", dealer_id)
        return None

    candidates = list(
        (
            await session.execute(
                select(Employee)
                .where(Employee.dealer_id == dealer_id, Employee.is_active.is_(True))
                # `created_at` is the rotation order the plan specifies. Employees
                # seeded in one transaction share a statement timestamp, so `id`
                # breaks ties and keeps the order total and stable.
                .order_by(Employee.created_at, Employee.id)
            )
        ).scalars()
    )

    if not candidates:
        logger.info("Dealer %s has no active staff; lead left unassigned", dealer_id)
        return None

    pointer = dealer.last_assigned_employee_id
    idx = -1
    if pointer is not None:
        for i, emp in enumerate(candidates):
            if emp.id == pointer:
                idx = i
                break
        if idx == -1:
            logger.info(
                "Stale rotation pointer %s for dealer %s; restarting rotation",
                pointer,
                dealer_id,
            )

    assignee = candidates[(idx + 1) % len(candidates)]
    dealer.last_assigned_employee_id = assignee.id
    await session.flush()

    logger.info(
        "Round-robin assigned dealer=%s employee=%s (%d candidates)",
        dealer_id,
        assignee.id,
        len(candidates),
    )
    return assignee


async def resolve_dealer(
    session: AsyncSession,
    *,
    dealer_id: uuid.UUID | None = None,
    pincode: str | None = None,
) -> Dealer | None:
    """Resolve the dealer for a public booking.

    Preference order: explicit selection from the form, then pincode match, then
    a deterministic default so a booking is never lost for want of a branch.
    """
    if dealer_id is not None:
        dealer = await session.get(Dealer, dealer_id)
        if dealer is not None:
            return dealer
        logger.warning("Booking referenced unknown dealer %s", dealer_id)

    if pincode:
        dealer = (
            await session.execute(
                select(Dealer).where(Dealer.pincode == pincode.strip()).order_by(Dealer.code)
            )
        ).scalars().first()
        if dealer is not None:
            return dealer

    return (
        await session.execute(select(Dealer).order_by(Dealer.code))
    ).scalars().first()
