"""In-app notification centre.

The Flutter app polls this on launch, on resume, and on a light foreground
timer; new unread rows drive the badge and a local OS notification. There is no
device-token registration and no Firebase.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.deps import AnyUserDep, CurrentUser, SessionDep
from app.models.engagement import Notification
from app.models.enums import RecipientType, Role
from app.schemas.common import Message
from app.schemas.engagement import NotificationListOut, NotificationOut

router = APIRouter(tags=["notifications"])


def _recipient(user: CurrentUser) -> tuple[RecipientType, uuid.UUID]:
    if user.role is Role.DEALER_STAFF:
        employee = user.require_employee()
        return RecipientType.EMPLOYEE, employee.id
    customer = user.require_customer()
    return RecipientType.CUSTOMER, customer.id


@router.get(
    "/notifications",
    response_model=NotificationListOut,
    summary="Notifications for the caller (unread first)",
)
async def list_notifications(
    session: SessionDep,
    user: AnyUserDep,
    unread_only: Annotated[bool, Query()] = False,
    since_id: Annotated[
        uuid.UUID | None, Query(description="Reserved for cursor paging")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NotificationListOut:
    recipient_type, recipient_id = _recipient(user)

    stmt = select(Notification).where(
        Notification.recipient_type == recipient_type,
        Notification.recipient_id == recipient_id,
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))

    # Unread first, then newest — matches how the app renders the list.
    stmt = stmt.order_by(
        Notification.is_read.asc(), Notification.created_at.desc()
    ).limit(limit)

    rows = list((await session.execute(stmt)).scalars())

    unread_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.recipient_type == recipient_type,
                    Notification.recipient_id == recipient_id,
                    Notification.is_read.is_(False),
                )
            )
        ).scalar_one()
    )

    return NotificationListOut(
        items=[NotificationOut.model_validate(r) for r in rows],
        unread_count=unread_count,
    )


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark one notification read",
)
async def mark_read(
    notification_id: uuid.UUID, session: SessionDep, user: AnyUserDep
) -> NotificationOut:
    recipient_type, recipient_id = _recipient(user)
    notification = await session.get(Notification, notification_id)
    if (
        notification is None
        or notification.recipient_type != recipient_type
        or notification.recipient_id != recipient_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )

    notification.is_read = True
    await session.commit()
    await session.refresh(notification)
    return NotificationOut.model_validate(notification)


@router.patch(
    "/notifications/read-all",
    response_model=Message,
    summary="Mark every notification read",
)
async def mark_all_read(session: SessionDep, user: AnyUserDep) -> Message:
    recipient_type, recipient_id = _recipient(user)
    result = await session.execute(
        update(Notification)
        .where(
            Notification.recipient_type == recipient_type,
            Notification.recipient_id == recipient_id,
            Notification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    await session.commit()
    return Message(detail=f"Marked {result.rowcount or 0} notification(s) read")
