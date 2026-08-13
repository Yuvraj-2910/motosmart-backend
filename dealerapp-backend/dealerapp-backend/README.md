# Smart Dealer Enquiry App — Backend

REST API for the YMSLI Smart Dealer Enquiry App (Hackathon PS-06). FastAPI +
SQLAlchemy 2.0 (async) + Alembic, backed by Postgres, with Cognito auth and
Bedrock-powered AI features. Built to `PLAN.md`'s four phases; each phase is
independently runnable.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async, `asyncpg`) · Alembic · Pydantic
v2 · boto3 (Cognito, S3, SES, SNS, Bedrock) · Docker

## Quickstart (local, Docker)

```bash
cp .env.example .env      # fill in whatever you have; everything else has a safe default
docker compose up --build
```

This starts Postgres and the API together, runs migrations on boot, and serves
docs at http://localhost:8000/docs. `AUTH_DEV_MODE=true` is set by default in
`docker-compose.yml` so you can hit protected endpoints without a real Cognito
pool — see **Local auth shortcut** below.

Seed some demo data once the containers are up:

```bash
docker compose exec api python -m scripts.seed
```

## Quickstart (local, no Docker)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Postgres must be reachable at DATABASE_URL. Easiest: `docker compose up db`.
cp .env.example .env
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

## Configuration

Every setting is an environment variable, read by `app/core/config.py`
(`pydantic-settings`, `.env` supported). See `.env.example` for the full list.
Nothing is hardcoded; there is no `.env` committed.

