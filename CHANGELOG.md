# Changelog

Every change to this project is recorded here. Newest first.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entry categories: **Added**, **Changed**, **Fixed**, **Removed**,
**Deprecated**, **Security**.

---

## [Unreleased]

### Added

- **TextBee.dev as a second SMS provider** (`dissemination/gateways.py`).
  `TextBeeClient` sends through a paired Android phone's own SIM via the
  TextBee API, matching `M360Client`'s `DeliveryResult` contract so the
  dispatcher and health check do not need to know which gateway is active.
  A new `SMS_PROVIDER` setting (`m360` | `textbee`) selects it; `get_sms_client()`
  centralises the choice so a third gateway later is a new client class plus
  one branch, not a search-and-replace. `TextBeeClient.device_status()`
  reports the paired device's reachability for the System Health Check,
  since TextBee has no prepaid credit balance the way M360 does.
- **`/api/webhooks/textbee/`** (`api/webhooks.py`) for inbound SAFE/HELP
  replies via TextBee, reusing `accountability.services.handle_inbound_sms`.
  Field names are read tolerantly (several possible spellings for the sender
  and the message body), the same pattern as the M360 inbound view, since
  TextBee's exact payload shape has not been pinned against a live account.
- **Header-free webhook authentication for TextBee.** TextBee's own webhook
  configuration has no way to set a custom header, so `TEXTBEE_WEBHOOK_TOKEN`
  is designed to travel as `?token=...` in the callback URL - the existing
  `_token_ok` helper already supported a query-string token as an
  alternative to the `X-Webhook-Token` header, so this needed no change to
  that helper, only a new view that uses it. Leaving the token unset accepts
  the callback with no credential at all (the existing degrade-with-a-warning
  behaviour every webhook already has), for wiring the integration up before
  a token is chosen.
- `.env.example` / `.env`: `SMS_PROVIDER`, and a `TEXTBEE_*` block
  (`TEXTBEE_ENABLED`, `TEXTBEE_API_KEY`, `TEXTBEE_DEVICE_ID`,
  `TEXTBEE_BASE_URL`, `TEXTBEE_TIMEOUT`, `TEXTBEE_WEBHOOK_TOKEN`) alongside
  the existing M360 block.
- Tests: `TestTextBeeClient`, `TestGetSmsClient` and a provider-switch case in
  `TestDeliverNotification`/`TestHealthCheck` in `tests/test_dissemination_services.py`;
  `TestTextBeeInboundWebhook` in `tests/test_api.py`, including a test that
  the query-string token works with no header at all, and a test that a
  configured token is still enforced.

### Fixed

- The sidebar did not highlight **Drills** on a drill detail page, and the
  page had no way back to the list. Vue Router's `active-class` matches on
  route *records*, not path prefixes, and `/drills` and `/drills/:id` are
  separate flat records — so the built-in matching never fired.
  `App.vue` now compares paths itself and sets `aria-current`, and
  `DrillMonitorView` has an "All drills" back link.

### Changed

- Replaced the bare `setInterval` polling in `DrillMonitorView` and
  `OverviewView` with a `usePolling` composable. The old version fired on a
  fixed interval whether or not the previous request had returned: measured
  against a server taking 8s per poll, it opened 6 requests with 3 in flight
  at once, and concurrency grows without bound the slower the server gets.
  Browsers allow roughly six connections per host, so a loaded server could
  starve the requests a click depends on — the page still renders, but
  nothing responds until a reload. The composable holds it to one request at
  a time (same measurement: 2 polls, 1 concurrent), aborts in flight on
  unmount, pauses in a hidden tab, backs off after failures, and stops once a
  drill is no longer active.

  Note: the pile-up is reproducible, but the click failure it can cause was
  not reproduced directly — at 3 concurrent requests clicks still worked.
  This removes the mechanism rather than a confirmed instance of it.

### Added

- `.gitattributes` normalising the repository to LF. Without it a Windows
  checkout can commit CRLF into `entrypoint.sh`, and the Linux container then
  fails to start with an unhelpful "no such file or directory" on the shebang.
- `ruff.toml` pinning the lint rules, with Django and DRF idioms exempted
  (`RUF012`) and the deliberate broad excepts in the dissemination and mirror
  layers exempted (`BLE001`).
- `DJANGO_SSL_REDIRECT` (default on in production) so `check --deploy` passes
  with no outstanding transport warnings. Safe behind Cloud Run because
  `SECURE_PROXY_SSL_HEADER` is already set.

### Fixed

- A fresh clone failed on `manage.py migrate` with "unable to open database
  file": the default SQLite path lives in `data/`, which is gitignored and so
  never exists on checkout. Settings now create the directory on demand.
