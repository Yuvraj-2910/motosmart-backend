"""AI endpoints backed by Bedrock (with deterministic fallbacks)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.config import settings
from app.deps import DealerUserDep, SessionDep
from app.models.lead import Lead
from app.schemas.lead import ClassifyLeadRequest, ClassifyLeadResponse
from app.schemas.voice import TranscribeResponse
from app.services import ai as ai_service
from app.services import voice

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post(
    "/classify-lead",
    response_model=ClassifyLeadResponse,
    summary="Classify buying intent as HOT / WARM / COLD",
)
async def classify_lead(
    payload: ClassifyLeadRequest,
    session: SessionDep,
    user: DealerUserDep,
) -> ClassifyLeadResponse:
    """Pass a `lead_id` to classify and persist, or raw notes for a dry run.

    Never fails on Bedrock trouble: it falls back to a heuristic and reports
    `source="fallback"` so the app can still render a badge.
    """
    lead: Lead | None = None

    if payload.lead_id is not None:
        lead = await session.get(Lead, payload.lead_id)
        if lead is None or lead.dealer_id != user.require_dealer_id():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
            )

    notes = payload.notes if payload.notes is not None else (lead.notes if lead else None)
    tentative = payload.tentative_purchase_date or (
        lead.tentative_purchase_date if lead else None
    )

    if notes is None and tentative is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a lead_id, notes, or a tentative_purchase_date to classify.",
        )

    result = await ai_service.classify_lead(
        notes=notes,
        tentative_date=tentative,
        customer_name=lead.customer_name if lead else None,
        current_bike=lead.current_bike if lead else None,
    )

    persisted = False
    if lead is not None:
        lead.ai_intent = result.intent
        await session.commit()
        persisted = True

    return ClassifyLeadResponse(
        intent=result.intent,
        lead_id=lead.id if lead else None,
        persisted=persisted,
        source=result.source,
        rationale=result.rationale,
    )


@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe a short voice note to text (mic dictation for lead/follow-up notes)",
)
async def transcribe_audio(
    user: DealerUserDep,
    audio: Annotated[UploadFile, File(description="Short clip: webm/wav/m4a/mp3/ogg/flac")],
    language_code: Annotated[
        str | None,
        Form(description="Overrides TRANSCRIBE_LANGUAGE_CODE, e.g. 'hi-IN'"),
    ] = None,
) -> TranscribeResponse:
    """Round trip is a batch Transcribe job, so this can take several seconds -
    the app should show a spinner rather than treat it as instant."""
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty audio file")

    max_bytes = settings.TRANSCRIBE_MAX_AUDIO_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio exceeds {settings.TRANSCRIBE_MAX_AUDIO_MB}MB limit",
        )

    try:
        result = await voice.transcribe_audio(
            data=data,
            content_type=audio.content_type or "",
            filename=audio.filename or "voice-note",
            language_code=language_code,
        )
    except voice.TranscribeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return TranscribeResponse(
        text=result.text, language_code=result.language_code, source=result.source
    )
