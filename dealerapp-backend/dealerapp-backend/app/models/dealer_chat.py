"""Dealer-side chatbot: threads scoped to a staff member, not a customer.

Mirrors `ChatbotConversation`/`ChatbotMessage` in `app/models/service.py`, but
keyed on `employee_id` — a dealer's assistant conversation is theirs, the way
the owner's assistant conversation belongs to a `customer_id`. Kept as a
separate table rather than a nullable `customer_id`/`employee_id` pair on the
existing tables so the two chat surfaces (rider support vs. dealer ops) never
share rows or transcripts.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class DealerChatbotConversation(Base):
    __tablename__ = "dealer_chatbot_conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    messages: Mapped[list["DealerChatbotMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="DealerChatbotMessage.created_at",
    )


class DealerChatbotMessage(Base, TimestampMixin):
    __tablename__ = "dealer_chatbot_messages"
    __table_args__ = (
        Index("ix_dealer_chatbot_messages_conv", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("dealer_chatbot_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    conversation: Mapped["DealerChatbotConversation"] = relationship(back_populates="messages")
