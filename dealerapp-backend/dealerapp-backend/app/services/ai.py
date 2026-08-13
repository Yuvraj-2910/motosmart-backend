"""Bedrock-backed AI features.

Every function here is **stub-friendly**: if Bedrock is unconfigured, throttled,
or erroring, we fall back to a deterministic heuristic and report
`source="fallback"`. The demo must never break because of an AI call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError

from app.core import aws
from app.core.config import settings
from app.models.enums import AiIntent

logger = logging.getLogger(__name__)

Source = Literal["bedrock", "fallback"]

CLASSIFY_SYSTEM = (
    "You are a sales-intent classifier for a Yamaha two-wheeler dealership in India. "
    "Given a salesperson's notes about an enquiry and the customer's tentative purchase "
    "date, classify buying intent as exactly one of HOT, WARM, or COLD.\n"
    "HOT: ready to buy within ~2 weeks, has finance/exchange sorted, asking about "
    "delivery, booking amount, or on-road price.\n"
    "WARM: genuinely interested but comparing models, waiting on funds, or buying in "
    "1-3 months.\n"
    "COLD: just browsing, no timeline, price-shopping only, or unresponsive.\n"
    "Reply with the single word only. No punctuation, no explanation."
)

CHAT_SYSTEM = (
    "You are the Yamaha Smart Dealer assistant, helping a Yamaha two-wheeler owner in "
    "India. Be concise, friendly, and practical. You can help with service schedules, "
    "basic troubleshooting, warranty and documentation questions, riding tips, and "
    "explaining the owner's vehicle data. "
    "Rules: keep answers under 120 words unless asked for detail; use INR for money; "
    "never invent a specific price, part number, dealer commitment, or service date - "
    "instead tell the owner to confirm with their dealer; for anything safety-critical "
    "(brakes, steering, fuel leaks, electrical burning smell) tell them to stop riding "
    "and contact the dealer immediately. If asked something outside Yamaha ownership, "
    "politely redirect."
)


@dataclass
class ClassificationResult:
    intent: AiIntent
    source: Source
    rationale: str | None = None


@dataclass
class ChatResult:
    content: str
    source: Source


def _bedrock_ready() -> bool:
    return bool(settings.BEDROCK_ENABLED and settings.BEDROCK_MODEL_ID)


async def _invoke(
    *,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    """Invoke Bedrock and return the concatenated text output.

    Raises on failure — callers decide the fallback.
    """
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens or settings.BEDROCK_MAX_TOKENS,
        "temperature": temperature,
        "system": system,
        "messages": messages,
    }
    client = aws.bedrock_client()
    response = await aws.call(
        client.invoke_model,
        modelId=settings.BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    payload = json.loads(response["body"].read())
    parts = [
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    ]
    return "".join(parts).strip()


# --- Lead intent ----------------------------------------------------------

_HOT_HINTS = (
    "book", "booking", "finance approved", "loan approved", "downpayment",
    "down payment", "ready to buy", "buy today", "delivery", "on-road price",
    "onroad", "emi approved", "confirm", "advance paid", "token",
)
_COLD_HINTS = (
    "just looking", "just browsing", "no response", "not interested",
    "next year", "maybe later", "window shopping", "enquiry only",
    "asked price only", "unreachable", "wrong number",
)


def _heuristic_intent(notes: str | None, tentative_date: date | None) -> AiIntent:
    """Deterministic fallback. Recency of intent beats keyword noise."""
    if tentative_date is not None:
        days = (tentative_date - date.today()).days
        if days <= 14:
            return AiIntent.HOT
        if days <= 60:
            return AiIntent.WARM

    text = (notes or "").lower()
    if any(h in text for h in _HOT_HINTS):
        return AiIntent.HOT
    if any(h in text for h in _COLD_HINTS):
        return AiIntent.COLD

    # Plan mandates WARM as the safe default so nothing renders empty.
    return AiIntent.WARM


def _parse_intent(raw: str) -> AiIntent | None:
    """Parse defensively — the model may add stray words or punctuation."""
    match = re.search(r"\b(HOT|WARM|COLD)\b", raw.upper())
    if match:
        return AiIntent(match.group(1))
    return None


async def classify_lead(
    notes: str | None = None,
    tentative_date: date | None = None,
    *,
    customer_name: str | None = None,
    interested_model: str | None = None,
    current_bike: str | None = None,
) -> ClassificationResult:
    """Classify buying intent as HOT / WARM / COLD."""
    fallback = _heuristic_intent(notes, tentative_date)

    if not _bedrock_ready():
        logger.debug("Bedrock disabled or unconfigured; using heuristic intent")
        return ClassificationResult(fallback, "fallback", "Bedrock not configured")

    if not notes and tentative_date is None:
        return ClassificationResult(fallback, "fallback", "Not enough signal to classify")

    facts = [
        f"Notes: {notes or 'none recorded'}",
        f"Tentative purchase date: {tentative_date.isoformat() if tentative_date else 'not given'}",
        f"Today's date: {date.today().isoformat()}",
    ]
    if interested_model:
        facts.append(f"Interested model: {interested_model}")
    if current_bike:
        facts.append(f"Current bike: {current_bike}")
    if customer_name:
        facts.append(f"Customer: {customer_name}")

    try:
        raw = await _invoke(
            system=CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(facts)}],
            max_tokens=8,
            temperature=0.0,
        )
    except (ClientError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Bedrock classify_lead failed (%s); falling back", exc)
        return ClassificationResult(fallback, "fallback", f"Bedrock unavailable: {type(exc).__name__}")

    intent = _parse_intent(raw)
    if intent is None:
        logger.warning("Unparseable Bedrock intent response: %r", raw[:200])
        return ClassificationResult(fallback, "fallback", "Unparseable model response")

    return ClassificationResult(intent, "bedrock", None)


# --- Chatbot --------------------------------------------------------------

FALLBACK_CHAT_REPLY = (
    "I can't reach the assistant service right now. For service bookings, spare parts, "
    "or anything urgent, please raise a service request in the app or call your dealer "
    "directly - they'll be able to help straight away."
)


async def chat(
    messages: list[dict[str, str]],
    *,
    context: str | None = None,
) -> ChatResult:
    """Run a chatbot turn.

    `messages` is the full transcript as `[{"role": "user"|"assistant",
    "content": ...}]`; Bedrock is stateless, so history is resent each call.
    """
    if not _bedrock_ready():
        return ChatResult(FALLBACK_CHAT_REPLY, "fallback")

    normalised: list[dict[str, Any]] = []
    for m in messages:
        role = "assistant" if str(m.get("role", "")).lower() == "assistant" else "user"
        content = (m.get("content") or "").strip()
        if content:
            normalised.append({"role": role, "content": content})

    if not normalised:
        return ChatResult(FALLBACK_CHAT_REPLY, "fallback")

    # The Anthropic messages API requires the transcript to start with a user
    # turn and to alternate; collapse any consecutive same-role turns.
    while normalised and normalised[0]["role"] != "user":
        normalised.pop(0)
    collapsed: list[dict[str, Any]] = []
    for m in normalised:
        if collapsed and collapsed[-1]["role"] == m["role"]:
            collapsed[-1]["content"] += "\n\n" + m["content"]
        else:
            collapsed.append(m)

    system = CHAT_SYSTEM
    if context:
        system = f"{CHAT_SYSTEM}\n\nContext about this owner's vehicle:\n{context}"

    try:
        reply = await _invoke(
            system=system, messages=collapsed, max_tokens=600, temperature=0.4
        )
    except (ClientError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Bedrock chat failed (%s); returning fallback reply", exc)
        return ChatResult(FALLBACK_CHAT_REPLY, "fallback")

    return ChatResult(reply or FALLBACK_CHAT_REPLY, "bedrock" if reply else "fallback")


# --- Optional helpers used by the dealer UI -------------------------------


async def suggest_followup(notes: str | None, status: str) -> tuple[str, Source]:
    """Suggest the salesperson's next action."""
    default = "Call the customer to confirm interest and agree a showroom visit."
    if not _bedrock_ready() or not notes:
        return default, "fallback"
    try:
        reply = await _invoke(
            system=(
                "You advise Yamaha dealership sales staff. Given enquiry notes and the "
                "lead status, reply with ONE next action in under 20 words. Imperative "
                "mood, no preamble."
            ),
            messages=[{"role": "user", "content": f"Status: {status}\nNotes: {notes}"}],
            max_tokens=60,
            temperature=0.3,
        )
    except (ClientError, BotoCoreError, KeyError, ValueError) as exc:
        logger.warning("Bedrock suggest_followup failed (%s)", exc)
        return default, "fallback"
    return (reply or default), ("bedrock" if reply else "fallback")


async def summarise_notes(notes: str) -> tuple[str, Source]:
    """One-line summary of a long note trail."""
    if not _bedrock_ready() or not notes:
        return (notes or "")[:200], "fallback"
    try:
        reply = await _invoke(
            system=(
                "Summarise these Yamaha dealership enquiry notes in one sentence under "
                "25 words. Keep concrete details (model, budget, timeline)."
            ),
            messages=[{"role": "user", "content": notes}],
            max_tokens=80,
            temperature=0.2,
        )
    except (ClientError, BotoCoreError, KeyError, ValueError) as exc:
        logger.warning("Bedrock summarise_notes failed (%s)", exc)
        return notes[:200], "fallback"
    return (reply or notes[:200]), ("bedrock" if reply else "fallback")
