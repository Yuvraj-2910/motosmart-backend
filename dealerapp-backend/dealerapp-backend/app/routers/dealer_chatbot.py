"""Bedrock-backed dealer assistant — leads and tickets context.

Mirrors `app/routers/chatbot.py` (the customer assistant): same send/history
shape, same fallback-on-Bedrock-failure discipline, same one-conversation-per-
principal default. The difference is what "context" means — here it's a
snapshot of the caller's branch pipeline (`dealer_chat_context`) rather than
one vehicle's service state, and conversations are keyed on `employee_id`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DealerUserDep, SessionDep
from app.models.enums import ChatRole
from app.models.dealer_chat import DealerChatbotConversation, DealerChatbotMessage
from app.schemas.dealer_chatbot import (
    DealerChatHistoryOut,
    DealerChatMessageOut,
    DealerChatSendRequest,
    DealerChatSendResponse,
)
from app.services import ai as ai_service
from app.services.dealer_chat_context import build_dealer_context

router = APIRouter(prefix="/dealer/chatbot", tags=["dealer-chatbot"])

# How much prior transcript to resend. Bedrock is stateless, so history is the
# only memory the assistant has; cap it to keep latency and cost predictable.
HISTORY_TURNS = 12


async def _resolve_conversation(
    session: AsyncSession, employee_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> DealerChatbotConversation:
    if conversation_id is not None:
        conversation = await session.get(DealerChatbotConversation, conversation_id)
        if conversation is None or conversation.employee_id != employee_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        return conversation

    existing = (
        await session.execute(
            select(DealerChatbotConversation)
            .where(DealerChatbotConversation.employee_id == employee_id)
            .order_by(DealerChatbotConversation.started_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if existing is not None:
        return existing

    conversation = DealerChatbotConversation(employee_id=employee_id)
    session.add(conversation)
    await session.flush()
    return conversation


@router.post("/message", response_model=DealerChatSendResponse, summary="Send a dealer chatbot message")
async def send_message(
    payload: DealerChatSendRequest, session: SessionDep, user: DealerUserDep
) -> DealerChatSendResponse:
    """Persists both turns. Falls back to a canned reply if Bedrock is down."""
    employee = user.require_employee()
    dealer_id = user.require_dealer_id()
    conversation = await _resolve_conversation(session, employee.id, payload.conversation_id)

    history = list(
        (
            await session.execute(
                select(DealerChatbotMessage)
                .where(DealerChatbotMessage.conversation_id == conversation.id)
                .order_by(DealerChatbotMessage.created_at.desc())
                .limit(HISTORY_TURNS)
            )
        ).scalars()
    )
    history.reverse()

    user_message = DealerChatbotMessage(
        conversation_id=conversation.id, role=ChatRole.USER, content=payload.message
    )
    session.add(user_message)
    await session.flush()

    transcript = [
        {"role": "assistant" if m.role == ChatRole.ASSISTANT else "user", "content": m.content}
        for m in history
    ]
    transcript.append({"role": "user", "content": payload.message})

    context = await build_dealer_context(session, dealer_id=dealer_id, message=payload.message)
    result = await ai_service.dealer_chat(transcript, context=context)

    assistant_message = DealerChatbotMessage(
        conversation_id=conversation.id, role=ChatRole.ASSISTANT, content=result.content
    )
    session.add(assistant_message)

    await session.commit()
    await session.refresh(user_message)
    await session.refresh(assistant_message)

    return DealerChatSendResponse(
        conversation_id=conversation.id,
        user_message=DealerChatMessageOut.model_validate(user_message),
        assistant_message=DealerChatMessageOut.model_validate(assistant_message),
        source=result.source,
    )


@router.get("/history", response_model=DealerChatHistoryOut, summary="Dealer chatbot transcript")
async def history(
    session: SessionDep,
    user: DealerUserDep,
    conversation_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> DealerChatHistoryOut:
    employee = user.require_employee()

    if conversation_id is not None:
        conversation = await session.get(DealerChatbotConversation, conversation_id)
        if conversation is None or conversation.employee_id != employee.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
    else:
        conversation = (
            await session.execute(
                select(DealerChatbotConversation)
                .where(DealerChatbotConversation.employee_id == employee.id)
                .order_by(DealerChatbotConversation.started_at.desc())
                .limit(1)
            )
        ).scalars().first()

    if conversation is None:
        return DealerChatHistoryOut(conversation_id=None, messages=[])

    rows = (
        await session.execute(
            select(DealerChatbotMessage)
            .where(DealerChatbotMessage.conversation_id == conversation.id)
            .order_by(DealerChatbotMessage.created_at)
            .limit(limit)
        )
    ).scalars()

    return DealerChatHistoryOut(
        conversation_id=conversation.id,
        messages=[DealerChatMessageOut.model_validate(r) for r in rows],
    )