- The test suite inherited webhook secrets from the developer's local `.env`,
  so the webhook tests failed on any machine that had one. `tests/conftest.py`
  now pins those settings; the tests that assert enforcement set their own.

---

## [1.0.0] — 2026-09-19

Initial implementation of the Web-based Automated Call Tree System, built to
the specification in *Web-based Automated Call Tree System for Efficient
Earthquake Drill Notification and Coordination*.

### Added

**Domain model** (`core/`)
- `Location` — geofenced work site carrying its own Trigger Filter values
  (magnitude threshold, risk radius) and evacuation instruction.
- `Employee` — DSO directory entry with contact routes, role, escalation tier
  and per-channel opt-in.
- `SeismicEvent` — a USGS report, de-duplicated on `usgs_id`.
- `Drill` — one call tree activation, scheduled or incident-driven.
- `DrillParticipant` — per-person accountability record, including the unique
  status-link token and response latency.
- `Notification` — one row per outbound message per channel, with the
  timestamps the latency measurement reads.
- `InboundMessage` — raw inbound SMS, stored before interpretation.
- `AuditLog` — append-only operator and system action trail.

**Module 1 — USGS Data Ingestion** (`ingestion/`)
- Parsers for the GeoJSON summary feed, ENS webhooks (Feature,
  FeatureCollection and flat ENS JSON) and forwarded ENS email alerts.
- `poll_usgs` management command, single-shot or looping.

**Module 2 — Proximity Analysis & Logic Engine** (`engine/`)
- Haversine great-circle distance, clamped against domain errors at
  antipodal points.
- The Trigger Filter: per-location magnitude threshold AND risk radius, with
  a human-readable reason for every verdict.
- Call tree orchestration: roster construction, activation, response
  recording, the reminder and escalation sweep, drill completion and the
  compliance statistics.

**Module 3 — Cloud Dissemination** (`dissemination/`)
- M360 SMS client with configurable endpoint and payload, simulating
  delivery when disabled.
- SMTP email channel over Django's mail backend.
- Cloud Pub/Sub publisher with a local thread-pool fallback, so the system
  keeps alerting when Pub/Sub is unreachable.
- `run_dispatcher` worker: drains the queue and sweeps active drills.
- System health check covering database, gateway balance, email, Pub/Sub,
  Firestore and queue depth.

**Module 4 — Accountability & Feedback** (`accountability/`)
- Inbound SMS matching by normalised MSISDN against the active drill.
- SAFE/HELP keyword interpretation, tolerant of real-world phrasing and
  accepting Filipino ("ligtas", "tulong").
- One-tap web status links, usable without an account.

**REST API** (`api/`)
- Session authentication, directory CRUD, drill lifecycle actions, the live
  monitor endpoint, CSV compliance export, event simulation and manual
  status override.
- Webhooks for USGS ingestion, M360 inbound SMS and Pub/Sub push, each
  guarded by a shared secret.

**Dashboard** (`frontend/`)
- Vue 3 + Vite + Tailwind SPA, built to `frontend/dist` and served by Django.
- Overview, drills, live drill monitor (5-second refresh), directory,
  geofences, seismic feed with a Trigger Filter simulator, system health.
- Employee status page designed for one-handed use during an evacuation.

**Infrastructure**
- Multi-stage Dockerfile: Node builds the SPA, Python serves it; non-root
  runtime user and a container health check.
- Docker Compose stack: Postgres, web, dispatcher worker, optional poller.
- Entrypoint with `web` / `worker` / `poller` / `migrate` roles.
- `.env.example` documenting every variable; `.gitignore` and `.dockerignore`
  scoped to keep secrets and build artefacts out.
- `seed_demo` command for the evaluation walkthrough.

**Tests** — 245 tests concentrated on the service layer, covering the Trigger
Filter, call tree orchestration, dispatch idempotency, the two-way reply loop,
USGS parsing, and the API and webhooks. All external gateways are simulated.

### Fixed

- `activate_drill` rebound its local name to a freshly fetched row, so the
  caller's `Drill` instance was left stale after activation. The lock is now
  taken on a separate handle and the caller's instance is the one mutated.
  Found by `tests/test_engine_services.py::TestActivateDrill`.

### Security

- Webhook endpoints compare their shared secret with `hmac.compare_digest`,
  and log a warning when a token is unset.
- The SPA uses same-origin session authentication, so no access token is
  parked in browser storage.
- `.env` and service-account JSON are excluded from both git and the Docker
  build context.
- Non-staff accounts are refused at the login endpoint.
