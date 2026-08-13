# Frontend ↔ Backend integration

How `motosmart-app2.0/` (Flutter) talks to `dealerapp-backend/` (FastAPI), and how
to run the pair.

## Run it

**1. Backend** — needs `.env` (already points at the RDS instance):

```bash
cd dealerapp-backend
ENVIRONMENT=development AUTH_DEV_MODE=true .venv/bin/uvicorn app.main:app --port 8000
```

Check: `curl localhost:8000/health` → `{"status":"ok","database":"up"}`.
Migrations are already applied and the database is seeded.

**2. Frontend** — the port matters (see CORS below):

```bash
cd motosmart-app2.0
flutter run -d chrome --web-port=8080
```

Log in with a seeded account — any OTP is accepted in dev-auth mode:

| Role | Identifier |
|---|---|
| Dealer staff | `rohan@ymsli-demo.example` (also priya/arjun/sneha/vikram) |
| Customer | `test.customer@ymsli-demo.example` |

To go back to the offline demo data: `--dart-define=USE_MOCK_DATA=true`.

## How the swap works

The Flutter app was already built against one abstract repository per domain,
with mock implementations behind Riverpod providers. Integration added a
dio-backed implementation of each of those same interfaces in
`lib/data/api/`, so **no screen or widget changed**.

Each repository provider picks an implementation:

```dart
final leadsRepositoryProvider = Provider<LeadsRepository>((ref) {
  if (ref.watch(useMockDataProvider)) return MockLeadsRepository(...);
  return ApiLeadsRepository(ref.watch(apiClientProvider));
});
```

`useMockDataProvider` defaults to the build-time `Env.useMockData` (now
**false** — the real API is the default) but is a provider so the widget tests
pin themselves to mock mode with one override.

Request plumbing: `core/network/dio_client.dart` owns the dio instance, attaches
auth, and maps every failure to `ApiException` (including FastAPI's 422
field-error lists). `core/network/api_client.dart` wraps it so repositories deal
in maps and lists, never `Response`/`DioException`.

## Auth

Two modes, chosen by `Env.authDevMode` (default **true**):

- **Dev shortcut** — sends `X-Dev-User: <identifier>:`, which the backend maps to
  an `employees`/`customers` row. The trailing colon means "no explicit group",
  so the API probes both tables and `GET /me` returns the role. This exists
  because dealer staff are admin-provisioned in Cognito, so a freshly seeded
  database has no pool users to sign in as. The backend refuses this header
  unless it runs with `ENVIRONMENT=development` **and** `AUTH_DEV_MODE=true`.
- **Cognito** (`--dart-define=AUTH_DEV_MODE=false`) — real passwordless USER_AUTH
  OTP, called directly over dio (`InitiateAuth` → `RespondToAuthChallenge`) rather
  than adding `amplify_flutter`; PLAN_frontend.md allows either. Requires pool
  users whose `sub` matches `employees.cognito_sub`.

`scripts/seed.py` seeds `cognito_sub` with each account's email, which is what
makes the dev shortcut resolve. Existing rows were backfilled.

## Contract mismatches that were fixed

These were real and would each have crashed or silently misrendered:

| Issue | Fix |
|---|---|
| Pydantic serialises `Decimal` as a **JSON string** (`"139900.00"`); models did `as num` | `models/json_utils.dart` coercion helpers, used by every `fromJson` |
| Backend `StockStatus.LOW_STOCK` vs Dart `LIMITED` — silently fell back to "in stock" | Dart enum value corrected to `LOW_STOCK` |
| `DashboardSummary` expected flat counters + a follow-up **list**; API returned breakdown maps + a **count** | API gained `closed_this_month` and `todays_followup_items`; Dart factory reads the real field names |
| `convertLead` needed a `Customer`; API returned only `customer_id` | `LeadConvertResponse` gained the full `customer` row |
| Wrapper envelopes (`{lead, warnings}`, `{booking, ...}`, `{items, unread_count}`, `{user_message, assistant_message}`) | Unwrapped in the repositories |
| `ServiceStatus` had no `fromJson`, and the API reports the last service as flat `last_service_*` fields | Added, rebuilding a `ServiceRecord` from those fields |
| `IncentiveSummary`/`EmployeeIncentive` had no `fromJson`; period is `"YYYY-MM"` | Added, with `parsePeriodMonth` |
| Many nullable columns (`variant`, `service_type`, `dealer_id`, `employee_id`, `preferred_time`, …) were non-null in Dart | Coalesced; `Vehicle.purchaseDate` became nullable and its one usage guarded |
| `checkAvailability` wanted a bool; API returns an object | Reads `is_available` |
| `preferred_time` is a `time` — rejects `"10:30 AM"` | Normalised to 24-hour `HH:MM` |

