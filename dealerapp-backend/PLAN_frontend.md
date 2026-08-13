# PLAN.md — Smart Dealer Enquiry App (Frontend)

> Drop this at the root of the **Flutter** repo as `PLAN.md`. It is the build plan for Claude Code. It consumes the FastAPI backend described in the backend repo's PLAN.md.

## What we're building

A single Flutter app for the YMSLI Smart Dealer Enquiry App (Hackathon PS-06) with two role experiences behind one login, plus a public (no-login) browsing funnel:

- **Public/guest** — browse bikes, check exchange value, book a test ride (booking auto-creates a lead on the backend).
- **Dealer staff** — capture & work leads, follow-ups, statuses, test-ride requests, customer onboarding, incentive view.
- **Customer** — vehicle analytics, service status/history, service-request threads, chatbot.

Build in 4 phases matching the backend. Each phase is independently runnable.

## Tech stack (non-negotiable)

- **Flutter (stable)**, **Dart 3**
- **Riverpod** (`flutter_riverpod`) for state management
- **go_router** for navigation + **role-based redirect guards**
- **dio** for HTTP, with an auth interceptor that attaches the Cognito JWT
- **amplify_flutter** + **amplify_auth_cognito** for auth (Cognito OTP sign-in). *(Alternatively call Cognito's InitiateAuth/RespondToAuthChallenge directly via dio — but Amplify handles the USER_AUTH OTP challenge flow for you.)*
- **flutter_secure_storage** for token persistence
- **drift** (SQLite) for offline lead cache (Phase 4)
- **fl_chart** for OBD/analytics charts (Phase 3)
- **flutter_local_notifications** for surfacing in-app notifications as OS notifications (no Firebase) (Phase 2)
- **freezed** + **json_serializable** for models

## Conventions

- Feature-first folder layout; shared stuff in `core/`.
- All network access goes through a typed **repository** per domain; widgets never call `dio` directly.
- Riverpod providers expose repositories and async state (`AsyncValue`).
- Models are `freezed` immutable classes with `fromJson`/`toJson`.
- No secrets in code — Cognito pool/client IDs and API base URL come from a generated `lib/core/config/env.dart` (from `--dart-define` or a config file).
- Every screen handles the three states explicitly: loading, error (with retry), data.
- Role gating lives in the router redirect, not scattered in widgets.

## Directory structure

```
lib/
  main.dart
  core/
    config/            # env (API base URL, Cognito IDs), theme, constants
    network/           # dio client + auth interceptor + error mapping
    auth/              # Amplify/Cognito wrapper, session state, role enum
    router/            # go_router config + redirect guards
    widgets/           # shared UI (status chip, empty state, async builder)
  features/
    public/            # catalog, bike detail, exchange value, test-ride booking
    leads/             # list, detail, form, follow-ups
    dashboard/         # dealer home dashboard
    customers/         # onboarding / convert
    vehicles/          # analytics, service status/history
    service/           # service requests + message threads
    chatbot/
    incentives/
    notifications/
    profile/
  models/              # freezed DTOs shared across features
```

Each feature folder: `data/` (repository + providers), `presentation/` (screens + widgets).

## Auth flow (Cognito OTP)

1. Configure Amplify with the User Pool ID + App Client ID (from `env.dart`).
2. Login screen: user enters email/phone → `Amplify.Auth.signIn` with the passwordless/USER_AUTH flow → Cognito sends OTP → app shows OTP entry screen → `confirmSignIn(otp)`.
3. On success, pull the session's **JWT** and store it in `flutter_secure_storage`.
4. dio auth interceptor attaches `Authorization: Bearer <jwt>`; on 401, refresh via Amplify or route to login.
5. Read `cognito:groups` from the token to get the role; drive the post-login landing route.
6. `GET /me` confirms the backend profile and role.

## Router & role gating (go_router)

- Public routes (`/`, `/models`, `/models/:id`, `/exchange`, `/book-test-ride`) reachable without a session.
- `/login`, `/otp`.
- Authenticated shells by role:
  - `DEALER_STAFF` → `/dealer` (leads, test-rides, onboarding, incentives, notifications, profile)
  - `CUSTOMER` → `/customer` (vehicles, service, chatbot, notifications, profile)
- Redirect guard: no session + protected route → `/login`; wrong role for a route → that role's home.

## Global setup (do this first)

1. `flutter create`, add dependencies listed above.
2. `lib/core/config/env.dart` — API base URL + Cognito IDs (via `--dart-define`).
3. `core/network/dio_client.dart` — dio instance, base URL, auth interceptor, error → typed `ApiException` mapping.
4. `core/auth/` — Amplify configuration + `authControllerProvider` exposing session/role state.
5. `core/router/app_router.dart` — go_router with the redirect guard above.
6. `core/config/theme.dart` — Yamaha-flavored theme (deep blue / red accents), reusable `StatusChip` widget for lead statuses.
7. `core/widgets/async_value_widget.dart` — one place to render loading/error/data.

---

# Phase 1 — Core dealer flow + mandatory AI

**Deliverable:** dealer logs in via OTP, sees dashboard, lists/creates/edits leads, works follow-ups, sees the AI intent badge. Runnable alone.

### Models (`freezed`)
`Employee`, `Dealer`, `Customer`, `BikeModel`, `Lead` (incl. `status`, `aiIntent`, `source`), `LeadFollowup`, `DashboardSummary`.

### Repositories / providers
- `AuthRepository` (Amplify wrapper) + `MeRepository` (`GET /me`)
- `LeadsRepository`: list (status/search filters), get, create (walk-in), update, convert, follow-ups CRUD, `classifyLead` (`POST /ai/classify-lead`)
- `DashboardRepository`: `GET /dashboard/summary`

### Screens
1. **Login** (email/phone entry) → **OTP entry**
2. **Dealer home dashboard** — today's follow-ups + open leads cards
3. **Leads list** — search bar, status tabs (New/Follow-up/Closed), each card shows a **colored intent badge** (HOT/WARM/COLD) and status chip
4. **New lead form** — model dropdown (from `bike_models`), current bike, tentative date, notes; on save, optionally trigger classify and show the badge
5. **Lead detail** — fields + follow-up timeline + status change control + "Convert to customer" action
6. **Add/edit follow-up** — next action + scheduled date
7. **Customer onboarding** (convert) — minimal form → calls `POST /leads/{id}/convert`
8. **Profile** — name/role, logout

---

# Phase 2 — Public funnel + notifications

**Deliverable:** guest browsing + test-ride booking (which creates a lead server-side), and dealers receive notifications.

### Models
`ExchangeEstimate`, `TestRideBooking`, `AppNotification`.

### Repositories / providers
- `PublicRepository`: `GET /public/models`, `/public/models/:id`, `POST /public/exchange-value`, `GET /public/availability`, `POST /public/test-rides`
- `TestRideRepository` (dealer): list, confirm/complete
- `NotificationsRepository`: list (`GET /notifications`), mark read (`PATCH /notifications/{id}/read`)

### Screens
- **Public catalog** (grid of bikes) → **Bike detail** (specs, price, availability, brochure link) → **Exchange value estimator** (form → estimate) → **Test-ride booking** (name, mobile, date/time, **dealer/location select** — required so the backend can route the lead)
- **Dealer test-ride requests** — list + confirm/complete
- **Notifications** screen + unread badge on dealer home
- Notification wiring (Firebase-free): a Riverpod poller hits `GET /notifications` on app launch, on resume (`AppLifecycleState.resumed`), and on a light periodic timer while foregrounded. New unread items update the badge on dealer home and are surfaced via `flutter_local_notifications`. Tapping a local notification deep-links (go_router) to the related lead or test-ride using the notification's `payload_json`.

---

# Phase 3 — Customer side (vehicles, service, chatbot)

**Deliverable:** customer logs in, views analytics + service status, opens service threads, chats with the assistant.

### Models
`Vehicle`, `ServiceRecord`, `ServiceStatus` (last + next by time & km), `ServiceRequest`, `ServiceMessage`, `ObdTelemetry`, `ChatMessage`.

### Repositories / providers
- `VehiclesRepository`: `GET /me/vehicles`, analytics, service-status, service-history
- `ServiceRepository`: requests list/create/get, messages list/send
- `ChatbotRepository`: send message, history

### Screens
1. **Customer home** — vehicle overview card
2. **Bike analytics** — `fl_chart` dashboards from OBD telemetry (odometer trend, battery, fuel, etc.)
3. **Service status** — last service (date + km) and next due (date + km), with a due/overdue indicator
4. **Service history** — list of `service_records`
5. **New service request** → **Request thread** — chat-style message list; customer + dealer turns
6. **Chatbot assistant** — chat UI backed by `POST /chatbot/message`
7. **Notifications, Profile**

---

# Phase 4 — Incentives, offline & polish

**Deliverable:** incentive view + good-to-have polish.

### Models
`IncentiveSummary`, `EmployeeIncentive`.

### Repositories / providers
- `IncentivesRepository`: `GET /incentives?month=`, per-employee
- Offline: `drift` DB mirroring leads; a sync service that queues writes when offline and flushes on reconnect

### Screens & polish
- **Incentives** — monthly per-employee breakdown + dealer total (simple table/cards), reachable from the dealer shell
- **Leads list polish** — due/overdue follow-up tags, richer filters
- **Duplicate mobile warning** — on new lead / booking, surface the backend's warning inline
- **Offline save** — capture a lead with no network → stored locally, badge "pending sync", auto-syncs on reconnect

---

## Definition of done per phase
- Every screen handles loading/error/data; errors show a retry.
- All calls go through a repository + Riverpod provider (no dio in widgets).
- Role-based routing verified for each role.
- Runs against the live backend base URL from `env.dart`.
- No secrets committed; Cognito IDs + API URL injected via `--dart-define`.