Key ones:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` |
| `COGNITO_USER_POOL_ID` / `COGNITO_APP_CLIENT_ID` | JWT verification |
| `S3_BUCKET` | presigned upload/download URLs |
| `BEDROCK_MODEL_ID` | lead classification + chatbot; features fall back to deterministic heuristics if unset or unreachable |
| `SNS_SMS_ENABLED` / `SES_FROM_EMAIL` | optional SMS/email fan-out for high-priority notifications |
| `INTERNAL_API_KEY` | guards `/api/v1/internal/*` (OBD seed/ingest, incentive recompute) |
| `AUTH_DEV_MODE` | local-only Cognito bypass (see below); ignored unless `ENVIRONMENT=development` |

## Auth

The backend never issues tokens. The Flutter app authenticates directly
against Cognito (USER_AUTH / OTP flow) and sends the resulting JWT as
`Authorization: Bearer <token>`. `app/core/security.py` fetches and caches the
pool's JWKS, verifies signature/issuer/audience/expiry, and extracts `sub` +
`cognito:groups`. `app/deps.py` maps the verified `sub` to an `employees` or
`customers` row and exposes `require_role(...)` guards.

### Local auth shortcut

Set `ENVIRONMENT=development` and `AUTH_DEV_MODE=true` (both are the
docker-compose defaults), then send:

```
X-Dev-User: <any-string-as-sub>:DEALER_STAFF
```

instead of a real bearer token. The `sub` still has to match a seeded
`employees.cognito_sub` (or `customers.cognito_sub`) row for `get_current_user`
to resolve a profile — the seed script leaves these `NULL`, so either update a
row manually or provision a real Cognito user for end-to-end testing. This
switch is refused outside `ENVIRONMENT=development`, regardless of the flag.

## Project layout

```
app/
  main.py            FastAPI app, CORS, request-ID logging, /health
  core/              settings, DB engine, boto3 clients, Cognito JWT verification
  models/            SQLAlchemy ORM (one file per domain)
  schemas/           Pydantic request/response models
  routers/           one module per domain, mounted under /api/v1
  services/          business logic: ai, assignment, notifications, obd,
                      storage, cognito, leads, exchange, vehicles, incentives
  deps.py            current_user / require_role / internal-key dependencies
alembic/
  versions/          0001-0004, one migration per phase
scripts/
  seed.py            dummy dealers/employees/models/leads (no real PII)
```

## API surface by phase

**Phase 1 — core dealer flow**
`GET /me` · `GET/POST /leads` · `GET/PATCH /leads/{id}` ·
`POST /leads/{id}/convert` · `GET/POST /leads/{id}/followups` ·
`PATCH/DELETE /followups/{id}` · `GET /dashboard/summary` ·
`POST /ai/classify-lead`

**Phase 2 — public funnel + notifications**
`GET /public/models[/​{id}]` · `GET /public/dealers` ·
`GET /public/availability` · `POST /public/exchange-value` ·
`POST /public/test-rides` (auto-assigns + notifies) ·
`GET/PATCH /test-rides[/​{id}]` · `GET /notifications` ·
`PATCH /notifications/{id}/read` · `PATCH /notifications/read-all`

**Phase 3 — customer side**
`POST /customers` · `POST /vehicles` · `POST /uploads/presign[-download]` ·
`GET /me/vehicles` · `GET /vehicles/{id}/analytics` ·
`GET /vehicles/{id}/service-status` · `GET /vehicles/{id}/service-history` ·
`GET/POST /service-requests[/​{id}]` ·
`GET/POST /service-requests/{id}/messages` ·
`POST /chatbot/message` · `GET /chatbot/history`

**Phase 4 — incentives**
`GET /incentives` · `GET /incentives/employee/{id}`

**Ops (guarded by `X-Internal-Key`, not Cognito)**
`POST /internal/obd` · `POST /internal/obd/seed` ·
`POST /internal/incentives/recompute`

Full interactive reference: `/docs` (Swagger) or `/redoc`.

`PATCH /leads/{id}` accepts any status including `CLOSED_WON`, so the app's
status dropdown works standalone; `POST /leads/{id}/convert` is the richer path
that also creates and links the customer row. Test rides may go
`REQUESTED → COMPLETED` directly (a walk-in can ride without in-app
confirmation); `COMPLETED` and `CANCELLED` are terminal.

See `COMPLIANCE_AUDIT.md` for the audit of this implementation against
`PLAN.md`, including verification steps and deliberate deviations.

## Design notes worth knowing

- **Round-robin assignment** (`services/assignment.py`): the rotation pointer
  lives on `dealers.last_assigned_employee_id`. The dealer row is locked
  (`SELECT ... FOR UPDATE`) before reading it, so concurrent test-ride bookings
  can't double-assign. If the stored pointer references a deactivated or
  deleted employee, the lookup returns `-1` and rotation restarts from the
  first active candidate — no manual repair needed after roster changes.
- **AI features degrade, never fail** (`services/ai.py`): lead classification
  and the chatbot both fall back to a deterministic heuristic / canned reply
  if Bedrock is unconfigured, throttled, or erroring. Responses report
  `source: "bedrock"` or `"fallback"` so the app can render a note if it wants
  to, but the demo never breaks on an AI hiccup.
- **Notifications are DB-first**: `notifications` rows are the source of
  truth for the in-app centre the Flutter app polls. SNS/SES sends are a
  best-effort side effect, dispatched via `BackgroundTasks` after commit, so a
  slow or failing AWS call can never roll back a booking or lead.
- **Service status uses both time and distance**: whichever of "next service
  date" or "next service km" comes first determines `OVERDUE` / `DUE_NOW` /
  `OK`, matching the plan's "last + next by time AND km" requirement.
- **Round-robin rotation order is `employees.created_at`** (with `id` as
  tiebreak), so rotation follows roster seniority rather than an arbitrary UUID
  order. Employees seeded in one transaction share a statement timestamp, which
  is why the tiebreak is needed for a total, stable order.
- **Duplicate-mobile checks are advisory only** (`services/leads.py`): both
  `POST /leads` and `POST /public/test-rides` return a `warnings[]` array
  alongside the created resource instead of blocking the write — the same
  person legitimately enquires twice.
- **AWS failures degrade gracefully everywhere**: every boto3 call site
  catches `ClientError`/`BotoCoreError` and returns a typed failure or falls
  back, per the plan's definition of done ("AWS calls degrade gracefully").
