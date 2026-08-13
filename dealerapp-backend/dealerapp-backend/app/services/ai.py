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
from app.models.enums import AiIntent, TicketCategory, TicketPriority

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

TRIAGE_SYSTEM = (
    "You triage service requests for a Yamaha two-wheeler dealership in India. "
    "The rider describes a problem in their own words; you classify it.\n"
    "Reply with STRICT JSON only, no markdown fence, exactly these keys:\n"
    '{"category": "...", "priority": "...", "summary": "..."}\n'
    "category is one of: ENGINE, BRAKES, ELECTRICAL, TRANSMISSION, SUSPENSION, "
    "TYRES, BODY, PERIODIC_SERVICE, OTHER.\n"
    "priority is one of: URGENT, HIGH, NORMAL, LOW.\n"
    "URGENT means the rider should stop riding now - brake failure, steering play, "
    "fuel leak, burning smell, smoke, or anything that could cause a crash. "
    "HIGH means the bike is unsafe to ride far or is undrivable. NORMAL is a "
    "genuine fault that can wait a few days. LOW is routine service, cosmetics, "
    "or an enquiry.\n"
    "summary is one line under 15 words, written for the service desk, keeping "
    "concrete symptoms."
)

TELEMETRY_SYSTEM = (
    "You explain a Yamaha two-wheeler's live diagnostics to its owner - a rider, "
    "not a mechanic. You are given the latest sensor readings, statistics from the "
    "last minute of monitoring, and any active fault codes.\n"
    "Use the window statistics to talk about how the bike behaved over that minute "
    "(steady, climbing, spiking) rather than treating the numbers as one instant. "
    "Do not describe a trend the statistics do not support.\n"
    "Write 2-4 short sentences in plain language: what the numbers say overall, "
    "anything that looks off and why it matters, and one practical next step. "
    "Reference the actual figures you were given. Never invent a reading, a part "
    "number, a price, or a service date. If a fault code or reading is "
    "safety-critical (brakes, fuel, overheating, burning smell), say plainly to "
    "stop riding and contact the dealer. Otherwise stay calm and matter-of-fact. "
    "No markdown, no bullet points, no preamble."
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


@dataclass
class TriageResult:
    category: TicketCategory
    priority: TicketPriority
    summary: str
    source: Source


@dataclass
class TelemetrySummary:
    summary: str
    source: Source


def _bedrock_ready() -> bool:
    return bool(settings.BEDROCK_ENABLED and settings.BEDROCK_MODEL_ID)


# Newer models (Sonnet 5 among them) reject `temperature` outright rather than
# ignoring it. Rather than hardcoding which models those are - a list that would
# rot with every release - the first rejection is remembered here and the call
# retried without the parameter. Keyed by model ID so a config change re-probes.
_NO_TEMPERATURE_MODELS: set[str] = set()


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
    model_id = settings.BEDROCK_MODEL_ID

    def _body(*, with_temperature: bool) -> str:
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens or settings.BEDROCK_MAX_TOKENS,
            "system": system,
            "messages": messages,
        }
        if with_temperature:
            body["temperature"] = temperature
        return json.dumps(body)

    client = aws.bedrock_client()
    send_temperature = model_id not in _NO_TEMPERATURE_MODELS

    async def _call(*, with_temperature: bool) -> Any:
        return await aws.call(
            client.invoke_model,
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=_body(with_temperature=with_temperature),
        )

    try:
        response = await _call(with_temperature=send_temperature)
    except ClientError as exc:
        if not (send_temperature and _is_temperature_rejection(exc)):
            raise
        logger.info(
            "Model %s rejects 'temperature'; retrying without it and remembering",
            model_id,
        )
        _NO_TEMPERATURE_MODELS.add(model_id)
        response = await _call(with_temperature=False)

    payload = json.loads(response["body"].read())
    parts = [
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    ]
    return "".join(parts).strip()


def _is_temperature_rejection(exc: ClientError) -> bool:
    """True for the ValidationException raised when a model has dropped
    `temperature` (rather than any other validation problem, which must not be
    retried)."""
    error = exc.response.get("Error", {})
    if error.get("Code") != "ValidationException":
        return False
    return "temperature" in error.get("Message", "").lower()


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


# --- Service-request triage -----------------------------------------------

