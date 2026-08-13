"""Notification service.

The `notifications` table is the source of truth for the in-app notification
centre — the Flutter app polls `GET /notifications`, so there are no device
tokens and no Firebase.

SMS (SNS) and email (SES) delivery is an optional, **best-effort** side effect
for high-priority events. External sends never raise into the caller and never
roll back the business write; a booking that succeeded must stay committed even
if SNS is down.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import aws
from app.core.config import settings
from app.models.engagement import Notification
from app.models.enums import NotificationType, RecipientType
from app.models.org import Employee

logger = logging.getLogger(__name__)

# Events worth an out-of-band nudge as well as the in-app row.
HIGH_PRIORITY: set[str] = {NotificationType.NEW_LEAD, NotificationType.TEST_RIDE}


async def notify(
    session: AsyncSession,
    *,
    recipient_type: RecipientType,
    recipient_id: uuid.UUID,
    type: NotificationType,
    title: str,
    body: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Notification:
    """Insert a notification row. Does not commit — the caller owns the txn."""
    notification = Notification(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        type=type,
        title=title,
        body=body,
        payload_json=payload or {},
        is_read=False,
    )
    session.add(notification)
    await session.flush()
    logger.info(
        "Notification queued type=%s recipient=%s:%s", type, recipient_type, recipient_id
    )
    return notification


async def notify_dealer_staff(
    session: AsyncSession,
    *,
    dealer_id: uuid.UUID,
    type: NotificationType,
    title: str,
    body: str | None = None,
    payload: dict[str, Any] | None = None,
) -> list[Notification]:
    """Fan a notification out to every active employee at a dealer.

    Used for events that belong to the branch rather than to one salesperson —
    a customer message on a service thread, for instance. `service_requests` has
    no assigned employee, so there is no single correct recipient and the whole
    desk is told; whoever picks it up marks it read.

    Returns the rows created (empty when the branch has no active staff, which is
    not an error — the request still stands and stays visible in the queue).
    """
    employees = list(
        (
            await session.execute(
                select(Employee).where(
                    Employee.dealer_id == dealer_id,
                    Employee.is_active.is_(True),
                )
            )
        ).scalars()
    )
    if not employees:
        logger.warning(
            "No active staff at dealer %s to notify about %s", dealer_id, type
        )
        return []

    # Built directly rather than via `notify()` so the whole fan-out costs one
    # flush instead of one per employee - the database is a region away, so each
    # extra round trip is ~250ms on the customer's request.
    rows = [
        Notification(
            recipient_type=RecipientType.EMPLOYEE,
            recipient_id=employee.id,
            type=type,
            title=title,
            body=body,
            payload_json=payload or {},
            is_read=False,
        )
        for employee in employees
    ]
    session.add_all(rows)
    await session.flush()
    logger.info(
        "Notification fanned out type=%s dealer=%s recipients=%d",
        type,
        dealer_id,
        len(rows),
    )
    return rows


async def send_sms(phone: str | None, message: str) -> bool:
    """Best-effort SNS SMS. Returns success; never raises."""
    if not settings.SNS_SMS_ENABLED or not phone:
        return False
    try:
        client = aws.sns_client()
        await aws.call(client.publish, PhoneNumber=phone, Message=message[:1500])
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.warning("SNS SMS to %s failed: %s", _mask(phone), exc)
        return False


async def send_email(to_email: str | None, subject: str, body: str) -> bool:
    """Best-effort SES email. Returns success; never raises."""
    if not to_email or not settings.SES_FROM_EMAIL:
        return False
    try:
        client = aws.ses_client()
        await aws.call(
            client.send_email,
            Source=settings.SES_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject[:200]},
                "Body": {"Text": {"Data": body}},
            },
        )
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.warning("SES email to %s failed: %s", _mask(to_email), exc)
        return False


async def fan_out(
    *,
    type: NotificationType,
    title: str,
    body: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> None:
    """Optional external delivery for high-priority events.

    Call this *after* the transaction commits, typically via
    `BackgroundTasks.add_task`, so a slow or failing AWS call cannot affect the
    request's outcome.
    """
    if type not in HIGH_PRIORITY:
        return
    text = f"{title}\n{body}" if body else title
    await send_sms(phone, text)
    await send_email(email, title, body or title)


def _mask(value: str) -> str:
    """Keep PII out of logs (DPDP)."""
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"
