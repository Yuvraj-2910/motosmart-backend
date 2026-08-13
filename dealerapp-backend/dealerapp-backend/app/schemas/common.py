"""Shared schema building blocks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    """Base for response models read straight off an ORM instance."""

    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class Warning_(BaseModel):
    """A non-blocking advisory returned alongside a successful write."""

    code: str
    message: str


class HealthResponse(BaseModel):
    status: str = "ok"
    environment: str
    database: str = Field(description="'up' or 'down'")
