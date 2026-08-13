"""Domain enums.

All of these are Python `StrEnum`s and are persisted as VARCHAR. Because a
`StrEnum` member compares equal to its string value, ORM columns are typed as
plain `str` and comparisons like `lead.status == LeadStatus.NEW` work directly
against the value loaded from Postgres. Validation happens at the Pydantic
boundary.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    DEALER_STAFF = "DEALER_STAFF"
    CUSTOMER = "CUSTOMER"


class LeadSource(StrEnum):
    WALK_IN = "WALK_IN"
    TEST_RIDE = "TEST_RIDE"
    APP = "APP"
    FIELD = "FIELD"


class LeadStatus(StrEnum):
    NEW = "NEW"
    FOLLOW_UP = "FOLLOW_UP"
    CLOSED_WON = "CLOSED_WON"
    CLOSED_LOST = "CLOSED_LOST"


class AiIntent(StrEnum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"


class StockStatus(StrEnum):
    IN_STOCK = "IN_STOCK"
    LOW_STOCK = "LOW_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"


class TestRideStatus(StrEnum):
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RecipientType(StrEnum):
    EMPLOYEE = "EMPLOYEE"
    CUSTOMER = "CUSTOMER"


class NotificationType(StrEnum):
    NEW_LEAD = "NEW_LEAD"
    TEST_RIDE = "TEST_RIDE"
    SERVICE_REPLY = "SERVICE_REPLY"
    FOLLOWUP_DUE = "FOLLOWUP_DUE"


class ServiceRequestStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class SenderType(StrEnum):
    CUSTOMER = "CUSTOMER"
    DEALER = "DEALER"


class ChatRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class IncentiveEventType(StrEnum):
    LEAD_CONVERTED = "LEAD_CONVERTED"
    TEST_RIDE = "TEST_RIDE"
    SALE = "SALE"


class IncentivePeriod(StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class DueFilter(StrEnum):
    """`GET /leads?due=` filter values."""

    TODAY = "today"
    OVERDUE = "overdue"
    UPCOMING = "upcoming"
