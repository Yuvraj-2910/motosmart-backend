# PLAN.md — Smart Dealer Enquiry App (Backend)

> Drop this at the root of the **backend** repo as `PLAN.md`. It is the build plan for Claude Code.

## What we're building

A REST API for the YMSLI Smart Dealer Enquiry App (Hackathon PS-06). Dealer sales staff capture and follow up on leads; a public funnel (bike catalog, exchange value, test-ride booking) auto-generates leads; onboarded customers get vehicle analytics, service management, and a chatbot. Build in 4 phases — each phase is independently runnable and demoable.

## Tech stack (non-negotiable)

- **Python 3.12**, **FastAPI**, **Uvicorn**
- **SQLAlchemy 2.0** (typed, async) + **Alembic** for migrations
- **Pydantic v2** for schemas/settings
- **boto3** for all AWS access
- **AWS services:**
  - **Cognito** — user auth + OTP (email/SMS), user groups for roles
  - **RDS (Aurora Serverless v2, PostgreSQL)** — primary database
  - **S3** — object storage (bike images, brochures, service attachments)
  - **SES / SNS** — email + SMS delivery (Cognito uses these). **SNS** is the notification service; in-app notifications are served from the `notifications` table via polling.
  - **Bedrock** — AI features (lead intent, follow-up suggestion, note summary, chatbot)
- **Docker**; deploy to **ECS Fargate** or **AWS App Runner** (either is fine — App Runner is faster to stand up for the hackathon)

## Conventions

- Async everywhere: `async def` routes, `AsyncSession`, `asyncpg` driver.
- One router module per domain, mounted under `/api/v1`.
- Pydantic schemas separate from ORM models (`schemas/` vs `models/`).
- All AWS clients created once in `core/aws.py` and injected via FastAPI `Depends`.
- Never hardcode secrets — everything via `core/config.py` (Pydantic `BaseSettings`, reads env).
- Every table gets an Alembic migration; never `create_all` in app code.
- Enums as Python `enum.StrEnum`, stored as text/`VARCHAR` in Postgres.
- Return typed Pydantic response models; use FastAPI status codes explicitly.

## Directory structure

```
app/
  main.py                 # FastAPI app, router mounting, CORS, middleware
  core/
    config.py             # Settings (env-driven)
    aws.py                # boto3 client factory (cognito, s3, ses, sns, bedrock)
    security.py           # Cognito JWT verification, role/group extraction
    db.py                 # async engine + session dependency
  models/                 # SQLAlchemy ORM models (one file per domain)
  schemas/                # Pydantic request/response models
  routers/                # FastAPI routers (one per domain)
  services/               # business logic (lead assignment, incentives, ai, notifications)
  deps.py                 # shared dependencies (current_user, require_role)
alembic/
  versions/
  env.py
alembic.ini
Dockerfile
requirements.txt
.env.example
```

## Auth model (Cognito)

- Cognito **User Pool** with an app client (public client, no secret) and **USER_AUTH** flow enabled for passwordless **Email/SMS OTP**.
- Cognito **groups** = roles: `DEALER_STAFF`, `CUSTOMER`.
- Backend does **not** issue tokens — the Flutter app authenticates with Cognito directly and sends the resulting **JWT (ID or access token)** as `Authorization: Bearer <token>`.
- `core/security.py`: fetch the pool JWKS (cache it), verify signature + `aud`/`iss`/`exp`, extract `sub` and `cognito:groups`.
- `deps.py`:
  - `get_current_user` → verified claims (maps `sub` → `employees` or `customers` row).
  - `require_role("DEALER_STAFF")` → dependency that 403s if the group is missing.
- Dealer staff accounts are **admin-provisioned** (AdminCreateUser). Customers are created by a dealer during lead conversion, then invited to set OTP login.

## Environment variables (`.env.example`)

```
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dealerapp
AWS_REGION=ap-south-1
COGNITO_USER_POOL_ID=ap-south-1_XXXXXXX
COGNITO_APP_CLIENT_ID=xxxxxxxxxxxxxxxxxxxx
S3_BUCKET=ymsli-dealerapp-assets
SES_FROM_EMAIL=noreply@yourdomain
SNS_SMS_ENABLED=true
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-...
```

## Global setup (do this first)