# Keyword table for the fallback. Order matters: the first category whose hints
# match wins, so the safety-critical ones are checked first.
_CATEGORY_HINTS: tuple[tuple[TicketCategory, tuple[str, ...]], ...] = (
    (TicketCategory.BRAKES, ("brake", "braking", "disc", "pad", "stopping")),
    (
        TicketCategory.ELECTRICAL,
        ("battery", "electrical", "wiring", "headlight", "indicator", "horn",
         "starter", "self start", "spark", "fuse", "short circuit", "burning smell"),
    ),
    (
        TicketCategory.ENGINE,
        # "petrol" is the everyday word in India; without it a fuel leak fell
        # through to OTHER even though the priority rules caught it as urgent.
        ("engine", "overheat", "heating", "smoke", "oil leak", "fuel leak",
         "petrol leak", "petrol smell", "fuel smell", "misfire", "knocking",
         "stalls", "stalling", "rpm", "coolant", "mileage"),
    ),
    (
        TicketCategory.TRANSMISSION,
        ("clutch", "gear", "gearbox", "chain", "transmission", "sprocket"),
    ),
    (TicketCategory.SUSPENSION, ("suspension", "shock", "fork", "handling", "steering")),
    (TicketCategory.TYRES, ("tyre", "tire", "puncture", "wheel", "alignment", "rim")),
    (TicketCategory.BODY, ("scratch", "dent", "panel", "mirror", "seat", "paint", "body")),
    (
        TicketCategory.PERIODIC_SERVICE,
        ("service", "servicing", "periodic", "check up", "checkup",
         "maintenance", "washing"),
    ),
)

# Checked *before* the fault categories, because routine work often names a
# component too: "change the engine oil" is periodic service, not an engine
# fault, and matching ENGINE first mis-filed it. These are deliberately specific
# phrases - the bare word "service" is not here, since "brake service" must still
# reach BRAKES.
_ROUTINE_HINTS = (
    "oil change", "change the oil", "change engine oil", "engine oil",
    "periodic service", "periodic maintenance", "service due", "routine service",
    "general service", "servicing due", "free service", "washing", "polish",
)

# Anything here means "stop riding" regardless of category.
_URGENT_HINTS = (
    "brake fail", "no brakes", "brake not working", "fuel leak", "petrol leak",
    "burning smell", "smoke", "fire", "steering", "accident", "cannot stop",
    "not stopping",
)
_HIGH_HINTS = (
    # "not start" as a substring also covers "not starting" / "will not start".
    "not start", "won't start", "wont start", "breakdown", "stuck", "towed",
    "overheat", "leak", "stalls", "dead battery", "puncture",
)
_LOW_HINTS = (
    "service due", "periodic", "oil change", "washing", "scratch", "enquiry",
    "quote", "price", "checkup", "check up", "general",
)


def heuristic_triage(type_: str | None, description: str | None) -> TriageResult:
    """Instant, deterministic triage.

    Public because ticket creation uses it inline: a Bedrock round trip costs
    several seconds, which is far too long to hold a customer on a spinner. The
    ticket is stored with this verdict immediately and refined in the background
    by [triage_service_request], so the dealer queue is never unsorted and the
    customer never waits.
    """
    return _heuristic_triage(type_, description)


def _heuristic_triage(type_: str | None, description: str | None) -> TriageResult:
    """Deterministic fallback so a ticket is always triaged."""
    text = f"{type_ or ''} {description or ''}".lower().strip()

    category = TicketCategory.OTHER
    # Routine work first: it frequently names a component ("engine oil") that
    # would otherwise be read as a fault in that component.
    if any(h in text for h in _ROUTINE_HINTS) and not any(
        h in text for h in _URGENT_HINTS
    ):
        category = TicketCategory.PERIODIC_SERVICE
    else:
        for candidate, hints in _CATEGORY_HINTS:
            if any(h in text for h in hints):
                category = candidate
                break

    if any(h in text for h in _URGENT_HINTS):
        priority = TicketPriority.URGENT
    elif category is TicketCategory.BRAKES:
        # Any brake complaint is at least HIGH - it is the one system where
        # guessing low would be dangerous.
        priority = TicketPriority.HIGH
    elif any(h in text for h in _HIGH_HINTS):
        priority = TicketPriority.HIGH
    elif category is TicketCategory.PERIODIC_SERVICE or any(h in text for h in _LOW_HINTS):
        priority = TicketPriority.LOW
    else:
        priority = TicketPriority.NORMAL

    raw_summary = (description or type_ or "Service request").strip()
    summary = raw_summary if len(raw_summary) <= 120 else raw_summary[:117] + "..."
    return TriageResult(category, priority, summary, "fallback")


