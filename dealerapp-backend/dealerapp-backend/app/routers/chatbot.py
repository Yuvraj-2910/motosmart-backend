"""Bedrock-backed customer assistant."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CustomerUserDep, SessionDep
from app.models.catalog import BikeModel
from app.models.enums import ChatRole
from app.models.service import ChatbotConversation, ChatbotMessage
from app.models.vehicle import Vehicle
from app.schemas.chatbot import (
    ChatHistoryOut,
    ChatMessageOut,
    ChatSendRequest,
    ChatSendResponse,
)
from app.services import ai as ai_service
from app.services import vehicles as vehicle_service

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

# How much prior transcript to resend. Bedrock is stateless, so history is the
# only memory the assistant has; cap it to keep latency and cost predictable.
HISTORY_TURNS = 12


async def _vehicle_context(session: AsyncSession, customer_id: uuid.UUID) -> str | None:
    """Give the assistant the owner's actual vehicle and service state.

    Without this it can only answer generically, and the whole point is that it
    knows when their service is due.
    """
    vehicle = (
        await session.execute(
            select(Vehicle)
            .where(Vehicle.customer_id == customer_id)
            .order_by(Vehicle.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if vehicle is None:
        return None

    lines: list[str] = []
    if vehicle.bike_model_id:
        model = await session.get(BikeModel, vehicle.bike_model_id)
        if model is not None:
            lines.append(f"Model: {model.name} {model.variant or ''}".strip())
    if vehicle.registration_no:
        lines.append(f"Registration: {vehicle.registration_no}")
    lines.append(f"Odometer: {vehicle.odometer_km} km")
    if vehicle.purchase_date:
        lines.append(f"Purchased: {vehicle.purchase_date.isoformat()}")

    status_out = await vehicle_service.build_service_status(session, vehicle)
    lines.append(f"Service status: {status_out.status} - {status_out.message}")

    return "\n".join(lines)


async def _resolve_conversation(
    session: AsyncSession, customer_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> ChatbotConversation:
    if conversation_id is not None:
        conversation = await session.get(ChatbotConversation, conversation_id)
        if conversation is None or conversation.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        return conversation

    existing = (
        await session.execute(
            select(ChatbotConversation)
            .where(ChatbotConversation.customer_id == customer_id)
            .order_by(ChatbotConversation.started_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if existing is not None:
        return existing

    conversation = ChatbotConversation(customer_id=customer_id)
    session.add(conversation)
    await session.flush()
    return conversation


@router.post("/message", response_model=ChatSendResponse, summary="Send a chatbot message")
async def send_message(
    payload: ChatSendRequest, session: SessionDep, user: CustomerUserDep
) -> ChatSendResponse:
    """Persists both turns. Falls back to a canned reply if Bedrock is down."""
    customer = user.require_customer()
    conversation = await _resolve_conversation(session, customer.id, payload.conversation_id)

    history = list(
        (
            await session.execute(
                select(ChatbotMessage)
                .where(ChatbotMessage.conversation_id == conversation.id)
                .order_by(ChatbotMessage.created_at.desc())
                .limit(HISTORY_TURNS)
            )
        ).scalars()
    )
    history.reverse()

    user_message = ChatbotMessage(
        conversation_id=conversation.id, role=ChatRole.USER, content=payload.message
    )
    session.add(user_message)
    await session.flush()

    transcript = [
        {"role": "assistant" if m.role == ChatRole.ASSISTANT else "user", "content": m.content}
        for m in history
    ]
    transcript.append({"role": "user", "content": payload.message})

    context = await _vehicle_context(session, customer.id)
    result = await ai_service.chat(transcript, context=context)

    assistant_message = ChatbotMessage(
        conversation_id=conversation.id, role=ChatRole.ASSISTANT, content=result.content
    )
    session.add(assistant_message)

    await session.commit()
    await session.refresh(user_message)
    await session.refresh(assistant_message)

    return ChatSendResponse(
        conversation_id=conversation.id,
        user_message=ChatMessageOut.model_validate(user_message),
        assistant_message=ChatMessageOut.model_validate(assistant_message),
        source=result.source,
    )


@router.get("/history", response_model=ChatHistoryOut, summary="Chatbot transcript")
async def history(
    session: SessionDep,
    user: CustomerUserDep,
    conversation_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ChatHistoryOut:
    customer = user.require_customer()

    if conversation_id is not None:
        conversation = await session.get(ChatbotConversation, conversation_id)
        if conversation is None or conversation.customer_id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
    else:
        conversation = (
            await session.execute(
                select(ChatbotConversation)
                .where(ChatbotConversation.customer_id == customer.id)
                .order_by(ChatbotConversation.started_at.desc())
                .limit(1)
            )
        ).scalars().first()

    if conversation is None:
        return ChatHistoryOut(conversation_id=None, messages=[])

    rows = (
        await session.execute(
            select(ChatbotMessage)
            .where(ChatbotMessage.conversation_id == conversation.id)
            .order_by(ChatbotMessage.created_at)
            .limit(limit)
        )
    ).scalars()

    return ChatHistoryOut(
        conversation_id=conversation.id,
        messages=[ChatMessageOut.model_validate(r) for r in rows],
    )