## AI (Bedrock)

Live on **Claude Sonnet 5**, verified end to end — lead intent classification,
the customer chatbot, and follow-up suggestions all report `source: "bedrock"`.

```
BEDROCK_ENABLED=true
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-5
BEDROCK_REGION=us-east-1
BEDROCK_API_KEY=<bearer token>      # optional; see below
```

### Which model IDs this account can actually invoke

Only the **`us.` cross-region inference profiles, in us-east-1**. Probed:

| Model ID | Region | Result |
|---|---|---|
| `us.anthropic.claude-sonnet-5` | us-east-1 | ✅ works |
| `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | us-east-1 | ✅ works |
| `global.anthropic.claude-sonnet-5` | either | ❌ explicit deny |
| `…:application-inference-profile/e3rvumlc9t6p` | ap-northeast-2 | ❌ explicit deny |
| `anthropic.claude-sonnet-5` (bare / full ARN) | either | ❌ no on-demand support |

Two separate traps. **One:** Sonnet cannot be invoked on-demand through a bare
`foundation-model` ID — it needs an inference profile. **Two:** invoking through a
profile is authorised against the *profile's* ARN, so
`AIHackathon-Bedrock-ApprovedModels-Only` (which lists only `foundation-model/*`
resources) denies the `global.` profiles and the `motosmart-fork` application
inference profile alike — even though the wrapped model is approved. The `us.`
profiles are the ones that slip through.

### `temperature` on newer models

Sonnet 5 **rejects** `temperature` with a `ValidationException` rather than
ignoring it. `_invoke()` in `services/ai.py` handles this without a hardcoded
model list: on the first rejection it records the model in
`_NO_TEMPERATURE_MODELS` and retries without the parameter, so later calls send
the right shape immediately (one retry per process, per model). Models that still
accept `temperature` keep getting it — classification stays at 0.0.

`BEDROCK_API_KEY` is a bearer token exported as `AWS_BEARER_TOKEN_BEDROCK` (there
is no boto3 client kwarg for it). It needs boto3 ≥ 1.40 — `requirements.txt` is
pinned accordingly. Worth knowing: **an API key authenticates as the IAM
principal that minted it**, so it cannot grant access that principal lacks; it is
a credential format, not a way around a policy.

Every AI call still degrades gracefully — if Bedrock is throttled or
misconfigured, callers get a deterministic heuristic and `source: "fallback"`
rather than a 500. `_bedrock_ready()` gates on `BEDROCK_ENABLED` **and** a
non-empty `BEDROCK_MODEL_ID`, so clearing either reverts to fallbacks.

## Ticketing (service requests)

One resource, two views: the customer's "Service" tab and the dealer's "Tickets"
tab are the same `service_requests` rows and the same `service_request_messages`
thread — the API scopes the list by the caller's role. There is no separate
ticket table.

- **Who can open one**: a **customer only**, for a vehicle they own (both are
  checked). It is routed to `customers.onboarding_dealer_id`; a customer with no
  dealer gets a 409 rather than an orphan ticket no branch can see.
- **Sender identity**: `service_request_messages.sender_type` is `CUSTOMER` or
  `DEALER` and `sender_id` is the customer/employee id. It is polymorphic, so
  there is **no FK** — display names are joined at read time.
- **Both directions notify**: opening a ticket and every customer reply notify
  the branch's active staff (fan-out — a service request has no assigned
  employee); every dealer reply notifies the customer. A dealer reply also moves
  `OPEN → IN_PROGRESS`.

### AI triage

Every ticket is classified on creation into a **category** (ENGINE, BRAKES,
ELECTRICAL, TRANSMISSION, SUSPENSION, TYRES, BODY, PERIODIC_SERVICE, OTHER) and a
**priority** (URGENT, HIGH, NORMAL, LOW), plus a one-line summary for the desk.

Two stages, because a Bedrock round trip takes seconds and nobody should watch a
spinner for that:

1. **Inline** — `ai.heuristic_triage()` runs synchronously (keyword based, 0 ms),
   so the ticket is categorised the instant it is stored.
2. **Background** — a FastAPI background task asks Sonnet 5 and upgrades the row
   if the model answers. Failure is logged and ignored; the heuristic stands.

Safety rules that do not depend on the model: a brake complaint is never below
HIGH, and fuel leaks / burning smells / "brakes not working" are URGENT.

The dealer queue sorts URGENT first (`ticketsListProvider`), and untriaged
tickets sort as Normal so they never sink below routine work.

`python -m app.services.ai` runs the triage self-check (no AI, no database).

## OBD dashboard AI (customer)

The analytics screen is `lib/obd_feature/`'s health dashboard, fed by the in-app
simulator or a live ELM327. **That module is used exactly as shipped — zero files
changed inside it, Bluetooth code included.** Everything added is composed from
the outside:

- **`ObdAiActions`** (`features/vehicles/presentation/`) is an action bar rendered
  *below* the dashboard. It only reads `DashboardProvider.latestReading` /
  `.health`.
- **AI summary button** — on tap it snapshots the readings currently on screen
  (RPM, coolant, speed, battery, throttle, fuel, fault codes, plus the rule
  engine's own verdict) and posts them to
  `POST /vehicles/{id}/telemetry-summary`. The reply is shown in a sheet, labelled
  "AI generated" or "Rule-based" so a demo never claims AI it did not use. The
  readings are snapshotted at the tap, not re-read, because the stream keeps
  ticking.
- **Raise ticket button** — appears when the rule engine is amber/red, a fault
  code is active, or the summary came back `is_actionable`. It opens the normal
  service-request form pre-filled with the suggested type/description and the
  captured diagnostics, which are attached to the thread as their own message and
  fed to the triage. So a ticket from the dashboard is an ordinary ticket: same
  thread, same dealer chat, same triage.
- **Fault explanations** — the module's `AiExplanationService` posts to Anthropic
  with a key baked into the build (empty here, so the card used to read "AI
  explanation unavailable"). `BackendAiExplanationService` subclasses it in the
  app layer and overrides the one method to call
  `POST /vehicles/{id}/dtc-explanation`, so the model call happens server-side and
  no AI key ships in the app. It falls back to the parent's technical description.

Why the app posts readings instead of the server reading `obd_telemetry`: the
dashboard can be driven by a live device whose readings were never persisted, and
the summary has to describe what the rider is actually looking at.

## Incentives

Two acts earn money, each attributed to the person who performed it:

| Act | Who is paid | Default | Where it is recorded |
|---|---|---|---|
| Converting a lead into a customer | whoever ran `POST /leads/{id}/convert` | **₹1,500** | `leads.converted_by_employee_id` + `converted_at` |
| Closing a service ticket (→ RESOLVED) | whoever moved it | **₹300** | `service_requests.resolved_by_employee_id` + `resolved_at` |
| Completing a test ride | the generated lead's assignee | ₹100 | `test_ride_bookings.status` |

A dealer overrides any amount with an `incentive_rules` row for that event type;
`DEFAULT_AMOUNTS` in `services/incentives.py` applies otherwise.

**A lead that is lost or still open pays nothing** — the query requires
`CLOSED_WON` *and* a linked customer. **Re-opening a ticket withdraws its
incentive**: the resolver field is cleared, so the next recompute drops it.
Closing it again restores it.

Credit follows the actor, not the assignee: if a lead assigned to A is converted
by B, **B is paid**. Both timestamps are set at the moment of the act, so a later
edit to the row cannot move an earned incentive into a different month.

Leads *created* are counted for context but never paid — capturing an enquiry is
the job; closing it is the achievement.

Figures are derived, never accumulated, so `recompute` is idempotent:

```bash
curl -X POST localhost:8000/api/v1/internal/incentives/recompute \
  -H 'X-Internal-Key: dev-internal-key' -H 'Content-Type: application/json' -d '{}'
```

`GET /incentives?month=YYYY-MM` computes on first read when nothing is stored, so
the screen is never blank. `python -m app.services.incentives` checks the
arithmetic with no database.

## Converted customers can sign in

Converting a lead provisions a Cognito user in the `CUSTOMER` group and links its
`sub` to the `customers` row. Both halves matter: without the user, asking for a
sign-in code delivers nothing; without the `sub`, a verified token cannot be
mapped back to the row and every authenticated call 403s.

Three things made this silently fail:

| Cause | Fix |
|---|---|
| `invite` defaulted to **false** and the app never sent it, so `provision_customer` never ran | default is now **true** — pass `invite: false` to record a customer without a login |
| The pool marks `phone_number` **required**, and hand-typed leads carry `9464674949`, not E.164 | `cognito.to_e164()` normalises; a ten-digit number is assumed Indian, an unrecognisable one is refused with a readable reason rather than a schema error |
| `invited` was computed and then discarded from the response | returned, alongside `invite_error`, and shown in the convert dialog |

Conversion never fails because Cognito did — the customer row is created either
way, and `invited: false` with a reason tells the dealer they have a customer who
cannot yet sign in. `AdminCreateUser` uses `MessageAction=SUPPRESS`: sign-in is
passwordless, so the temporary password Cognito would email is unusable noise.

Customers converted before this defaulted on have no login. Backfill them:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.backfill_customer_logins          # dry run
PYTHONPATH=. .venv/bin/python -m scripts.backfill_customer_logins --apply
```

## Gotchas

- **CORS**: allowed origins are `localhost:3000/8080` and `127.0.0.1:8080`. A
  random `flutter run` web port gets a 400 on preflight — pin `--web-port=8080`,
  or add the port to `CORS_ORIGINS`.
- **Test-ride dealer**: the booking screen has no location picker, so no
  `dealer_id`/`pincode` is sent and the backend's `resolve_dealer` falls through
  to its deterministic default branch. Add a picker to route leads by branch.
- **Bedrock runs in a different region from everything else.** The account is
  approved for **us-east-1** only, while Cognito/RDS/S3 are in `ap-northeast-2`,
  so `BEDROCK_REGION` overrides the region for `bedrock-runtime` alone
  (`settings.bedrock_region`, used by `aws.bedrock_client()`). Don't "fix" this
  by moving `AWS_REGION` — that would break Cognito and the database.
- **The model ID must be a cross-region inference profile**:
  `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Sonnet cannot be invoked
  on-demand through a bare `foundation-model` ID or ARN — that returns
  `ValidationException`. The `global.` prefixed profile is denied by the
  `AIHackathon-Bedrock-ApprovedModels-Only` policy; the `us.` one is allowed.

## Verifying

`test/api_integration_test.dart` drives every API repository against a running
backend — the check that actually proves the contract, since responses are parsed
through the same models the app uses:

```bash
RUN_API_TESTS=1 flutter test test/api_integration_test.dart   # 15 tests
```

Hermetic checks that need nothing running:

```bash
flutter test                              # incl. Cognito auth + auth-header tests
python -m app.services.ai                 # triage + telemetry-fallback self-check
python -m app.services.incentives         # incentive arithmetic self-check
```

It is skipped unless `RUN_API_TESTS=1`, so plain `flutter test` stays hermetic.

Known pre-existing failure, unrelated to integration (fails on the original
commit too): `widget_test.dart` › "Guest can book a test ride without logging
in" — `pumpAndSettle` times out on the mock booking screen.
