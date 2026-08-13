"""Dealers, employees (dealer sales staff), and customers."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Dealer(Base, TimestampMixin):
    __tablename__ = "dealers"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(400))
    phone: Mapped[str | None] = mapped_column(String(20))
    pincode: Mapped[str | None] = mapped_column(String(10), index=True)

    # Round-robin pointer for auto-assignment. Nullable: a fresh dealer has no
    # pointer yet, and the pointer may reference a since-deactivated employee
    # (the rotation is self-healing — see services/assignment.py).
    last_assigned_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    employees: Mapped[list["Employee"]] = relationship(
        back_populates="dealer",
        foreign_keys="Employee.dealer_id",
        cascade="all, delete-orphan",
    )


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = uuid_pk()
    dealer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealers.id", ondelete="CASCADE"), nullable=False
    )
    # Cognito `sub`. Nullable so a dealer can seed a roster before the Cognito
    # user is provisioned; unique when present.
    cognito_sub: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # `created_at` (from TimestampMixin) is the round-robin rotation order the
    # assignment algorithm sorts on - see services/assignment.py.

    dealer: Mapped["Dealer"] = relationship(
        back_populates="employees", foreign_keys=[dealer_id]
    )


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = uuid_pk()
    cognito_sub: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    onboarding_dealer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealers.id", ondelete="SET NULL")
    )

    dealer: Mapped["Dealer | None"] = relationship(foreign_keys=[onboarding_dealer_id])
