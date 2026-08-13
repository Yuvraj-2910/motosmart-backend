"""Bike catalog schemas."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import StockStatus
from app.schemas.common import ORMModel


class BikeModelOut(ORMModel):
    id: uuid.UUID
    name: str
    variant: str | None = None
    category: str | None = None
    price: Decimal
    engine_cc: int | None = None
    image_url: str | None = None
    brochure_url: str | None = None
    stock_status: StockStatus
    is_available: bool


class AvailabilityOut(BaseModel):
    bike_model_id: uuid.UUID
    stock_status: StockStatus
    is_available: bool
    dealers_with_stock: int
