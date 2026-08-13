"""Employee incentive views."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DealerUserDep, SessionDep
from app.models.incentive import EmployeeIncentive, IncentiveRule
from app.models.org import Employee
from app.schemas.incentive import (
    EmployeeIncentiveOut,
    IncentiveRuleOut,
    IncentiveSummaryOut,
)
from app.services import incentives as incentive_service

router = APIRouter(tags=["incentives"])


@router.get(
    "/incentives",
    response_model=IncentiveSummaryOut,
    summary="Monthly incentive breakdown for the dealer",
)
async def dealer_incentives(
    session: SessionDep,
    user: DealerUserDep,
    month: Annotated[
        str | None, Query(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (default: this month)")
    ] = None,
) -> IncentiveSummaryOut:
    """Reads the computed figures, computing them on first read if needed.

    Rows live in `employee_incentives` and are refreshed by
    `POST /internal/incentives/recompute`. If nothing has been computed for this
    dealer and period yet, this endpoint runs the aggregation itself rather than
    returning a screen full of zeroes that looks like "no one sold anything" —
    the numbers are derived from `leads`/`test_ride_bookings`, so computing them
    on demand is cheap and always consistent with the source data.

    Employees with genuinely no activity are still listed at zero so the roster
    stays complete.
    """
    dealer_id = user.require_dealer_id()
    period = incentive_service.parse_period(month)

    async def load() -> tuple[list[Employee], dict[uuid.UUID, EmployeeIncentive]]:
        employees = list(
            (
                await session.execute(
                    select(Employee)
                    .where(Employee.dealer_id == dealer_id)
                    .order_by(Employee.name)
                )
            ).scalars()
        )
        rows = {
            row.employee_id: row
            for row in (
                await session.execute(
                    select(EmployeeIncentive).where(
                        EmployeeIncentive.employee_id.in_(
                            [e.id for e in employees] or [uuid.uuid4()]
                        ),
                        EmployeeIncentive.period_month == period,
                    )
                )
            ).scalars()
        }
        return employees, rows

    employees, computed = await load()

    # The current month is live: the figures move every time somebody converts a
    # lead or closes a ticket, so recompute on read rather than serving a row
    # that went stale the moment it was written. Past months keep their stored
    # figures — that is what makes them payable.
    is_current_period = period == incentive_service.parse_period(None)

    if employees and (not computed or is_current_period):
        await incentive_service.recompute(session, month=period, dealer_id=dealer_id)
        await session.commit()
        employees, computed = await load()

    items: list[EmployeeIncentiveOut] = []
    total = Decimal("0")
    latest_computed = None

    for employee in employees:
        row = computed.get(employee.id)
        if row is None:
            items.append(
                EmployeeIncentiveOut(
                    employee_id=employee.id,
                    employee_name=employee.name,
                    period_month=period,
                    leads_count=0,
                    conversions_count=0,
                    test_rides_count=0,
                    sales_count=0,
                    total_incentive=Decimal("0"),
                )
            )
            continue

        dto = EmployeeIncentiveOut.model_validate(row)
        dto.employee_name = employee.name
        items.append(dto)
        total += Decimal(row.total_incentive)
        if latest_computed is None or (row.computed_at and row.computed_at > latest_computed):
            latest_computed = row.computed_at

    rules = (
        await session.execute(
            select(IncentiveRule).where(IncentiveRule.dealer_id == dealer_id)
        )
    ).scalars()

    return IncentiveSummaryOut(
        dealer_id=dealer_id,
        period_month=period,
        employees=items,
        dealer_total=total,
        rules=[IncentiveRuleOut.model_validate(r) for r in rules],
        computed_at=latest_computed,
    )


@router.get(
    "/incentives/employee/{employee_id}",
    response_model=list[EmployeeIncentiveOut],
    summary="One employee's incentive history",
)
async def employee_incentives(
    employee_id: uuid.UUID,
    session: SessionDep,
    user: DealerUserDep,
    limit: Annotated[int, Query(ge=1, le=36)] = 12,
) -> list[EmployeeIncentiveOut]:
    dealer_id = user.require_dealer_id()
    employee = await session.get(Employee, employee_id)
    if employee is None or employee.dealer_id != dealer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )

    rows = (
        await session.execute(
            select(EmployeeIncentive)
            .where(EmployeeIncentive.employee_id == employee_id)
            .order_by(EmployeeIncentive.period_month.desc())
            .limit(limit)
        )
    ).scalars()

    out: list[EmployeeIncentiveOut] = []
    for row in rows:
        dto = EmployeeIncentiveOut.model_validate(row)
        dto.employee_name = employee.name
        out.append(dto)
    return out
