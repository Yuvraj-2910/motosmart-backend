# chatbot_plan_frontend.md — dealer chatbot + new demo data, for the Flutter app

Everything below is written to be **pasted as-is into a Claude Code session opened
in the `motosmart-app2.0` (Flutter) repo**. It is addressed to that session, not
to a reader of this repo, which is why it says "this app" and "this repo" meaning
the frontend.

Backend side is already done and verified live: migration `0007_dealer_chatbot`
applied, both endpoints answering on Bedrock (`source: "bedrock"`), and the
database reset to the minimal demo set described in Part 2. See `INTEGRATION.md`
for the backend's own account of the feature.

---

# Task

Two additive changes to this Flutter app (`motosmart-app2.0`), against the live FastAPI backend at `http://localhost:8000`:

1. Wire up the **new dealer-side chatbot** (staff assistant over their branch's leads + tickets).
2. Update for the **new minimal dev/demo dataset** — the old seeded demo identifiers no longer exist.

**Hard constraint: do not break any existing functionality.** This is being demoed live today. Everything is additive or a value swap. No refactors, no renames of existing files, no changes to existing screens except where explicitly listed below. Keep mock mode (`--dart-define=USE_MOCK_DATA=true`) fully working.

Read `PLAN.md` (and `INTEGRATION.md` if it is present in this repo) first, then look at how the **existing customer chatbot** is actually wired. The conventions — one abstract repository per domain, dio impl in `lib/data/api/`, mock impl behind the same interface, a Riverpod provider choosing between them via `useMockDataProvider`, coercion through `models/json_utils.dart`, failures mapped to `ApiException` by `core/network/dio_client.dart` — are non-negotiable. Widgets never touch dio. If what you find on disk differs from anything described below, the code wins; tell me rather than reshaping the code to match this prompt.

---

## Part 1 — Dealer chatbot

The backend already ships this. It is the staff-side twin of the existing customer chatbot: same request/response shape, same envelope, same `source` field. **Mirror the existing `features/chatbot/` implementation rather than inventing a new pattern** — that is the point of this design.

### Endpoints

Both require a `DEALER_STAFF` caller (a customer token gets `403`).

**`POST /api/v1/dealer/chatbot/message`**

```json
// request
{ "message": "Which leads need a follow-up today?", "conversation_id": null }
```

`message`: 1–4000 chars. `conversation_id`: omit or send `null` to continue the employee's latest conversation (the backend creates one on first send). Passing another employee's `conversation_id` returns `404`.

```json
// response
{
  "conversation_id": "fd002084-4967-4c89-95c7-c6ec96989200",
  "user_message":      { "id": "...", "conversation_id": "...", "role": "USER",      "content": "...", "created_at": "2026-08-14T07:59:12.082263Z" },
  "assistant_message": { "id": "...", "conversation_id": "...", "role": "ASSISTANT", "content": "...", "created_at": "2026-08-14T07:59:12.082263Z" },
  "source": "bedrock"
}
```

**`GET /api/v1/dealer/chatbot/history?conversation_id=<uuid>&limit=100`**

Both params optional (`limit` 1–200, default 100). Omit `conversation_id` for the latest conversation.

```json
{ "conversation_id": "fd002084-...", "messages": [ { …DealerChatMessage… } ] }
```

**A dealer who has never chatted gets `{"conversation_id": null, "messages": []}`** — that is a normal empty state, not an error. Handle the null id.

### Contract details that will bite if missed

- `role` is uppercase `"USER"` / `"ASSISTANT"`, same as the customer chatbot — reuse the existing `ChatMessage` model / `ChatRole` parsing if it already handles that casing. Do not add a second message model unless the existing one genuinely cannot be reused.
- The `{user_message, assistant_message}` envelope must be **unwrapped in the repository**, exactly as `INTEGRATION.md` records for the customer chat. The screen should receive messages, not the envelope.
- Both messages come back with the **same `created_at`**, so never sort the transcript by timestamp alone — append in the order returned (user, then assistant).
- `source` is `"bedrock"` or `"fallback"`. When it is `"fallback"` Bedrock was unreachable and the reply is a canned "check the Leads or Tickets screen" line. Label it in the UI the same way `ObdAiActions` already distinguishes "AI generated" from "Rule-based" — a demo must never claim AI it did not use. Do not treat `fallback` as an error; it is a `200`.
- The assistant is prompted to emit **plain sentences, with `**bold**` for a name or status and `-` bullets for actual lists**. No headings, tables, or emoji. If the existing customer chat bubble renders raw text, `**Karan Malhotra**` will show literal asterisks — render inline bold + `-` bullets (a small inline-markdown span builder is fine; do not add a heavy markdown dependency), and apply the same treatment to the customer chat bubble only if it shares the widget.
- It is **read-only**. It answers about leads/tickets; it cannot create, assign, or update anything. Do not build action buttons off its replies.
- It only ever sees the caller's **own branch**. Rohan (Andheri) cannot ask about Whitefield's leads, and the answer will correctly say it has no such record.

### What to build

- `DealerChatbotRepository` interface + `ApiDealerChatbotRepository` (dio, in `lib/data/api/`) + a mock impl returning a couple of canned turns, wired through a provider that respects `useMockDataProvider`.
- A dealer assistant screen under `features/chatbot/` (staff variant) reachable from the **dealer shell** — a nav entry / tab alongside Leads, Tickets, Incentives, plus a route such as `/dealer/assistant` registered in `core/router/app_router.dart` and gated to `DEALER_STAFF` by the **existing router redirect guard**, not by widget-level checks.
- On open: load history, then keep the returned `conversation_id` in state and send it on subsequent messages.
- Optimistically show the user's bubble plus a typing indicator (a Bedrock round trip takes seconds), then reconcile with the response. On failure, surface the error with a retry that does not lose the typed message.
- Loading / error-with-retry / data states handled explicitly, per the repo's definition of done.
- Suggested starter chips, since a blank chat box demos badly. Use questions the current dataset can actually answer:
  - "Which follow-ups are due today?"
  - "Any urgent tickets right now?"
  - "What's the status of Karan Malhotra?"
  - "How many open leads do I have?"

Verified working example against the live backend (Andheri staff): *"Which leads need a follow-up today, and is any ticket urgent?"* → *"**Karan Malhotra** (R15 V4, HOT, finance approved) is the lead due for follow-up today … On tickets, yes: **Vyom Sharma**'s brake ticket (MT-15, MH02VY1234) is **OPEN** and marked **URGENT**…"* with `source: "bedrock"`.

---

## Part 2 — New dev/demo dataset

The database was reset to a minimal demo set. **Every previously seeded identifier is gone.** Anything in this repo that hardcodes one — prefilled login fields, quick-login/demo-account buttons, mock fixtures, integration-test constants, README/docs tables — must be updated or it will fail on stage.

Removed: `rohan@ymsli-demo.example`, `arjun@…`, `sneha@…`, `vikram@…`, `test.customer@ymsli-demo.example`.

### The accounts that now exist

| Role | Dealer | Sign-in | Identifier to use |
|---|---|---|---|
| Dealer staff — Rohan Mehta | YMSLI Andheri (MUM-AND, Mumbai) | **Real Cognito email OTP** | `ijklmnop7417@gmail.com` |
| Dealer staff — Priya Nair | YMSLI Whitefield (BLR-WHF, Bengaluru) | Dev header | `priya@ymsli-demo.example` |
| Customer — Vyom Sharma | Andheri | **Real Cognito email OTP** | `vyom5212@gmail.com` |
| Customer — Amit Kumar | Andheri | Dev header | `amit@ymsli-demo.example` |
| Customer — Neha Sharma | Whitefield | Dev header | `neha@ymsli-demo.example` |

**Important asymmetry:** the dev-header shortcut sends `X-Dev-User: <identifier>:` and the backend matches it against `cognito_sub`. For the three dev accounts `cognito_sub` *is* the email above, so they work as before. But **Rohan's and Vyom's `cognito_sub` are real Cognito subs, not emails** — so they cannot be dev-header logins by email. Their dev-header equivalents, if you need one for testing, are the raw subs:

- Rohan (DEALER_STAFF): `94080dfc-b091-70a5-8b52-bef6f44bf966`
- Vyom (CUSTOMER): `84e8ddbc-2001-7037-cc8e-d9808f24ac5f`

For any demo-account shortcut UI, prefer **Priya for dev dealer staff** and **Amit/Neha for dev customers**, and let Rohan and Vyom be the real-OTP demo path. Do not paste raw subs into user-facing copy.

### Real OTP path — confirmed working, note the delivery channel

The pool's only first auth factor for these users is **`EMAIL_OTP`**. Verified: `InitiateAuth` with `AUTH_FLOW=USER_AUTH` and just `USERNAME` returns `ChallengeName: "EMAIL_OTP"` directly — no `SELECT_CHALLENGE` step to handle — so the existing dio-based `InitiateAuth` → `RespondToAuthChallenge` flow should work unchanged. Sending `PREFERRED_CHALLENGE=EMAIL_OTP` is harmless and makes the intent explicit; add it only if it does not disturb the existing code path.

**The code arrives by email, not SMS.** Audit the login/OTP screens for copy that says "SMS", "text message", or "sent to your phone" and correct it to email, and make sure the OTP screen accepts an email address as the identifier (the pool's usernames are email + phone). Both accounts do have a phone number on file, but nothing is delivered to it.

### Data now on screen (for fixtures and expectations)

2 dealers × 1 employee, 3 customers, 4 leads, 1 open ticket. Bike catalog (10 models) and exchange-value rows are unchanged.

- **Andheri / Rohan** — leads: Vyom Sharma (`CLOSED_WON`, HOT, converted to the customer account) and Karan Malhotra (`NEW`, HOT, R15, follow-up **due today**). One `OPEN` / `URGENT` brake ticket from Vyom on an MT-15 (`MH02VY1234`), with one customer message on the thread.
- **Whitefield / Priya** — leads: Divya Reddy (`FOLLOW_UP`, WARM, Aerox, follow-up in 2 days) and Sanjay Gupta (`NEW`, COLD, Fascino, follow-up **overdue**).
- Vehicles: Vyom → MT-15 `MH02VY1234` (one past service record, next due in 60 days), Amit → Fascino `MH02AK5678`, Neha → R15 `KA05NS9012`.

Consequences to check rather than assume:

- Each dealer has **exactly one employee**, so any assignee dropdown, round-robin display, or leaderboard now renders a single row. Verify these degrade gracefully — no empty-list crash, no "no assignee" fallback firing wrongly.
- `employee_incentives` was cleared. The incentives screen computes on first read, so it should populate for the converted Vyom lead — confirm it renders rather than erroring on an empty month.
- There are **no notifications** yet; the unread badge starts at zero. Confirm the poller handles an empty list.

---

## Verify before you finish

```bash
flutter analyze
flutter test                                                    # must stay green
RUN_API_TESTS=1 flutter test test/api_integration_test.dart      # update its identifiers first
flutter run -d chrome --web-port=8080                            # port matters: CORS allows :8080 only
```

`test/api_integration_test.dart` is the check that actually proves the contract — extend it with the two dealer-chatbot endpoints (send a message, then read history back) and update its account constants to the table above.

Known pre-existing failure, unrelated — do not chase it: `widget_test.dart` › "Guest can book a test ride without logging in" (`pumpAndSettle` times out on the mock booking screen).

Then manually walk: dev-login as `priya@ymsli-demo.example` → dealer assistant → ask "Which follow-ups are due today?" → expect a grounded answer naming Divya/Sanjay for Whitefield, with the AI-source label showing. Then real-OTP as `ijklmnop7417@gmail.com` → same screen → expect the Andheri answer (Karan Malhotra, Vyom's urgent brake ticket).

Finally, update any doc in this repo that lists demo accounts so it matches the table above.
