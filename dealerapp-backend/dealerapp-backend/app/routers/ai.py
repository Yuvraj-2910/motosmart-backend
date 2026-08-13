"""AI endpoints backed by Bedrock (with deterministic fallbacks)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.deps import DealerUserDep, SessionDep
from app.models.lead import Lead
from app.schemas.lead import ClassifyLeadRequest, ClassifyLeadResponse
from app.services import ai as ai_service

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