def _coerce_enum(value: Any, options: type[TicketCategory] | type[TicketPriority]) -> Any:
    """Match a model's answer to an enum member, case-insensitively."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return options(candidate)
    except ValueError:
        return None


async def triage_service_request(
    *,
    type_: str | None,
    description: str | None,
    vehicle_label: str | None = None,
    odometer_km: int | None = None,
) -> TriageResult:
    """Classify a service request into a category + priority, with a one-liner.

    Never raises: on any trouble the keyword heuristic answers instead, so the
    dealer queue is always sorted and labelled.
    """
    fallback = _heuristic_triage(type_, description)

    if not _bedrock_ready():
        return fallback
    if not (description or type_):
        return fallback

    facts = [f"Reported issue type: {type_ or 'not given'}", f"Rider's description: {description or 'none'}"]
    if vehicle_label:
        facts.append(f"Vehicle: {vehicle_label}")
    if odometer_km is not None:
        facts.append(f"Odometer: {odometer_km} km")

    try:
        raw = await _invoke(
            system=TRIAGE_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(facts)}],
            max_tokens=200,
            temperature=0.0,
        )
        payload = json.loads(_strip_json_fence(raw))
    except (ClientError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Bedrock triage failed (%s); using heuristic", exc)
        return fallback

    category = _coerce_enum(payload.get("category"), TicketCategory)
    priority = _coerce_enum(payload.get("priority"), TicketPriority)
    summary = payload.get("summary")

    if category is None or priority is None:
        logger.warning("Unparseable triage response: %r", raw[:200])
        return fallback

    # Never let the model talk a brake complaint down below HIGH.
    if category is TicketCategory.BRAKES and priority in (
        TicketPriority.NORMAL,
        TicketPriority.LOW,
    ):
        priority = TicketPriority.HIGH

    return TriageResult(
        category=category,
        priority=priority,
        summary=(summary if isinstance(summary, str) and summary.strip() else fallback.summary)[:200],
        source="bedrock",
    )


def _strip_json_fence(raw: str) -> str:
    """Models sometimes wrap JSON in a ```json fence despite instructions."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Trim anything before/after the outermost object.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return text


DTC_SYSTEM = (
    "You explain a single diagnostic trouble code to the owner of a Yamaha "
    "two-wheeler in India - a rider, not a mechanic.\n"
    "Write 2-3 short sentences: what the code actually means in plain words, what "
    "the rider is likely to notice, and what to do now. Use the current readings "
    "for context when they are relevant. Do not repeat the raw code as the whole "
    "answer. Never invent a price, part number, or service date. If it is "
    "safety-critical, say plainly to stop riding and contact the dealer. "
    "No markdown, no preamble."
)


async def explain_dtc(
    *,
    dtc_code: str,
    technical_description: str | None,
    readings: dict[str, Any],
) -> tuple[str, Source]:
    """Rider-facing explanation of one fault code.

    Exists so the OBD dashboard's alert card can show a real explanation without
    an AI key inside the app - the model call happens here, on the server.
    """
    fallback = (
        f"{technical_description}. Please get this checked at your Yamaha dealer."
        if technical_description
        else f"Fault code {dtc_code} is active. Please get it checked at your Yamaha dealer."
    )

    if not _bedrock_ready():
        return fallback, "fallback"

    facts = [f"Diagnostic code: {dtc_code}"]
    if technical_description:
        facts.append(f"Technical meaning: {technical_description}")
    for label, key, suffix in (
        ("Engine speed", "rpm", " rpm"),
        ("Coolant", "coolant_temp_c", " C"),
        ("Road speed", "speed_kph", " km/h"),
        ("Battery", "battery_voltage", " V"),
        ("Throttle", "throttle_position_pct", " %"),
        ("Fuel level", "fuel_level_pct", " %"),
    ):
        value = readings.get(key)
        if value is not None:
            facts.append(f"{label}: {value}{suffix}")

    try:
        reply = await _invoke(
            system=DTC_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(facts)}],
            max_tokens=260,
            temperature=0.3,
        )
    except (ClientError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Bedrock explain_dtc failed (%s); using fallback", exc)
        return fallback, "fallback"

    return (reply or fallback), ("bedrock" if reply else "fallback")


# --- Telemetry summary ----------------------------------------------------


_WINDOW_FIELDS = (
    ("rpm", "engine speed", "rpm", 0),
    ("coolant_temp_c", "coolant", "C", 0),
    ("speed_kph", "road speed", "km/h", 0),
    ("battery_voltage", "battery", "V", 2),
    ("throttle_position_pct", "throttle", "%", 0),
    ("fuel_level_pct", "fuel", "%", 0),
)


def summarise_window(samples: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Min / max / average / change for each field across the buffered window.

    Computed here rather than sent to the model as hundreds of raw samples: the
    statistics are what the summary actually needs, and they are deterministic, so
    the model cannot invent a trend that the numbers do not show.

    `change` is last minus first, using the sample ordering the caller provides
    (oldest first).
    """
    stats: dict[str, dict[str, float]] = {}
    for key, _label, _unit, _places in _WINDOW_FIELDS:
        values = [
            float(s[key])
            for s in samples
            if s.get(key) is not None
        ]
        if not values:
            continue
        stats[key] = {
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "change": values[-1] - values[0],
        }
    return stats


def _format_window(stats: dict[str, dict[str, float]], window_seconds: int | None) -> list[str]:
    if not stats:
        return []
    span = f"last {window_seconds}s" if window_seconds else "monitoring window"
    lines = [f"Statistics over the {span}:"]
    for key, label, unit, places in _WINDOW_FIELDS:
        entry = stats.get(key)
        if entry is None:
            continue
        lines.append(
            f"- {label}: min {entry['min']:.{places}f}{unit}, "
            f"max {entry['max']:.{places}f}{unit}, "
            f"avg {entry['avg']:.{places}f}{unit}, "
            f"net change {entry['change']:+.{places}f}{unit}"
        )
    return lines


def _heuristic_telemetry_summary(readings: dict[str, Any]) -> str:
    """Rule-based summary, used when Bedrock is unavailable.

    Deliberately states the same facts the AI version would, so the card never
    renders empty and never contradicts the gauges.
    """
    notes: list[str] = []
    warnings: list[str] = []

    rpm = readings.get("rpm")
    coolant = readings.get("coolant_temp_c")
    battery = readings.get("battery_voltage")
    speed = readings.get("speed_kph")
    fuel = readings.get("fuel_level_pct")
    throttle = readings.get("throttle_position_pct")
    dtcs = [c for c in (readings.get("dtc_codes") or []) if c]

    if rpm is not None:
        notes.append(f"engine at {int(rpm)} rpm")
    if speed is not None:
        notes.append(f"{float(speed):.0f} km/h")
    if coolant is not None:
        notes.append(f"coolant {float(coolant):.0f}°C")
        if float(coolant) >= 105:
            warnings.append("the engine is running hot")
    if battery is not None:
        notes.append(f"battery {float(battery):.2f}V")
        if float(battery) < 12.0:
            warnings.append("battery voltage is low")
        elif float(battery) > 15.0:
            warnings.append("charging voltage is high")
    if fuel is not None:
        notes.append(f"fuel {float(fuel):.0f}%")
        if float(fuel) < 15:
            warnings.append("fuel is low")
    if throttle is not None:
        notes.append(f"throttle {float(throttle):.0f}%")

    head = "Current readings: " + ", ".join(notes) + "." if notes else "No readings available."

    window = readings.get("window_stats") or {}
    trend_notes: list[str] = []
    coolant_stats = window.get("coolant_temp_c")
    if coolant_stats and abs(coolant_stats["change"]) >= 5:
        direction = "rose" if coolant_stats["change"] > 0 else "fell"
        trend_notes.append(f"coolant {direction} {abs(coolant_stats['change']):.0f}°C")
    rpm_stats = window.get("rpm")
    if rpm_stats:
        trend_notes.append(
            f"engine speed ranged {rpm_stats['min']:.0f}-{rpm_stats['max']:.0f} rpm"
        )
    if trend_notes:
        head += " Over the last minute: " + ", ".join(trend_notes) + "."

    if dtcs:
        body = (
            f" {len(dtcs)} active fault code(s) reported ({', '.join(dtcs[:3])})."
            " Get this checked at your dealer."
        )
    elif warnings:
        body = " " + "; ".join(warnings).capitalize() + " - worth getting checked."
    else:
        body = " Everything is within the normal range for this bike."

    return head + body


async def summarise_telemetry(readings: dict[str, Any]) -> TelemetrySummary:
    """Plain-language summary of the live OBD readings shown on the dashboard."""
    fallback = _heuristic_telemetry_summary(readings)

    if not _bedrock_ready():
        return TelemetrySummary(fallback, "fallback")

    lines = ["Latest reading:"]
    labels = {
        "rpm": "Engine speed (rpm)",
        "coolant_temp_c": "Coolant temperature (°C)",
        "speed_kph": "Road speed (km/h)",
        "battery_voltage": "Battery voltage (V)",
        "throttle_position_pct": "Throttle position (%)",
        "fuel_level_pct": "Fuel level (%)",
        "odometer_km": "Odometer (km)",
    }
    for key, label in labels.items():
        value = readings.get(key)
        if value is not None:
            lines.append(f"{label}: {value}")

    lines.extend(
        _format_window(readings.get("window_stats") or {}, readings.get("window_seconds"))
    )

    dtcs = [c for c in (readings.get("dtc_codes") or []) if c]
    lines.append(f"Active fault codes: {', '.join(dtcs) if dtcs else 'none'}")
    if readings.get("health_level"):
        lines.append(f"Rule-engine verdict: {readings['health_level']}")
    for reason in (readings.get("health_reasons") or [])[:5]:
        lines.append(f"Rule-engine note: {reason}")

    if not lines:
        return TelemetrySummary(fallback, "fallback")

    try:
        reply = await _invoke(
            system=TELEMETRY_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            max_tokens=320,
            temperature=0.3,
        )
    except (ClientError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Bedrock telemetry summary failed (%s); using heuristic", exc)
        return TelemetrySummary(fallback, "fallback")

    return TelemetrySummary(reply or fallback, "bedrock" if reply else "fallback")


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


def _selfcheck() -> None:
    """Self-check for the triage heuristic, which has no AI or database in it.

    Run with `python -m app.services.ai`. The heuristic is what the dealer queue
    shows for the first few seconds after a ticket is raised (the model refines it
    in the background), so its answers have to be sensible on their own.
    """
    cases: tuple[tuple[str | None, str, TicketCategory, TicketPriority], ...] = (
        # Safety first: brake complaints are never below HIGH, and an outright
        # failure is URGENT.
        ("Brake issue", "Front brake not working, bike does not stop", TicketCategory.BRAKES, TicketPriority.URGENT),
        ("Brake issue", "Brake pads feel slightly worn", TicketCategory.BRAKES, TicketPriority.HIGH),
        (None, "Petrol leak near the tank, smells strong", TicketCategory.ENGINE, TicketPriority.URGENT),
        # Routine work that names a component must not read as a fault in it.
        ("General service", "Periodic service due, please change engine oil", TicketCategory.PERIODIC_SERVICE, TicketPriority.LOW),
        (None, "Engine oil change needed", TicketCategory.PERIODIC_SERVICE, TicketPriority.LOW),
        # ...but a genuine fault in the same component still lands as a fault.
        ("Engine noise", "Engine is overheating and losing power", TicketCategory.ENGINE, TicketPriority.HIGH),
        (None, "Clutch is very tight, hard to change gears", TicketCategory.TRANSMISSION, TicketPriority.NORMAL),
        (None, "Headlight stopped working at night", TicketCategory.ELECTRICAL, TicketPriority.NORMAL),
        (None, "Rear tyre has a puncture", TicketCategory.TYRES, TicketPriority.HIGH),
        # No component named, so the category stays OTHER - but a bike that will
        # not start is still HIGH.
        (None, "Bike will not start this morning", TicketCategory.OTHER, TicketPriority.HIGH),
    )

    for type_, description, expected_category, expected_priority in cases:
        result = heuristic_triage(type_, description)
        assert result.category is expected_category, (
            f"{description!r}: expected {expected_category}, got {result.category}"
        )
        assert result.priority is expected_priority, (
            f"{description!r}: expected {expected_priority}, got {result.priority}"
        )
        assert result.summary, "every ticket needs a summary line"

    # A ticket with nothing to go on still gets a usable verdict.
    empty = heuristic_triage(None, None)
    assert empty.category is TicketCategory.OTHER
    assert empty.priority is TicketPriority.NORMAL

    # The telemetry fallback must state the readings it was given.
    text = _heuristic_telemetry_summary(
        {"rpm": 3200, "coolant_temp_c": 118.0, "battery_voltage": 11.4, "dtc_codes": ["P0217"]}
    )
    assert "3200 rpm" in text and "118" in text, text
    assert "P0217" in text, "active fault codes must be named"

    healthy = _heuristic_telemetry_summary(
        {"rpm": 3000, "coolant_temp_c": 82.0, "battery_voltage": 13.6, "dtc_codes": []}
    )
    assert "normal range" in healthy, healthy

    # JSON fence stripping, for models that ignore "no markdown".
    assert _strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_json_fence('Here you go: {"a": 1} cheers') == '{"a": 1}'

    print("ai self-check: OK")


if __name__ == "__main__":
    _selfcheck()
