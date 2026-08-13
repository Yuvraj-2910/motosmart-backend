# Backend Compliance Audit — `PLAN_backend.md`

Audit of the implemented backend against `PLAN_backend.md`, with
`PLAN_frontend.md` used only as reference for integration contracts. No
frontend files were read for instruction or modified.

**Result: compliant.** 9 issues found and fixed, one of which was a
request-breaking bug. Verified against a real PostgreSQL 16 instance, not just
by inspection.

---

## 1. Verification performed

Everything below was executed, not eyeballed:

| Check | Result |
|---|---|
| All 4 migrations apply to an empty database | 4/4 applied, 18 plan tables created |
| `alembic downgrade base` → `upgrade head` round-trip | Clean (exercises the `dealers`↔`employees` FK cycle) |
| `alembic revision --autogenerate` drift check | **Empty** — schema matches ORM exactly |
| `scripts/seed.py` | 2 dealers, 5 employees, 10 models, 15 leads, 1 customer + vehicle |
| pyflakes | 0 issues |
| OpenAPI generation | 38 documented paths |
| Round-robin: full-lap rotation, no repeats, stable cycle | Pass |
| Round-robin: stale pointer → restart at first candidate | Pass |
| Round-robin: no active staff / unknown dealer → `None`, no exception | Pass |
| Phase 2 booking transaction (booking + lead + assign + notify) | Pass, with lead back-reference intact |
| Duplicate mobile warns without blocking | Pass (`201` + `DUPLICATE_MOBILE`) |
| AI classify with Bedrock disabled | Returns `WARM`, `source: "fallback"` |
| Chatbot with Bedrock disabled | Returns fallback reply, both turns persisted |
| Role separation (customer↔dealer endpoints) | `403` both directions |
| Cross-dealer data access | `403`/`404`, no leakage |
| `/internal/*` without `X-Internal-Key` | `401` |
| Service thread: dealer reply → `SERVICE_REPLY` to customer | Pass, auto `IN_PROGRESS` |
| Incentive recompute + read | Pass, per-employee and dealer totals |
| `/health` with database down | `200` with `database: "down"` (degrades, doesn't 500) |

---

## 2. Issues found and fixed

### 2.1 Critical — `POST /service-requests` and `GET /service-requests/{id}` returned 500

`ServiceRequestDetailOut.model_validate(request)` ran against an ORM instance
while the schema declares a `messages` list. Pydantic's `from_attributes`
walked into the lazy `messages` relationship during validation, which raises
`MissingGreenlet` under asyncio.

Worse than a plain error: the row was already committed, so the client got a
500 for a request that had actually succeeded — the customer would retry and
duplicate their service request.

Fixed with a `_detail_out()` helper that validates against `ServiceRequestOut`
(which has no `messages` field) and attaches the already-loaded rows
explicitly. Audited every other response model with a relationship-backed list
field; `LeadDetailOut` and `ChatHistoryOut` were already constructed explicitly
and were never affected.

### 2.2 Round-robin ordered by the wrong column

The plan's algorithm specifies `ORDER BY created_at  # stable rotation order`,
but the plan's own `employees` table spec omits `created_at`. I had ordered by
`id` (UUIDv4) — stable, but an arbitrary order unrelated to roster seniority,
so rotation wouldn't match what a dealer would expect.

Added `created_at` to `employees` (model + migration `0001`) and changed the
ordering to `(created_at, id)`. The `id` tiebreak matters: employees seeded in
one transaction share a statement timestamp, so `created_at` alone is not a
total order and rotation could be non-deterministic between calls.

### 2.3 ORM ↔ migration drift (three separate classes)

`--autogenerate` produced a 40-line diff, meaning the next developer to run it
would have generated a migration that silently undid schema decisions:

- **`service_requests.updated_at`** existed in migration `0003` but not on the
  model — autogenerate wanted to **drop the column**. Model now uses
  `TimestampsMixin`, which also gives the thread a real activity timestamp.
- **~18 `server_default`s** were set in migrations but not declared on the ORM
  columns (only Python-side `default=`) — autogenerate wanted to drop every one,
  which would break any insert not going through the ORM.
- **`cognito_sub`** had `unique=True, index=True` on the model (one unique
  index) but the migration created a unique *constraint* plus a second,
  redundant non-unique index — two extra objects per table and a permanent diff.

Post-fix autogenerate output is empty.

### 2.4 `PATCH /leads/{id}` rejected `CLOSED_WON` — would have broken the frontend

I had returned `409` for a direct `CLOSED_WON`, insisting callers use
`/convert`. But `PLAN_frontend.md` Phase 1 specifies the lead detail screen has
a **status change control** *and* a separate "Convert to customer" action. A
dealer picking `CLOSED_WON` from the dropdown would have hit an error with no
way forward.

Relaxed to allow it. `/convert` remains the richer path that also creates and
links the customer row.

### 2.5 Test-ride `REQUESTED → COMPLETED` was blocked

My state machine forced `CONFIRMED` first. The frontend offers confirm and
complete side by side, and a walk-in can ride without the request ever being
confirmed in the app. Now allowed; `COMPLETED`/`CANCELLED` remain terminal
(re-opening still correctly returns `409`).

### 2.6 `TestRideBookingOut` had untyped fields

`created_at: object | None` and `status: str` produced a useless OpenAPI schema
— the Flutter `freezed` model would have generated `Object?` instead of
`DateTime?`, and lost the status enum. Now `datetime | None` and
`TestRideStatus`, so codegen produces the right Dart types.

### 2.7 Dependency aliases misused in plain helper functions

Six helpers annotated parameters as `SessionDep` / `AnyUserDep` — those are
`Annotated[..., Depends(...)]` aliases meaningful only on endpoints. Harmless
at runtime but actively misleading, and it would break if anyone promoted a
helper to a route. Changed to `AsyncSession` / `CurrentUser`.

### 2.8 Dead code

Removed an unused `fk_uuid()` helper, an empty `if TYPE_CHECKING: pass` block,
the unused generic `Page` schema, and three `BackgroundTasks` parameters that
were declared but never used (only the test-ride booking actually dispatches
background work).

### 2.9 Misleading Dockerfile comment / obsolete Compose key

The Dockerfile claimed to install Postgres client libraries for "asyncpg's
build step" — asyncpg ships wheels; `curl` is only there for the healthcheck.
Also removed the obsolete `version:` key from `docker-compose.yml`, which
warns on every Compose v2 invocation.

---

## 3. Clause-by-clause compliance

**Tech stack & conventions** — all met: Python 3.12, FastAPI, async
SQLAlchemy 2.0 + `asyncpg`, Alembic, Pydantic v2, boto3, StrEnums stored as
VARCHAR, schemas separate from models, one router per domain under `/api/v1`,
no hardcoded secrets, no `create_all()` in app code, typed response models with
explicit status codes.

**Phase 1** — all 6 tables with every specified column, the
`leads(dealer_id, assigned_employee_id, status)` index, all 9 endpoints,
stub-friendly `classify_lead`, and a seed script within the stated ranges using
only fictitious data and reserved test phone numbers (DPDP).

**Phase 2** — all 3 tables, all 5 public endpoints plus dealer test-ride
management and the polling notification centre. The assignment algorithm
implements the plan's 5 steps in one transaction with `SELECT ... FOR UPDATE`
on the dealer row and self-healing pointer recovery, all verified by test.

**Phase 3** — all 7 tables and all endpoints. Service status evaluates **both**
time and distance as required. `services/obd.py` is a mock generator with a
fake ingest endpoint; IoT Core is deliberately not wired.

**Phase 4** — both tables, both endpoints, `recompute(month)` exposed via
`POST /internal/incentives/recompute`, and duplicate-mobile warnings on both
`POST /leads` and `POST /public/test-rides`.

**Definition of done** — migrations written and applied; endpoints typed and
documented at `/docs`; seed data present; AWS calls degrade gracefully
(Bedrock/SNS/SES/S3/Cognito failures never 500 a core flow, verified with
Bedrock disabled); `/health` green and `docker compose up` wired.

---

## 4. Deliberate deviations

Three places where I departed from a literal reading, each to serve the plan's
intent or the frontend contract:

1. **`dealer_id` is optional on `POST /public/test-rides`.** The frontend marks
   the dealer select required, but the plan's own algorithm says
   "location select, **or** pincode → nearest dealer". Accepting either, with a
   `422` only when neither resolves, supports both paths rather than forcing
   the looser one to fail.
2. **Added `dealers.pincode` and `GET /public/dealers`.** Neither is in the
   plan's table spec, but the pincode fallback above is unimplementable without
   the column, and the booking form's dealer picker has no way to populate
   itself otherwise.
3. **Added an `AUTH_DEV_MODE` header bypass.** Not in the plan. Phases are
   meant to be independently runnable, which is impossible before a Cognito
   pool exists. It hard-refuses unless `ENVIRONMENT=development`, and startup
   logs a warning when active.

## 5. Notes for deployment

- **`employees.cognito_sub` is `NULL` in seed data** by design — dealer staff
  are admin-provisioned. Authenticated endpoints need either real Cognito users
  or a `cognito_sub` set on a seeded row.
- **The plan offers ECS Fargate or App Runner**; the repo ships a Dockerfile
  that satisfies both. No service-specific IaC is included — I'd rather not
  guess at account-specific values.
- **Migrations run on container start** in the Dockerfile for demo convenience.
  For production, move that to a one-off deploy task so concurrent container
  starts don't race on the migration lock.
- **`core/aws.py` exposes `get_*` Depends helpers** per the plan's convention,
  but services call the cached factories directly since `Depends` isn't
  available outside the request path. Clients are still created exactly once
  per process, which is the substance of that requirement.
