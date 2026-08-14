"""Voice-note transcription (dictation for lead/follow-up notes)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TranscribeResponse(BaseModel):
    text: str
    language_code: str | None = None
    source: str = Field(description="'transcribe' or 'unavailable'")
