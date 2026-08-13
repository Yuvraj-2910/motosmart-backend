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
    """Reads the last computed figures.

    Rows come from `employee_incentives`, refreshed by
    `POST /internal/incentives/recompute`. Employees with no computed row yet are
    still listed at zero so the roster is complete.
    """
    dealer_id = user.require_dealer_id()
    period = incentive_service.parse_period(month)

    employees = list(
        (
            await session.execute(
                select(Employee)
                .where(Employee.dealer_id == dealer_id)
                .order_by(Employee.name)
            )
        ).scalars()
    )
    employee_ids = [e.id for e in employees]

    computed = {
        row.employee_id: row
        for row in (
            await session.execute(
                select(EmployeeIncentive).where(
                    EmployeeIncentive.employee_id.in_(employee_ids or [uuid.uuid4()]),
                    EmployeeIncentive.period_month == period,
                )
            )
        ).scalars()
    }

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
