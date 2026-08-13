"""Seed data for local development and the hackathon demo.

Creates ~2 dealers, ~5 employees, ~10 Yamaha bike models, ~15 leads spread
across statuses, plus a couple of exchange-value reference rows and one
onboarded customer with a vehicle so the customer-side screens have something
to render too.

All data is fictitious/masked - no real customer PII (DPDP compliance). Phone
numbers use the reserved 9999900xxx-style test range.

Usage:
    python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.catalog import BikeModel, ExchangeValue
from app.models.enums import AiIntent, LeadSource, LeadStatus, StockStatus
from app.models.lead import Lead, LeadFollowup
from app.models.org import Customer, Dealer, Employee
from app.models.vehicle import ServiceRecord, Vehicle

random.seed(42)

DEALERS = [
    {"name": "YMSLI Andheri", "code": "MUM-AND", "city": "Mumbai", "pincode": "400058",
     "phone": "+919999900001", "address": "Link Road, Andheri West, Mumbai"},
    {"name": "YMSLI Whitefield", "code": "BLR-WHF", "city": "Bengaluru", "pincode": "560066",
     "phone": "+919999900002", "address": "ITPL Main Road, Whitefield, Bengaluru"},
]

EMPLOYEE_NAMES = [
    "Rohan Mehta", "Priya Nair", "Arjun Iyer", "Sneha Kulkarni", "Vikram Singh",
]

BIKE_MODELS = [
    {"name": "MT-15", "variant": "V2", "category": "Sport", "price": Decimal("172900"),
     "engine_cc": 155, "stock_status": StockStatus.IN_STOCK},
    {"name": "R15", "variant": "V4", "category": "Sport", "price": Decimal("192900"),
     "engine_cc": 155, "stock_status": StockStatus.IN_STOCK},
    {"name": "FZ-S", "variant": "FI V4", "category": "Street", "price": Decimal("128900"),
     "engine_cc": 149, "stock_status": StockStatus.IN_STOCK},
    {"name": "Fascino", "variant": "125 Hybrid", "category": "Scooter", "price": Decimal("89900"),
     "engine_cc": 125, "stock_status": StockStatus.LOW_STOCK},
    {"name": "RayZR", "variant": "125 Hybrid", "category": "Scooter", "price": Decimal("87900"),
     "engine_cc": 125, "stock_status": StockStatus.IN_STOCK},
    {"name": "Aerox", "variant": "155", "category": "Scooter", "price": Decimal("139900"),
     "engine_cc": 155, "stock_status": StockStatus.LOW_STOCK},
    {"name": "MT-03", "variant": "Standard", "category": "Sport", "price": Decimal("399900"),
     "engine_cc": 321, "stock_status": StockStatus.OUT_OF_STOCK},
    {"name": "R3", "variant": "Standard", "category": "Sport", "price": Decimal("429900"),
     "engine_cc": 321, "stock_status": StockStatus.OUT_OF_STOCK},
    {"name": "FZ-X", "variant": "FI", "category": "Street", "price": Decimal("134900"),
     "engine_cc": 149, "stock_status": StockStatus.IN_STOCK},
    {"name": "Ray ZR Street Rally", "variant": "125", "category": "Scooter",
     "price": Decimal("93900"), "engine_cc": 125, "stock_status": StockStatus.IN_STOCK},
]

LEAD_NAME_POOL = [
    "Amit Kumar", "Neha Sharma", "Rahul Verma", "Divya Reddy", "Karan Malhotra",
    "Pooja Joshi", "Sanjay Gupta", "Anjali Rao", "Vivek Menon", "Ritu Chopra",
    "Manoj Pillai", "Shreya Das", "Aditya Bhatt", "Kavya Pillai", "Nikhil Shetty",
]

NOTE_SAMPLES = [
    ("Wants to book this week, finance already approved.", 7, AiIntent.HOT),
    ("Comparing with a competitor scooter, deciding in a month.", 30, AiIntent.WARM),
    ("Just browsing at the showroom, no timeline given.", None, AiIntent.COLD),
    ("Asked for on-road price and delivery timeline, ready to pay token today.", 5, AiIntent.HOT),
    ("Waiting for bonus payout before purchase, interested in R15.", 60, AiIntent.WARM),
    ("Enquiry only, wrong number on callback.", None, AiIntent.COLD),
    ("Exchange of old bike discussed, wants exchange value first.", 20, AiIntent.WARM),
    ("Confirmed booking amount paid, wants delivery by weekend.", 3, AiIntent.HOT),
]


async def seed() -> None:
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(Dealer.id).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            print("Seed data already present (a dealer exists) - skipping.")
            return

        # --- Dealers & employees ------------------------------------------
        dealers: list[Dealer] = []
        for spec in DEALERS:
            dealer = Dealer(**spec)
            session.add(dealer)
            dealers.append(dealer)
        await session.flush()

        employees: list[Employee] = []
        name_cycle = iter(EMPLOYEE_NAMES)
        for i, dealer in enumerate(dealers):
            count = 3 if i == 0 else len(EMPLOYEE_NAMES) - 3
            for _ in range(count):
                try:
                    name = next(name_cycle)
                except StopIteration:
                    break
                employee = Employee(
                    dealer_id=dealer.id,
                    name=name,
                    phone=f"+9199999{random.randint(10000, 99999)}",
                    email=f"{name.split()[0].lower()}@ymsli-demo.example",
                    is_active=True,
                    # cognito_sub left NULL - wire it up via AdminCreateUser separately.
                )
                session.add(employee)
                employees.append(employee)
        await session.flush()

        # --- Bike catalog ----------------------------------------------------
        models: list[BikeModel] = []
        for spec in BIKE_MODELS:
            model = BikeModel(
                **spec,
                image_url=None,
                brochure_url=None,
                is_available=spec["stock_status"] != StockStatus.OUT_OF_STOCK,
            )
            session.add(model)
            models.append(model)
        await session.flush()

        # --- Exchange value reference data -----------------------------------
        session.add_all(
            [
                ExchangeValue(
                    brand="Yamaha", model="FZ-S", year=date.today().year - 3,
                    base_value=Decimal("70000"),
                    condition_factor_json={
                        "EXCELLENT": "1.0", "GOOD": "0.85", "FAIR": "0.68", "POOR": "0.5"
                    },
                ),
                ExchangeValue(
                    brand="Honda", model="Activa", year=date.today().year - 4,
                    base_value=Decimal("45000"),
                    condition_factor_json={
                        "EXCELLENT": "1.0", "GOOD": "0.82", "FAIR": "0.65", "POOR": "0.48"
                    },
                ),
            ]
        )

        # --- Leads across statuses --------------------------------------------
        today = date.today()
        status_cycle = (
            [LeadStatus.NEW] * 5
            + [LeadStatus.FOLLOW_UP] * 6
            + [LeadStatus.CLOSED_WON] * 2
            + [LeadStatus.CLOSED_LOST] * 2
        )
        random.shuffle(status_cycle)

        leads: list[Lead] = []
        for i, lead_status in enumerate(status_cycle):
            dealer = dealers[i % len(dealers)]
            dealer_employees = [e for e in employees if e.dealer_id == dealer.id]
            employee = random.choice(dealer_employees) if dealer_employees else None
            note_text, days_ahead, intent = random.choice(NOTE_SAMPLES)
            model = random.choice(models)

            lead = Lead(
                dealer_id=dealer.id,
                assigned_employee_id=employee.id if employee else None,
                customer_name=LEAD_NAME_POOL[i % len(LEAD_NAME_POOL)],
                mobile=f"+9199999{random.randint(10000, 99999)}",
                source=random.choice(list(LeadSource)),
                interested_model_id=model.id,
                current_bike=random.choice([None, "Honda Activa", "Hero Splendor", "TVS Jupiter"]),
                tentative_purchase_date=(
                    today + timedelta(days=days_ahead) if days_ahead else None
                ),
                status=lead_status,
                ai_intent=intent,
                notes=note_text,
            )
            session.add(lead)
            leads.append(lead)
        await session.flush()

        # --- Follow-ups for open leads -----------------------------------------
        for lead in leads:
            if lead.status in (LeadStatus.NEW, LeadStatus.FOLLOW_UP):
                offset = random.choice([-2, -1, 0, 1, 3, 7])
                session.add(
                    LeadFollowup(
                        lead_id=lead.id,
                        employee_id=lead.assigned_employee_id,
                        next_action="Call to confirm interest and schedule a showroom visit.",
                        scheduled_date=today + timedelta(days=offset),
                        completed=False,
                    )
                )

        # --- One onboarded customer with a vehicle, so Phase 3 has data -------
        customer = Customer(
            name="Test Customer",
            phone="+919999900099",
            email="test.customer@ymsli-demo.example",
            onboarding_dealer_id=dealers[0].id,
        )
        session.add(customer)
        await session.flush()

        vehicle = Vehicle(
            customer_id=customer.id,
            bike_model_id=models[0].id,
            vin="DEMOVIN0000000001",
            registration_no="MH02AB1234",
            purchase_date=today - timedelta(days=400),
            odometer_km=6200,
        )
        session.add(vehicle)
        await session.flush()

        session.add(
            ServiceRecord(
                vehicle_id=vehicle.id,
                service_date=today - timedelta(days=190),
                odometer_km=3000,
                service_type="Periodic Maintenance",
                cost=Decimal("1200.00"),
                next_service_date=today + timedelta(days=(180 - 190)),
                next_service_km=6000,
                notes="Oil change, brake check.",
            )
        )

        await session.commit()

        print(
            f"Seeded {len(dealers)} dealers, {len(employees)} employees, "
            f"{len(models)} bike models, {len(leads)} leads, 1 customer with 1 vehicle."
        )


if __name__ == "__main__":
    asyncio.run(seed())