1. Scaffold the structure above; `requirements.txt` with fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, alembic, pydantic-settings, boto3, python-jose[cryptography], httpx.
2. `core/config.py` — Pydantic `BaseSettings` reading the env vars above.
3. `core/db.py` — async engine + `get_session` dependency.
4. `core/aws.py` — lazy boto3 clients for cognito-idp, s3, ses, sns, bedrock-runtime.
5. `core/security.py` — JWKS fetch/cache + JWT verify.
6. Initialize Alembic (`alembic init alembic`), wire `env.py` to the async engine and import all models' metadata.
7. `main.py` — create app, add CORS (allow the Flutter dev origin), mount `/api/v1`, add a `GET /health`.
8. `Dockerfile` (python:3.12-slim, uvicorn). Provide a `docker-compose.yml` with a local Postgres for dev.

---

# Phase 1 — Core dealer flow + mandatory AI

**Deliverable:** dealer can log in (Cognito JWT), CRUD leads, manage follow-ups, move statuses, and get an AI intent classification. Runnable + demoable alone.

### Models / migration
- `dealers(id, name, code, city, address, phone, last_assigned_employee_id FK NULL, created_at)`
- `employees(id, dealer_id FK, cognito_sub UNIQUE, name, phone, email, is_active)`
- `customers(id, cognito_sub, name, phone, email, onboarding_dealer_id FK, created_at)`
- `bike_models(id, name, variant, category, price, engine_cc, image_url, brochure_url, stock_status, is_available)`
- `leads(id, dealer_id FK, assigned_employee_id FK NULL, customer_name, mobile, source[WALK_IN|TEST_RIDE|APP|FIELD], interested_model_id FK, current_bike, tentative_purchase_date, status[NEW|FOLLOW_UP|CLOSED_WON|CLOSED_LOST], ai_intent[HOT|WARM|COLD] NULL, notes, converted_customer_id FK NULL, created_at, updated_at)`
- `lead_followups(id, lead_id FK, employee_id FK, next_action, scheduled_date, completed, outcome_note, created_at)`
- Index: `leads(dealer_id, assigned_employee_id, status)`

### Routers (`/api/v1`)
- `GET /me` → current employee/customer profile + role
- `GET /leads` (query: status, q, due) · `POST /leads` (walk-in → self-assign to `current_user`) · `GET /leads/{id}` · `PATCH /leads/{id}`
- `POST /leads/{id}/convert` → create `customers` row, set `leads.converted_customer_id`, status CLOSED_WON
- `GET /leads/{id}/followups` · `POST /leads/{id}/followups` · `PATCH /followups/{id}`
- `GET /dashboard/summary` → counts of today's follow-ups + open leads for current dealer
- `POST /ai/classify-lead` → `services/ai.py` calls Bedrock, returns HOT/WARM/COLD, persist to `leads.ai_intent`

### Services
- `services/ai.py`: `classify_lead(notes, tentative_date) -> Intent`. Bedrock prompt returns one token; parse defensively. Stub-friendly (return WARM if Bedrock unavailable) so the demo never breaks.

### Seed script
`scripts/seed.py` — 1–2 dealers, ~5 employees (dealer sales staff), ~10 Yamaha `bike_models`, ~15 dummy leads across statuses. Dummy/masked data only (no real PII — DPDP).

---

# Phase 2 — Public funnel + auto-assignment + notifications

**Deliverable:** unauthenticated endpoints for catalog/exchange/test-ride; booking auto-creates and auto-assigns a lead; the assigned salesperson is notified.

### Models / migration
- `exchange_values(id, brand, model, year, base_value, condition_factor_json)`
- `test_ride_bookings(id, bike_model_id FK, name, mobile, preferred_date, preferred_time, dealer_id FK, status[REQUESTED|CONFIRMED|COMPLETED|CANCELLED], linked_lead_id FK NULL, created_at)`
- `notifications(id, recipient_type[EMPLOYEE|CUSTOMER], recipient_id, type[NEW_LEAD|TEST_RIDE|SERVICE_REPLY|FOLLOWUP_DUE], title, body, payload_json, is_read, created_at)`

### Routers
- Public (no auth): `GET /public/models`, `GET /public/models/{id}`, `POST /public/exchange-value`, `GET /public/availability?model_id=`, `POST /public/test-rides`
- Dealer: `GET /test-rides`, `PATCH /test-rides/{id}` (confirm/complete)
- Notifications: `GET /notifications` (unread first), `PATCH /notifications/{id}/read`. The app polls `GET /notifications` for the in-app notification center + unread badge — no device-token registration needed (no Firebase). SMS/email delivery goes through SNS/SES as needed. *(Optional real push later: Amazon SNS mobile push — note this still needs an APNs cert for iOS and an FCM credential for Android at the OS transport layer; not required for the demo.)*

### Services
- `services/assignment.py` — **round-robin** auto-assignment (see algorithm below).
- `services/notifications.py` — insert a `notifications` row (source of truth for the in-app center). Optionally also send an SNS SMS/SES email for high-priority events. All external sends are best-effort; failure must not roll back the booking.

