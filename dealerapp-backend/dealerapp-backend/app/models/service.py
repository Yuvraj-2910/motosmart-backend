"""Phase 3: service-request threads and the chatbot transcript."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TimestampsMixin, uuid_pk
from app.models.enums import ServiceRequestStatus


class ServiceRequest(Base, TimestampsMixin):
    __tablename__ = "service_requests"
    __table_args__ = (
        Index("ix_service_requests_dealer_status", "dealer_id", "status"),
        Index("ix_service_requests_customer", "customer_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    dealer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealers.id", ondelete="SET NULL")
    )
    type: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ServiceRequestStatus.OPEN, server_default="OPEN"
    )
    preferred_date: Mapped[date | None] = mapped_column(Date)

    # Who closed it, and when. There is no assignee on a service request — the
    # branch's whole desk is notified — so this is the only record of which
    # employee earned the closing incentive.
    resolved_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # AI triage of the customer's own description, filled in on creation. Nullable
    # because classification is best-effort - a ticket is never blocked on it.
    ai_category: Mapped[str | None] = mapped_column(String(30))
    ai_priority: Mapped[str | None] = mapped_column(String(20))
    ai_summary: Mapped[str | None] = mapped_column(Text)

    messages: Mapped[list["ServiceRequestMessage"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ServiceRequestMessage.created_at",
    )


class ServiceRequestMessage(Base, TimestampMixin):
    __tablename__ = "service_request_messages"
    __table_args__ = (Index("ix_service_messages_request", "service_request_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Polymorphic over sender_type (employee id or customer id) — no FK.
    sender_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    message: Mapped[str] = mapped_column(Text, nullable=False)

    request: Mapped["ServiceRequest"] = relationship(back_populates="messages")


class ChatbotConversation(Base):
    __tablename__ = "chatbot_conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    messages: Mapped[list["ChatbotMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatbotMessage.created_at",
    )


class ChatbotMessage(Base, TimestampMixin):
    __tablename__ = "chatbot_messages"
    __table_args__ = (Index("ix_chatbot_messages_conv", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chatbot_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    conversation: Mapped["ChatbotConversation"] = relationship(back_populates="messages")
