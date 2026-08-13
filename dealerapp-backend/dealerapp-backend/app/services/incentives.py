"""Incentive computation.

`recompute(month)` aggregates each employee's activity for a calendar month from
`leads` and `test_ride_bookings`, prices it with the dealer's `incentive_rules`,
and upserts one `employee_incentives` row per employee. Exposed for the demo via
`POST /internal/incentives/recompute`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import TestRideBooking
from app.models.enums import IncentiveEventType, LeadStatus, TestRideStatus
from app.models.incentive import EmployeeIncentive, IncentiveRule
from app.models.lead import Lead
from app.models.org import Employee

logger = logging.getLogger(__name__)

# Applied when a dealer has no rule configured for an event type, so the screen
# is never blank during a demo.
DEFAULT_AMOUNTS: dict[str, Decimal] = {
    IncentiveEventType.LEAD_CONVERTED: Decimal("500"),
    IncentiveEventType.TEST_RIDE: Decimal("100"),
    IncentiveEventType.SALE: Decimal("1500"),
}


def current_period() -> str:
    return date.today().strftime("%Y-%m")


def parse_period(month: str | None) -> str:
    if not month:
        return current_period()
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must be formatted YYYY-MM") from exc
    return month


def period_bounds(period_month: str) -> tuple[date, date]:
    """Return `[start, end)` for a YYYY-MM period."""
    year, month = (int(p) for p in period_month.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def per_conversion_amount(amounts: dict[str, Decimal]) -> Decimal:
    """What one closed-won lead earns.

    A conversion and a sale are the same event in this data model, so pricing it
    with LEAD_CONVERTED **and** SALE paid every conversion twice. The higher of
    the two rules wins, which keeps a dealer's configured SALE amount meaningful
    while letting them raise LEAD_CONVERTED above it if they want.
    """
    return max(
        amounts[IncentiveEventType.SALE],
        amounts[IncentiveEventType.LEAD_CONVERTED],
    )


async def _rule_amounts(
    session: AsyncSession, dealer_id: uuid.UUID
) -> dict[str, Decimal]:
    rules = (
        await session.execute(
            select(IncentiveRule).where(IncentiveRule.dealer_id == dealer_id)
        )
    ).scalars()
    amounts = dict(DEFAULT_AMOUNTS)
    for rule in rules:
        amounts[rule.event_type] = Decimal(rule.amount)
    return amounts


async def recompute(
    session: AsyncSession,
    *,
    month: str | None = None,
    dealer_id: uuid.UUID | None = None,
) -> tuple[str, int, Decimal]:
    """Recompute incentives. Returns `(period, employees_processed, total)`.

    Caller commits.
    """
    period = parse_period(month)
    start, end = period_bounds(period)

    emp_stmt = select(Employee)
    if dealer_id is not None:
        emp_stmt = emp_stmt.where(Employee.dealer_id == dealer_id)
    employees = list((await session.execute(emp_stmt)).scalars())

    if not employees:
        return period, 0, Decimal("0")

    employee_ids = [e.id for e in employees]

    # Leads created in the period, per assignee.
    leads_created = dict(
        (
            await session.execute(
                select(Lead.assigned_employee_id, func.count())
                .where(
                    Lead.assigned_employee_id.in_(employee_ids),
                    Lead.created_at >= start,
                    Lead.created_at < end,
                )
                .group_by(Lead.assigned_employee_id)
            )
        ).all()
    )

    # Conversions are attributed to the month the lead closed (updated_at),
    # which is when the salesperson earned it.
    conversions = dict(
        (
            await session.execute(
                select(Lead.assigned_employee_id, func.count())
                .where(
                    Lead.assigned_employee_id.in_(employee_ids),
                    Lead.status == LeadStatus.CLOSED_WON,
                    Lead.updated_at >= start,
                    Lead.updated_at < end,
                )
                .group_by(Lead.assigned_employee_id)
            )
        ).all()
    )

    # Completed test rides, credited to whoever owns the generated lead.
    test_rides = dict(
        (
            await session.execute(
                select(Lead.assigned_employee_id, func.count())
                .join(TestRideBooking, TestRideBooking.linked_lead_id == Lead.id)
                .where(
                    Lead.assigned_employee_id.in_(employee_ids),
                    TestRideBooking.status == TestRideStatus.COMPLETED,
                    TestRideBooking.created_at >= start,
                    TestRideBooking.created_at < end,
                )
                .group_by(Lead.assigned_employee_id)
            )
        ).all()
    )

    existing = {
        row.employee_id: row
        for row in (
            await session.execute(
                select(EmployeeIncentive).where(
                    EmployeeIncentive.employee_id.in_(employee_ids),
                    EmployeeIncentive.period_month == period,
                )
            )
        ).scalars()
    }

    amounts_by_dealer: dict[uuid.UUID, dict[str, Decimal]] = {}
    grand_total = Decimal("0")

    for employee in employees:
        if employee.dealer_id not in amounts_by_dealer:
            amounts_by_dealer[employee.dealer_id] = await _rule_amounts(
                session, employee.dealer_id
            )
        amounts = amounts_by_dealer[employee.dealer_id]

        leads_count = int(leads_created.get(employee.id, 0))
        conversions_count = int(conversions.get(employee.id, 0))
        test_rides_count = int(test_rides.get(employee.id, 0))
        # A conversion to CLOSED_WON *is* the sale in this data model, so the
        # count is reported under both names for the UI...
        sales_count = conversions_count

        # ...but paid only once - see `per_conversion_amount`.
        total = (
            per_conversion_amount(amounts) * conversions_count
            + amounts[IncentiveEventType.TEST_RIDE] * test_rides_count
        )
        grand_total += total

        row = existing.get(employee.id)
        if row is None:
            row = EmployeeIncentive(employee_id=employee.id, period_month=period)
            session.add(row)
        row.leads_count = leads_count
        row.conversions_count = conversions_count
        row.test_rides_count = test_rides_count
        row.sales_count = sales_count
        row.total_incentive = total
        row.computed_at = datetime.now(UTC)

    await session.flush()
    logger.info(
        "Recomputed incentives period=%s employees=%d total=%s",
        period,
        len(employees),
        grand_total,
    )
    return period, len(employees), grand_total


def _selfcheck() -> None:
    """Self-check for the arithmetic that has no database in it.

    Run with `python -m app.services.incentives`. Guards the two things here that
    were (or could silently become) wrong: paying a conversion twice, and the
    December rollover in the period bounds.
    """
    # Pay-once: defaults are LEAD_CONVERTED=500, SALE=1500 -> 1500, never 2000.
    assert per_conversion_amount(dict(DEFAULT_AMOUNTS)) == Decimal("1500"), (
        "a conversion must be priced once, not summed across LEAD_CONVERTED + SALE"
    )

    # A dealer may value the conversion above the sale; the higher rule wins.
    raised = dict(DEFAULT_AMOUNTS) | {IncentiveEventType.LEAD_CONVERTED: Decimal("2500")}
    assert per_conversion_amount(raised) == Decimal("2500")

    # Period parsing and bounds, including the year boundary.
    assert period_bounds("2026-08") == (date(2026, 8, 1), date(2026, 9, 1))
    assert period_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))
    assert parse_period("2026-03") == "2026-03"
    assert parse_period(None) == current_period()
    for bad in ("2026-13", "not-a-month", "2026/03"):
        try:
            parse_period(bad)
        except ValueError:
            pass
        else:  # pragma: no cover - only reached if validation regresses
            raise AssertionError(f"{bad!r} should have been rejected")

    print("incentives self-check: OK")


if __name__ == "__main__":
    _selfcheck()
