"""Dealer chatbot schemas — mirrors `app/schemas/chatbot.py` for the staff side."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ChatRole
from app.schemas.common import ORMModel


class DealerChatMessageOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: ChatRole
    content: str
    created_at: datetime


class DealerChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Omit to continue the dealer's latest conversation."
    )


class DealerChatSendResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message: DealerChatMessageOut
    assistant_message: DealerChatMessageOut
    source: str = Field(description="'bedrock' or 'fallback'")


class DealerChatHistoryOut(BaseModel):
    conversation_id: uuid.UUID | None = None
    messages: list[DealerChatMessageOut] = Field(default_factory=list)