### Lead assignment algorithm (in `POST /public/test-rides`, one transaction)
```
1. Resolve dealer_id from the booking form (location select, or pincode → nearest dealer).
2. Insert test_ride_booking.
3. Round-robin pick:
     candidates = employees WHERE dealer_id=X AND is_active=true
                  ORDER BY created_at                         # stable rotation order
     if no candidates -> assignee = NULL (unassigned pool)
     else:
        idx      = index of dealers.last_assigned_employee_id in candidates (-1 if null/absent)
        assignee = candidates[(idx + 1) % len(candidates)]
        UPDATE dealers SET last_assigned_employee_id = assignee.id   # advance pointer
   Lock the dealer row (SELECT ... FOR UPDATE) so concurrent bookings don't collide on the pointer.
4. Insert lead(source=TEST_RIDE, assigned_employee_id=assignee, linked_lead_id back-ref, status=NEW)
5. notify(assignee, NEW_LEAD)
```
Roster changes are self-healing: if the stored pointer is now inactive/deleted, the index lookup returns -1 and rotation restarts at the first candidate. Walk-in/field leads already self-assign in Phase 1's `POST /leads`.

---

# Phase 3 — Customer side (vehicles, service, chatbot)

**Deliverable:** onboarded customer logs in, sees vehicle analytics + service status, opens service-request threads, uses a Bedrock chatbot.

### Models / migration
- `vehicles(id, customer_id FK, bike_model_id FK, vin, registration_no, purchase_date, odometer_km)`
- `service_records(id, vehicle_id FK, service_date, odometer_km, service_type, cost, next_service_date, next_service_km)`
- `service_requests(id, vehicle_id FK, customer_id FK, dealer_id FK, type, description, status[OPEN|IN_PROGRESS|RESOLVED], preferred_date, created_at)`
- `service_request_messages(id, service_request_id FK, sender_type[CUSTOMER|DEALER], sender_id, message, created_at)`
- `obd_telemetry(id, vehicle_id FK, recorded_at, odometer_km, battery_voltage, fuel_level, engine_temp, avg_speed, dtc_codes, raw_json)`
- `chatbot_conversations(id, customer_id FK, started_at)`
- `chatbot_messages(id, conversation_id FK, role[USER|ASSISTANT], content, created_at)`

### Routers
- Dealer: `POST /customers`, `POST /vehicles` (assign a vehicle during onboarding), presigned S3 upload URL endpoint for images/brochures
- Customer: `GET /me/vehicles`, `GET /vehicles/{id}/analytics`, `GET /vehicles/{id}/service-status` (last + next by time AND km), `GET /vehicles/{id}/service-history`
- `GET/POST /service-requests`, `GET /service-requests/{id}`
- `GET/POST /service-requests/{id}/messages` (dealer reply → notify customer, type=SERVICE_REPLY)
- `POST /chatbot/message` (→ Bedrock, persist both turns), `GET /chatbot/history`

### Services
- `services/obd.py` — mock telemetry generator + a fake ingest endpoint (`POST /internal/obd`) to seed rows. Do **not** wire IoT Core.
- `services/storage.py` — S3 presigned PUT/GET for object storage.
- Extend `services/ai.py` with `chat(messages)` for the assistant.

---

# Phase 4 — Incentives + polish

**Deliverable:** employee incentive tracking + good-to-have polish.

### Models / migration
- `incentive_rules(id, dealer_id FK, name, event_type[LEAD_CONVERTED|TEST_RIDE|SALE], amount, period)`
- `employee_incentives(id, employee_id FK, period_month, leads_count, conversions_count, test_rides_count, sales_count, total_incentive, computed_at)`

### Routers
- `GET /incentives?month=` (all employees at the current dealer), `GET /incentives/employee/{id}`
- Leads polish: due/overdue filtering already supported via `GET /leads?due=overdue`; add duplicate-mobile check on `POST /leads` and `POST /public/test-rides` (warn, don't block)

### Services
- `services/incentives.py` — `recompute(month)`: aggregate per employee from leads/test_rides/service into `employee_incentives`. Expose `POST /internal/incentives/recompute` for the demo.

---

## Definition of done per phase
- Alembic migration written and applied.
- Endpoints return typed Pydantic models, documented in the auto OpenAPI (`/docs`).
- Seed data present so the frontend has something to render.
- AWS calls degrade gracefully (Bedrock/SNS/SES failures never 500 the core flow).
- `GET /health` green; app runs via `docker compose up`.
