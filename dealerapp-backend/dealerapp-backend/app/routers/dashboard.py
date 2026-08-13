"""Dealer home dashboard counters."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from sqlalchemy import func, select

from app.deps import DealerUserDep, SessionDep
from app.models.engagement import Notification, TestRideBooking
from app.models.enums import LeadStatus, RecipientType, TestRideStatus
from app.models.lead import Lead, LeadFollowup
from app.schemas.lead import (
    DashboardSummaryOut,
    FollowupWithLeadOut,
    LeadFollowupOut,
)

router = APIRouter(tags=["dashboard"])

OPEN_STATUSES = (LeadStatus.NEW, LeadStatus.FOLLOW_UP)


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryOut,
    summary="Counts for the dealer home screen",
)
async def dashboard_summary(
    session: SessionDep, user: DealerUserDep
) -> DashboardSummaryOut:
    dealer_id = user.require_dealer_id()
    today = date.today()

    async def _count_followups(*conditions: object) -> int:
        stmt = (
            select(func.count(func.distinct(LeadFollowup.id)))
            .select_from(LeadFollowup)
            .join(Lead, Lead.id == LeadFollowup.lead_id)
            .where(Lead.dealer_id == dealer_id, LeadFollowup.completed.is_(False))
        )
        for condition in conditions:
            stmt = stmt.where(condition)
        return int((await session.execute(stmt)).scalar_one())

    todays_followups = await _count_followups(LeadFollowup.scheduled_date == today)
    overdue_followups = await _count_followups(LeadFollowup.scheduled_date < today)

    status_rows = (
        await session.execute(
            select(Lead.status, func.count())
            .where(Lead.dealer_id == dealer_id)
            .group_by(Lead.status)
        )
    ).all()
    leads_by_status = {str(s): int(c) for s, c in status_rows}

    intent_rows = (
        await session.execute(
            select(Lead.ai_intent, func.count())
            .where(Lead.dealer_id == dealer_id, Lead.ai_intent.is_not(None))
            .group_by(Lead.ai_intent)
        )
    ).all()
    leads_by_intent = {str(i): int(c) for i, c in intent_rows}

    open_leads = sum(leads_by_status.get(str(s), 0) for s in OPEN_STATUSES)

    my_open_leads = 0
    if user.employee_id:
        my_open_leads = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Lead)
                    .where(
                        Lead.dealer_id == dealer_id,
                        Lead.assigned_employee_id == user.employee_id,
                        Lead.status.in_(OPEN_STATUSES),
                    )
                )
            ).scalar_one()
        )

    pending_test_rides = int(
        (
            await session.execute(
                select(func.count())
                .select_from(TestRideBooking)
                .where(
                    TestRideBooking.dealer_id == dealer_id,
                    TestRideBooking.status.in_(
                        (TestRideStatus.REQUESTED, TestRideStatus.CONFIRMED)
                    ),
                )
            )
        ).scalar_one()
    )

    # Leads closed (won or lost) since the 1st of the current month.
    closed_this_month = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Lead)
                .where(
                    Lead.dealer_id == dealer_id,
                    Lead.status.in_((LeadStatus.CLOSED_WON, LeadStatus.CLOSED_LOST)),
                    Lead.updated_at >= today.replace(day=1),
                )
            )
        ).scalar_one()
    )

    # The rows behind `todays_followups`, joined to their lead so the home
    # screen can render a worklist rather than just a number.
    followup_rows = (
        await session.execute(
            select(LeadFollowup, Lead.customer_name, Lead.mobile)
            .join(Lead, Lead.id == LeadFollowup.lead_id)
            .where(
                Lead.dealer_id == dealer_id,
                LeadFollowup.completed.is_(False),
                LeadFollowup.scheduled_date <= today,
            )
            .order_by(LeadFollowup.scheduled_date)
            .limit(50)
        )
    ).all()
    todays_followup_items = [
        FollowupWithLeadOut(
            followup=LeadFollowupOut.model_validate(followup),
            lead_customer_name=customer_name,
            lead_mobile=mobile,
        )
        for followup, customer_name, mobile in followup_rows
    ]

    unread_notifications = 0
    if user.employee_id:
        unread_notifications = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.recipient_type == RecipientType.EMPLOYEE,
                        Notification.recipient_id == user.employee_id,
                        Notification.is_read.is_(False),
                    )
                )
            ).scalar_one()
        )

    return DashboardSummaryOut(
        dealer_id=dealer_id,
        todays_followups=todays_followups,
        overdue_followups=overdue_followups,
        open_leads=open_leads,
        new_leads=leads_by_status.get(str(LeadStatus.NEW), 0),
        closed_this_month=closed_this_month,
        todays_followup_items=todays_followup_items,
        leads_by_status=leads_by_status,
        leads_by_intent=leads_by_intent,
        pending_test_rides=pending_test_rides,
        unread_notifications=unread_notifications,
        my_open_leads=my_open_leads,
    )
