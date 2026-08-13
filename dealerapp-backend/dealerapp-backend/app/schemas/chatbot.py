"""Chatbot schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ChatRole
from app.schemas.common import ORMModel


class ChatMessageOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: ChatRole
    content: str
    created_at: datetime


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Omit to continue the customer's latest conversation."
    )


class ChatSendResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    source: str = Field(description="'bedrock' or 'fallback'")


class ChatHistoryOut(BaseModel):
    conversation_id: uuid.UUID | None = None
    messages: list[ChatMessageOut] = Field(default_factory=list)
