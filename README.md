# Web-based Automated Call Tree System

Earthquake drill notification and personnel accountability for the **Digital
Service Operations (DSO) Team**.

The system ingests seismic events from the USGS Earthquake Notification
Service, decides whether they threaten a geofenced work site, broadcasts
location-aware alerts over SMS and email, and tracks every "SAFE" or "HELP"
reply on a live dashboard — replacing the manual phone tree that breaks down
under stress.

Django 5 + Django REST Framework · Vue 3 + Vite + Tailwind · Google Cloud
Platform (Cloud Run, Firestore, Pub/Sub) · M360 or TextBee SMS gateway.

---

## Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Running with Docker](#running-with-docker)
- [Configuration](#configuration)
- [Walking through a drill](#walking-through-a-drill)
- [API reference](#api-reference)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Deploying to Cloud Run](#deploying-to-cloud-run)
- [Operational notes](#operational-notes)

---

## How it works

Four modules, matching the system architecture:

```
  USGS ENS                    ┌──────────────────────────┐
  feed / webhook / email ───▶ │ 1. Data Ingestion        │  normalises to a
                              │    ingestion/usgs.py     │  SeismicEvent
                              └────────────┬─────────────┘
                                           ▼
                              ┌──────────────────────────┐
                              │ 2. Proximity Analysis &  │  haversine distance
                              │    Logic Engine          │  + Trigger Filter:
                              │    engine/proximity.py   │  magnitude ≥ threshold
                              └────────────┬─────────────┘  AND within radius
                                           ▼
                              ┌──────────────────────────┐
                              │ 3. Cloud Dissemination   │  Pub/Sub fan-out ▶
   SMS (M360 or TextBee) ◀────│    dissemination/        │  one active SMS
   SMTP mail ◀────────────────│                          │  provider + SMTP
                              └────────────┬─────────────┘
                                           ▼
  employee replies            ┌──────────────────────────┐
  SMS "SAFE"/"HELP"  ────────▶│ 4. Accountability &      │  updates the roster,
  or taps a status link       │    Feedback              │  halts reminders,
                              │    accountability/       │  escalates to the EMT
                              └────────────┬─────────────┘
                                           ▼
                                  Live dashboard (Vue 3)
```

**The Trigger Filter** is the decision at the heart of the system. Each
geofenced location carries its own magnitude threshold and risk radius, so a
data centre can be set more sensitive than an office. An event activates a
location's call tree only when *both* conditions hold.

**Escalation** runs on a clock. Non-responders get reminders at the configured
interval up to a maximum; once the compliance window closes, they are flagged
non-compliant and the Emergency Management Team is notified with the list of
people still unaccounted for.

**Nothing silently disappears.** An employee with no mobile number gets a
`SKIPPED` notification row explaining why. An SMS from an unrecognised number
is stored for the Safety Officer to reconcile by hand. Every operator action
lands in the audit log.

### Degrading gracefully

Each cloud dependency is optional, and the system stays fully functional
without it:

| Dependency | Enabled | Disabled (the default) |
|---|---|---|
| Cloud Pub/Sub | Fan-out via a push subscription | Local thread-pool dispatcher |
| Cloud Firestore | Live status mirrored for real-time clients | Relational storage only |
| M360 / TextBee gateway | Real SMS, real cost | Delivery simulated and logged |
| SMTP | Real email | Console backend |

### Two SMS providers

`SMS_PROVIDER` picks which gateway actually sends: `m360` (a Philippines SMS
API) or `textbee` (a self-hosted-friendly gateway that sends through a
paired Android phone's own SIM, via [textbee.dev](https://textbee.dev)). Both
adapters live in [`dissemination/gateways.py`](dissemination/gateways.py) and
share the same `DeliveryResult` contract, so the rest of the system does not
know or care which one is active. Each still simulates delivery when its own
`*_ENABLED` flag is off, independent of which one `SMS_PROVIDER` names.

This is deliberate: the whole application runs on a laptop with no GCP project
and no SMS credits, and a mirror outage can never stop an alert going out.

---

## Quick start

Requires Python 3.12+ and Node 20+.

```bash
# 1. Configuration
cp .env.example .env      # then edit; see Configuration below

# 2. Backend
python -m venv .venv
.venv/Scripts/activate            # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo --admin-password 'pick-something-strong'

# 3. Frontend
cd frontend
npm install
npm run build
cd ..

# 4. Run
python manage.py runserver
```

Open <http://localhost:8000> and sign in as `safetyofficer`.

### Front-end hot reload

For UI work, run Vite alongside Django. The dev server proxies `/api` to
port 8000, so sessions and CSRF keep working with no CORS setup:

```bash
python manage.py runserver     # terminal 1
cd frontend && npm run dev     # terminal 2 -> http://localhost:5173
```

### Background workers

Reminders and escalation need the dispatcher running:

```bash
python manage.py run_dispatcher --loop --interval 20
python manage.py poll_usgs --loop --interval 60    # optional: live USGS feed
```

---

## Running with Docker

```bash
cp .env.example .env
docker compose up --build
```

Brings up Postgres, the web container (API + built SPA) on
<http://localhost:8000>, and the dispatcher worker. Migrations run on start,
and the Safety Officer account is created when `DJANGO_SUPERUSER_PASSWORD` is
set in `.env`.

Add the USGS poller when you want live ingestion:

```bash
docker compose --profile poller up
```

One image, three roles, selected by the entrypoint argument:

| Command | Role |
|---|---|
| `web` | gunicorn serving the API and the SPA (default) |
| `worker` | notification dispatcher + reminder/escalation sweeper |
| `poller` | USGS feed ingestion loop |
| `migrate` | apply migrations and exit |
| anything else | passed to `manage.py`, e.g. `docker compose run --rm web shell` |

---

## Configuration

Everything is environment-driven; `.env.example` documents every variable.
The ones that matter most:

| Variable | Why it matters |
|---|---|
| `DJANGO_SECRET_KEY` | Generate a fresh one per environment. |
| `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` | Required once you are behind a real hostname. |
| `PUBLIC_BASE_URL` | Builds each employee's status link. **Must be reachable from a phone on mobile data** — `localhost` will not work in a real drill. |
| `SMS_PROVIDER` | `m360` or `textbee` — which gateway actually sends. |
| `M360_ENABLED` / `TEXTBEE_ENABLED` | `true` sends real SMS and spends real money. Only the one named by `SMS_PROVIDER` is actually used to send. |
| `AUTO_TRIGGER_ENABLED` | `true` lets a USGS event broadcast with no human in the loop. Keep it `false` until the geofences are tuned. |
| `*_WEBHOOK_TOKEN`, `PUBSUB_PUSH_TOKEN` | Shared secrets for the machine-to-machine endpoints. **Unset means that endpoint is open** — the app logs a warning, but set them before deploying. |

### About the M360 endpoint

`M360_BROADCAST_URL` and `M360_BALANCE_URL` are configurable because M360 has
revised its API across versions. If your account documents different paths or
field names, change them in `.env` rather than in code, and check the request
shape in [`dissemination/gateways.py`](dissemination/gateways.py) against your
account's documentation before the first live drill.

### About the TextBee endpoint

TextBee turns a paired Android phone into the gateway, so `TEXTBEE_DEVICE_ID`
(shown in the TextBee dashboard) has to be set before `TEXTBEE_ENABLED=true`
will actually send anything — with it blank, sending simulates instead of
failing. TextBee's own webhook configuration has **no way to set a custom
header**, so `TEXTBEE_WEBHOOK_TOKEN`, if you set one, has to travel in the
callback URL instead:

```
https://your-host/api/webhooks/textbee/?token=<TEXTBEE_WEBHOOK_TOKEN>
```

Paste that whole URL into TextBee's webhook field and no header is ever
needed. Leaving `TEXTBEE_WEBHOOK_TOKEN` blank accepts the callback with no
credential at all — the app logs a warning when it does, and this is only
appropriate while you are still wiring the integration up.

---

## Walking through a drill

This follows the operational procedure in the study.

1. **Configure.** Add geofences under *Geofences* — coordinates, risk radius,
   magnitude threshold, evacuation instruction. Add personnel under
   *Directory*, each with a mobile number, a location and an escalation tier.
   Make at least one person an *Emergency Management Team* member, or
   escalation has nobody to notify.
2. **Verify system health.** *System health* checks the database, the SMS
   gateway balance, SMTP, Pub/Sub, Firestore and the notification queue.
3. **Create the drill.** *Drills → New drill*: pick the target geofences, the
   response window, the reminder cadence and the channels.
4. **Initiate.** *Initiate call tree alert* freezes the roster and queues the
   broadcast. Nothing is sent until the database transaction commits, so a
   failed activation cannot leak a message.
5. **Monitor.** The drill page refreshes every 5 seconds: response rate, safe
   and help counts, per-person latency, who is still outstanding.
6. **Intervene.** *Re-broadcast & escalate* reminds non-responders, or — past
   the deadline — flags them and notifies the EMT. You can also set a status
   by hand for someone who reported over the radio.
7. **Close and export.** *Complete drill* marks anyone still outstanding
   non-compliant. *Export CSV* produces the per-person compliance report.

To rehearse the automatic path without waiting for an earthquake, use
*Seismic feed → Simulate a USGS alert*. It injects a synthetic event, shows
the Trigger Filter verdict per location, and optionally activates the drill.

---

## API reference

All endpoints are under `/api/`. The dashboard uses same-origin session
authentication, so there is no token to manage.

### Session

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/auth/session/` | Current user; also seeds the CSRF cookie |
| `POST` | `/api/auth/login/` | Sign in (staff accounts only) |
| `POST` | `/api/auth/logout/` | Sign out |

### Resources

`/api/locations/`, `/api/employees/` — full CRUD.
`/api/events/`, `/api/inbound/`, `/api/audit/` — read-only.

### Drills

| Method | Path | Purpose |
|---|---|---|
| `GET` `POST` | `/api/drills/` | List / create |
| `POST` | `/api/drills/{id}/activate/` | Initiate the call tree |
| `POST` | `/api/drills/{id}/sweep/` | Remind non-responders, escalate |
| `POST` | `/api/drills/{id}/complete/` | Close the drill |
| `POST` | `/api/drills/{id}/cancel/` | Cancel it |
| `GET` | `/api/drills/{id}/monitor/` | Live statistics + roster |
| `GET` | `/api/drills/{id}/notifications/` | Delivery log |
| `GET` | `/api/drills/{id}/report.csv/` | Compliance export |

### Operations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/overview/` | Dashboard summary |
| `GET` | `/api/health/` | System health check |
| `GET` | `/api/healthz/` | Liveness probe (unauthenticated) |
| `POST` | `/api/events/simulate/` | Inject a simulated USGS alert |
| `POST` | `/api/participants/{id}/override/` | Set a status by hand |

### Public and machine-to-machine

| Method | Path | Auth |
|---|---|---|
| `GET` `POST` | `/api/status/{token}/` | The token in the URL |
| `POST` | `/api/webhooks/usgs/` | `USGS_WEBHOOK_TOKEN` |
| `POST` | `/api/webhooks/m360/` | `M360_WEBHOOK_TOKEN` |
| `POST` | `/api/webhooks/textbee/` | `TEXTBEE_WEBHOOK_TOKEN` |
| `POST` | `/api/webhooks/pubsub/` | `PUBSUB_PUSH_TOKEN` |

Webhook tokens travel as `?token=…` or an `X-Webhook-Token` header — except
TextBee's, which only ever arrives as `?token=…`, since TextBee's own
webhook configuration cannot send a custom header.

```bash
# Ingest a simulated USGS event
curl -X POST "http://localhost:8000/api/webhooks/usgs/?token=$USGS_WEBHOOK_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"type":"Feature","id":"test-001",
       "properties":{"mag":6.4,"place":"Luzon","time":1758290000000},
       "geometry":{"type":"Point","coordinates":[121.05,14.60,30.0]}}'

# Simulate an employee replying SAFE by SMS (M360)
curl -X POST "http://localhost:8000/api/webhooks/m360/?token=$M360_WEBHOOK_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"639171000100","message":"SAFE"}'

# Simulate an employee replying SAFE by SMS (TextBee) - no header, token in the URL
curl -X POST "http://localhost:8000/api/webhooks/textbee/?token=$TEXTBEE_WEBHOOK_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"sender":"639171000100","message":"SAFE"}'
```

---

## Testing

```bash
pytest                      # the whole suite
pytest tests/test_engine_services.py -v
pytest -k trigger_filter
```

245 tests, concentrated on the service layer — the Trigger Filter, call tree
orchestration, dispatch, the two-way reply loop and USGS parsing — plus
integration coverage of the API and webhooks.

| File | Covers |
|---|---|
| `tests/test_core_services.py` | Phone normalisation, message templating, audit |
| `tests/test_proximity.py` | Haversine distance and the Trigger Filter |
| `tests/test_engine_services.py` | Roster, activation, responses, escalation, statistics |
| `tests/test_dissemination_services.py` | M360 & TextBee clients, provider selection, email, dispatch, idempotency, health |
| `tests/test_accountability_services.py` | Inbound SMS matching, status links |
| `tests/test_ingestion_usgs.py` | GeoJSON, ENS email parsing, de-duplication |
| `tests/test_api.py` | REST endpoints, auth, webhooks, CSV export |

Every test runs with the gateways simulated, so the suite never touches the
network or spends SMS credits.

Some behaviours worth knowing are pinned by tests rather than by convention:

- HELP outranks SAFE. A later "SAFE" can never overwrite an earlier "HELP",
  so a duplicate reply cannot quietly cancel a rescue.
- Delivery is idempotent. Pub/Sub is at-least-once; a redelivered message must
  not send a second SMS.
- Dispatch waits for commit. A rolled-back activation sends nothing.
- GeoJSON is `[longitude, latitude]`. Swapping them would misplace every
  epicentre, so it is asserted explicitly.

---

## Project layout

```
calltree/          Django project: settings, URLs, SPA shell view
core/              Models, Firestore mirror, shared services, admin
ingestion/         Module 1 - USGS feed, webhook and ENS email parsing
engine/            Module 2 - proximity, Trigger Filter, call tree orchestration
dissemination/     Module 3 - Pub/Sub, SMS (M360 or TextBee), SMTP
accountability/    Module 4 - inbound replies, status links
api/               DRF serializers, viewsets, webhooks
frontend/          Vue 3 SPA (Vite + Tailwind), builds to frontend/dist
tests/             pytest suite
```

The service layer holds the logic. Views stay thin, so the same call tree
behaviour runs identically from the dashboard, a webhook or a management
command.

---

## Deploying to Cloud Run

[`infra/`](infra/) automates all of this: Cloud Run (web service + two
scheduled jobs for the dispatcher and the USGS poller), Cloud SQL, Firestore,
Pub/Sub, Secret Manager and Artifact Registry, all described declaratively in
[`infra/resources.json`](infra/resources.json).

```bash
./infra/provision.sh                       # base infra - once
./infra/deploy.sh                          # build, push, deploy - every release
./infra/pause.sh                           # between sessions - stop Cloud SQL
./infra/resume.sh                          # ... and bring it back
./infra/destroy.sh                         # tear it all down when done
```

Every script accepts `--dry-run` to preview its `gcloud`/`docker` commands
without running them. See [`infra/README.md`](infra/README.md) for
prerequisites, the required IAM roles, and why the dispatcher/poller are
Cloud Run *Jobs* on a Cloud Scheduler cron rather than long-running services
(`entrypoint.sh`'s `worker`/`poller` roles have no HTTP server to bind
`$PORT` to, which a Cloud Run *service* requires).

To do it by hand instead: attach Cloud SQL and set `DATABASE_URL`; set
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` and `PUBLIC_BASE_URL`
to the service's URL once it is known; create the Pub/Sub topic and a
**push** subscription pointing at
`https://<service-url>/api/webhooks/pubsub/?token=<PUBSUB_PUSH_TOKEN>`; run
`migrate` as its own one-off job rather than on the web service's own
startup once there is more than one instance, so they cannot race it; and
grant the runtime service account `roles/cloudsql.client`,
`roles/datastore.user`, `roles/pubsub.publisher` and
`roles/secretmanager.secretAccessor`.

---

## Operational notes

- **Test the reply path before you rely on it.** Inbound SMS matching depends
  on the M360 callback reaching `/api/webhooks/m360/` and on the mobile
  numbers in the directory being correct. Send one real message end to end
  before the first live drill.
- **`PUBLIC_BASE_URL` has to be publicly reachable.** Otherwise the status
  link in every alert is dead, and SMS reply becomes the only way to respond.
- **Keep an EMT member in every drill.** Escalation notifies people whose role
  is *Emergency Management Team* or *Safety Officer*; with none in scope the
  escalation step logs a warning and does nothing.
- **`AUTO_TRIGGER_ENABLED=true` removes the human in the loop.** Tune your
  geofences against the simulator first.
- **SQLite is for development only.** The thread-pool dispatcher writes
  concurrently; use Postgres anywhere real.

---

## Further reading

- [`CHANGELOG.md`](CHANGELOG.md) — what changed, when and why.
- `.env.example` — every configuration variable, annotated.
- [`infra/README.md`](infra/README.md) — the GCP provisioning/deploy/teardown toolkit.
