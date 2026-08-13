"""ORM models.

Importing this package registers every table on `Base.metadata`, which is what
`alembic/env.py` targets for autogenerate.
"""

from app.models.base import Base
from app.models.catalog import BikeModel, ExchangeValue
from app.models.engagement import Notification, TestRideBooking
from app.models.incentive import EmployeeIncentive, IncentiveRule
from app.models.lead import Lead, LeadFollowup
from app.models.org import Customer, Dealer, Employee
from app.models.service import (
    ChatbotConversation,
    ChatbotMessage,
    ServiceRequest,
    ServiceRequestMessage,
)
from app.models.vehicle import ObdTelemetry, ServiceRecord, Vehicle

__all__ = [
    "Base",
    "BikeModel",
    "ChatbotConversation",
    "ChatbotMessage",
    "Customer",
    "Dealer",
    "Employee",
    "EmployeeIncentive",
    "ExchangeValue",
    "IncentiveRule",
    "Lead",
    "LeadFollowup",
    "Notification",
    "ObdTelemetry",
    "ServiceRecord",
    "ServiceRequest",
    "ServiceRequestMessage",
    "TestRideBooking",
    "Vehicle",
]
