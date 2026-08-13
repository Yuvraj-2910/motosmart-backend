"""Bike catalog (Phase 1) and exchange-value reference data (Phase 2)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import StockStatus


class BikeModel(Base, TimestampMixin):
    __tablename__ = "bike_models"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    variant: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[str | None] = mapped_column(String(60), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    engine_cc: Mapped[int | None] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(String(500))
    brochure_url: Mapped[str | None] = mapped_column(String(500))
    stock_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=StockStatus.IN_STOCK, server_default="IN_STOCK"
    )
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class ExchangeValue(Base, TimestampMixin):
    __tablename__ = "exchange_values"

    id: Mapped[uuid.UUID] = uuid_pk()
    brand: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    base_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Multipliers keyed by condition, e.g. {"EXCELLENT": 1.0, "GOOD": 0.88, ...}
    condition_factor_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
