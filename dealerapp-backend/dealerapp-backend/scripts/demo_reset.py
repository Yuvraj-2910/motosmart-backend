"""Reset to a minimal hackathon demo dataset.

Wipes dealers/employees/customers/leads/vehicles/tickets and reseeds:
2 dealers x 1 employee, 3 customers, 4 leads, 1 open service ticket.
Keeps the bike catalog and exchange-value reference rows.

One employee and one customer sign in with a real Cognito email OTP; the rest
resolve through the AUTH_DEV_MODE `X-Dev-User: <email>:` header.

Usage:
    python -m scripts.demo_reset
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models.catalog import BikeModel
from app.models.enums import (
    AiIntent,
    LeadSource,
    LeadStatus,
    SenderType,
    ServiceRequestStatus,
    TicketCategory,
    TicketPriority,
)
from app.models.lead import Lead, LeadFollowup
from app.models.org import Customer, Dealer, Employee
from app.models.service import ServiceRequest, ServiceRequestMessage
from app.models.vehicle import ServiceRecord, Vehicle

# Cognito subs for the two accounts that sign in with a real email OTP.
REAL_EMPLOYEE_EMAIL = "ijklmnop7417@gmail.com"
REAL_EMPLOYEE_SUB = "94080dfc-b091-70a5-8b52-bef6f44bf966"
REAL_CUSTOMER_EMAIL = "vyom5212@gmail.com"
REAL_CUSTOMER_SUB = "84e8ddbc-2001-7037-cc8e-d9808f24ac5f"

WIPE_TABLES = [
    "chatbot_messages",
    "chatbot_conversations",
    "service_request_messages",
    "service_requests",
    "obd_telemetry",
    "service_records",
    "vehicles",
    "test_ride_bookings",
    "notifications",
    "employee_incentives",
    "lead_followups",
    "leads",
    "customers",
    "employees",
    "dealers",
]


async def main() -> None:
    today = date.today()
    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(WIPE_TABLES)} CASCADE")
        )

        models = {
            f"{m.name}": m
            for m in (await session.execute(select(BikeModel))).scalars()
        }

        # --- Dealers -----------------------------------------------------
        andheri = Dealer(
            name="YMSLI Andheri", code="MUM-AND", city="Mumbai", pincode="400058",
            phone="+919999900001", address="Link Road, Andheri West, Mumbai",
        )
        whitefield = Dealer(
            name="YMSLI Whitefield", code="BLR-WHF", city="Bengaluru", pincode="560066",
            phone="+919999900002", address="ITPL Main Road, Whitefield, Bengaluru",
        )
        session.add_all([andheri, whitefield])
        await session.flush()

        # --- One employee per dealer -------------------------------------
        rohan = Employee(
            dealer_id=andheri.id, name="Rohan Mehta", email=REAL_EMPLOYEE_EMAIL,
            phone="+919999900201", is_active=True, cognito_sub=REAL_EMPLOYEE_SUB,
        )
        priya = Employee(
            dealer_id=whitefield.id, name="Priya Nair", email="priya@ymsli-demo.example",
            phone="+919999900211", is_active=True, cognito_sub="priya@ymsli-demo.example",
        )
        session.add_all([rohan, priya])
        await session.flush()

        # --- Customers ---------------------------------------------------
        vyom = Customer(
            name="Vyom Sharma", email=REAL_CUSTOMER_EMAIL, phone="+919999900301",
            onboarding_dealer_id=andheri.id, cognito_sub=REAL_CUSTOMER_SUB,
        )
        amit = Customer(
            name="Amit Kumar", email="amit@ymsli-demo.example", phone="+919999900302",
            onboarding_dealer_id=andheri.id, cognito_sub="amit@ymsli-demo.example",
        )
        neha = Customer(
            name="Neha Sharma", email="neha@ymsli-demo.example", phone="+919999900303",
            onboarding_dealer_id=whitefield.id, cognito_sub="neha@ymsli-demo.example",
        )
        session.add_all([vyom, amit, neha])
        await session.flush()

        # --- One vehicle each --------------------------------------------
        vyom_bike = Vehicle(
            customer_id=vyom.id, bike_model_id=models["MT-15"].id,
            vin="DEMOVIN0000000001", registration_no="MH02VY1234",
            purchase_date=today - timedelta(days=300), odometer_km=5400,
        )
        amit_bike = Vehicle(
            customer_id=amit.id, bike_model_id=models["Fascino"].id,
            vin="DEMOVIN0000000002", registration_no="MH02AK5678",
            purchase_date=today - timedelta(days=180), odometer_km=2100,
        )
        neha_bike = Vehicle(
            customer_id=neha.id, bike_model_id=models["R15"].id,
            vin="DEMOVIN0000000003", registration_no="KA05NS9012",
            purchase_date=today - timedelta(days=90), odometer_km=1500,
        )
        session.add_all([vyom_bike, amit_bike, neha_bike])
        await session.flush()

        session.add(
            ServiceRecord(
                vehicle_id=vyom_bike.id,
                service_date=today - timedelta(days=120),
                odometer_km=3200,
                service_type="Periodic Maintenance",
                cost=Decimal("1200.00"),
                next_service_date=today + timedelta(days=60),
                next_service_km=9000,
                notes="Oil change, brake check.",
            )
        )

        # --- Leads: one converted, three open ----------------------------
        session.add_all(
            [
                Lead(
                    dealer_id=andheri.id, assigned_employee_id=rohan.id,
                    customer_name="Vyom Sharma", mobile="+919999900301",
                    source=LeadSource.APP, interested_model_id=models["MT-15"].id,
                    status=LeadStatus.CLOSED_WON, ai_intent=AiIntent.HOT,
                    notes="Booked and delivered. Converted from the app enquiry.",
                    converted_customer_id=vyom.id, converted_by_employee_id=rohan.id,
                    converted_at=now - timedelta(days=10),
                ),
            ]
        )

        karan = Lead(
            dealer_id=andheri.id, assigned_employee_id=rohan.id,
            customer_name="Karan Malhotra", mobile="+919999900401",
            source=LeadSource.WALK_IN, interested_model_id=models["R15"].id,
            current_bike="Honda Activa",
            tentative_purchase_date=today + timedelta(days=7),
            status=LeadStatus.NEW, ai_intent=AiIntent.HOT,
            notes="Wants to book this week, finance already approved.",
        )
        divya = Lead(
            dealer_id=whitefield.id, assigned_employee_id=priya.id,
            customer_name="Divya Reddy", mobile="+919999900402",
            source=LeadSource.TEST_RIDE, interested_model_id=models["Aerox"].id,
            current_bike="TVS Jupiter",
            tentative_purchase_date=today + timedelta(days=20),
            status=LeadStatus.FOLLOW_UP, ai_intent=AiIntent.WARM,
            notes="Took a test ride, comparing with a competitor scooter.",
        )
        sanjay = Lead(
            dealer_id=whitefield.id, assigned_employee_id=priya.id,
            customer_name="Sanjay Gupta", mobile="+919999900403",
            source=LeadSource.WALK_IN, interested_model_id=models["Fascino"].id,
            status=LeadStatus.NEW, ai_intent=AiIntent.COLD,
            notes="Just browsing at the showroom, no timeline given.",
        )
        session.add_all([karan, divya, sanjay])
        await session.flush()

        session.add_all(
            [
                LeadFollowup(
                    lead_id=karan.id, employee_id=rohan.id,
                    next_action="Call to confirm the booking amount and delivery slot.",
                    scheduled_date=today, completed=False,
                ),
                LeadFollowup(
                    lead_id=divya.id, employee_id=priya.id,
                    next_action="Share the on-road price breakup over WhatsApp.",
                    scheduled_date=today + timedelta(days=2), completed=False,
                ),
                LeadFollowup(
                    lead_id=sanjay.id, employee_id=priya.id,
                    next_action="Follow up on the Fascino enquiry.",
                    scheduled_date=today - timedelta(days=1), completed=False,
                ),
            ]
        )

        # --- One open service ticket for the real customer ---------------
        ticket = ServiceRequest(
            vehicle_id=vyom_bike.id, customer_id=vyom.id, dealer_id=andheri.id,
            type="Brakes",
            description="Front brake feels spongy and squeals at low speed.",
            status=ServiceRequestStatus.OPEN,
            preferred_date=today + timedelta(days=2),
            ai_category=TicketCategory.BRAKES,
            ai_priority=TicketPriority.URGENT,
            ai_summary="Spongy front brake with squealing — inspect pads and bleed the line.",
        )
        session.add(ticket)
        await session.flush()

        session.add(
            ServiceRequestMessage(
                service_request_id=ticket.id,
                sender_type=SenderType.CUSTOMER,
                sender_id=vyom.id,
                message="Front brake feels spongy and squeals at low speed.",
            )
        )

        await session.commit()
        print("Demo reset complete: 2 dealers, 2 employees, 3 customers, 4 leads, 1 ticket.")


if __name__ == "__main__":
    asyncio.run(main())
