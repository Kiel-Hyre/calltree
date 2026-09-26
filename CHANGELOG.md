# Changelog

Every change to this project is recorded here. Newest first.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entry categories: **Added**, **Changed**, **Fixed**, **Removed**,
**Deprecated**, **Security**.

---

## [Unreleased]

### Added

- **GCP infrastructure toolkit** (`infra/`): `provision.sh`, `deploy.sh` and
  `destroy.sh`, all idempotent and driven by a single declarative manifest,
  `infra/resources.json`, so naming/sizing/schedules live in one place
  rather than being hardcoded across three scripts.
  - `provision.sh` creates the base infra: required APIs, an Artifact
    Registry repo, the Cloud Run runtime service account and its IAM roles,
    a minimal-tier Cloud SQL for Postgres instance, a Firestore database,
    the Pub/Sub topic, and Secret Manager entries sourced from `.env`
    (values are piped into `gcloud secrets create`, never passed as a
    command-line argument or written into `resources.json`).
  - `deploy.sh` builds and pushes the image, deploys the Cloud Run web
    service, deploys the dispatcher and USGS poller as Cloud Run **Jobs**
    (not services - `entrypoint.sh`'s `worker`/`poller` roles loop forever
    with no HTTP server, which a Cloud Run service cannot run), runs
    migrations as a one-off job execution rather than on the web service's
    own startup (so several instances booting at once cannot race
    `migrate`), then wires up the Pub/Sub push subscription and two Cloud
    Scheduler crons once the service's URL is known. Handles Cloud Run's
    self-reference problem (the app needs its own URL for
    `PUBLIC_BASE_URL` and the Pub/Sub push target, neither of which exist
    before the first deploy) with a two-pass deploy: `.run.app` wildcard
    values for the first pass, then a metadata-only patch once the real URL
    is known.
  - `destroy.sh` tears all of it down in reverse order, prompting before
    anything irreversible (Cloud SQL, Firestore data, Secret Manager
    values) unless run with `--yes`; `--keep-db` spares Cloud SQL
    specifically.
  - Every script accepts `--dry-run`, which prints every `gcloud`/`docker`
    command instead of running it. Verified by stubbing `gcloud`/`docker`
    and exercising both the "nothing exists yet" and "everything already
    exists" branches of all three scripts; caught and fixed a real bug this
    way (see Fixed) plus a missing confirmation line on the Firestore
    delete path.
  - Reads the repo root's `.env` directly, unmodified - this is a
    disposable test environment, so provision.sh/deploy.sh deliberately do
    not maintain a separate cloud-specific env file. Whatever `.env`
    currently has (`DJANGO_DEBUG` included) is what gets deployed; an
    earlier version of this toolkit added a separate `infra/test.env`
    override layer specifically to force `DJANGO_DEBUG=false` in the cloud,
    which turned out to be unwanted complexity for a test-only deployment
    and was removed before release.
  - `infra/README.md` documents prerequisites, the required IAM roles, and
    the reasoning above in full.

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

- `deploy.sh` computed `DATABASE_URL` (the Cloud SQL Unix-socket connection
  string) but never actually added it to the env vars sent to Cloud Run -
  it built the variable and then never used it. `calltree/settings.py`
  falls back to a local SQLite file at `data/calltree.sqlite3` whenever
  `DATABASE_URL` is unset, so the deployed web service was silently running
  against a fresh, empty SQLite database inside its own ephemeral
  container filesystem instead of the real Cloud SQL instance - migrations
  had been applied to Cloud SQL by the one-off job execution, but the web
  service itself never saw `DATABASE_URL` at all, so every one of its
  instances started from a blank slate: `OperationalError ... no such
  table: auth_user` on the very first login attempt. Added the missing
  `plain_env+="DATABASE_URL=${DATABASE_URL},"` line alongside the script's
  other self-computed values.

- `deploy.sh`'s `deploy_job()` passed `--command="/app/entrypoint.sh"` to
  `gcloud run jobs create/update`. On Git Bash for Windows, MSYS
  auto-converts any argument that looks like a Unix absolute path before
  handing it to a native `.exe` - and since `/app` isn't a real path on the
  host, it rewrote this one to something like `C:/Program Files/Git/app/
  entrypoint.sh` before `gcloud` ever saw it. Cloud Run then tried to exec
  a path that does not exist inside the container, and every job execution
  failed immediately with exit code 1 (confirmed straight from the job's
  audit log: `"command": ["C:/Program Files/Git/app/entrypoint.sh"]`, not
  the `/app/entrypoint.sh` the script sent). `/app/entrypoint.sh` is a
  path inside the container, never the host filesystem, so conversion is
  always wrong for this one argument.

  First fix attempt prefixed the call with `MSYS_NO_PATHCONV=1`, which
  turned out to be the wrong tool: that variable disables path conversion
  for the *entire* invocation, including a conversion `gcloud`'s own
  launcher script depends on internally (handing its bundled `python3` a
  real Windows path to `gcloud.py`) - which then broke instead, with
  `python3.exe: can't open file 'C:\c\Users\...\gcloud.py'`. Replaced with
  the actual standard workaround (the same one used for `docker run -v`
  on Git Bash): a doubled leading slash, `--command="//app/entrypoint.sh"`.
  MSYS treats a `//`-prefixed argument as an explicit escape and passes it
  through unconverted; on Linux/macOS, where bash never touches it either
  way, the doubled slash reaches `gcloud` as-is and the container's own
  exec later resolves it identically to a single leading slash.

- `infra/resources.json`'s two Cloud Run Jobs (`dispatcher`, `poller`) were
  sized at 256Mi memory with `cpu: "1"`. Cloud Run's gen2 execution
  environment requires at least 512Mi whenever CPU is always-allocated
  (unthrottled, the default for Jobs): `Total memory < 512 Mi is not
  supported with gen2 execution environment with cpu always allocated`.
  Found on a real `deploy.sh` run - the web service (already 512Mi) deployed
  fine; only the two jobs were under the floor. Bumped both to 512Mi.

- `deploy.sh`'s first-pass `gcloud run deploy` for the web service combined
  `--set-env-vars` (via `common_flags`, shared with the Cloud Run jobs) with
  its own extra `--update-env-vars="RUN_MIGRATIONS_ON_START=false"` on
  `web_flags`. `gcloud run deploy` only accepts one env-var mutation flag
  per invocation and rejected the pair outright: `At most one of
  --clear-env-vars | --env-vars-file | --set-env-vars | --remove-env-vars
  --update-env-vars can be specified.` Found on a real `deploy.sh` run,
  after `provision.sh` succeeded. Folded `RUN_MIGRATIONS_ON_START=false`
  into `plain_env` (which already feeds the shared `--set-env-vars`)
  instead of a second flag.

- `infra/resources.json`'s `firestore.type` was `"FIRESTORE_NATIVE"`, the
  enum name Firestore's own API uses internally. Current `gcloud` (`firestore
  databases create --type=...`) instead wants the CLI's own lowercase,
  hyphenated flag values and rejects the old form outright:
  `Invalid choice: 'FIRESTORE_NATIVE'. Did you mean 'firestore-native'?`.
  Found by running `provision.sh` for real against the live project - this
  is exactly the kind of drift the manifest can't catch on its own, since
  `--dry-run` never actually invokes `gcloud` to validate a flag value.
  Changed to `"firestore-native"`.

- Every `*_exists()` check in `infra/lib.sh` (and a couple of inline
  `query gcloud ...` calls in `provision.sh`) was missing `--quiet`.
  Against a project where the relevant API is not enabled yet, `gcloud
  describe` responds with an interactive "Would you like to enable and
  retry? (y/N)?" prompt - and since these checks redirect both stdout
  *and* stderr to `/dev/null`, that prompt was invisible while `gcloud`
  still blocked on stdin, waiting for a keystroke nothing would ever
  send: a silent, unexplained hang, reported as the script "just stuck"
  with no error and no visible prompt. Confirmed directly: the same
  `gcloud ... describe` command against a disabled API prints the
  `(y/N)?` prompt without `--quiet` and skips straight to a clean error
  with it. Added `--quiet` to every real `gcloud` invocation across all
  three scripts (existence checks and the create/update/delete calls
  alike), which is the flag's documented purpose - disable all
  interactive prompts, fail deterministically instead.

- `.env` had genuinely picked up CRLF line endings (confirmed: 134
  carriage returns, one per line) from an earlier session in which a
  Python one-off script rewrote it via `pathlib.Path.write_text()` -
  which translates every `\n` to the platform's line ending on Windows
  unless told not to, silently turning a whole file's LF into CRLF on
  a single write. `.env` is gitignored, so the `eol=lf` rule in
  `.gitattributes` (which protects every tracked file from exactly
  this) never applied to it. Renormalised to LF (verified byte-for-byte
  identical content otherwise: same line count, diff only on the
  stripped `\r`s). `env_get()` in `infra/lib.sh` now also strips `\r`
  defensively regardless, since a plain `grep` + string-slice does not,
  and every secret value this reads feeds `gcloud secrets create
  --data-file=-` - an unstripped trailing `\r` there would silently
  corrupt the secret. No secret had actually been pushed to Secret
  Manager yet when this was found (only `--dry-run` had run for real),
  so nothing needs to be rotated retroactively.

- `res()`/`res_json()` in `infra/lib.sh` (reading `resources.json`) now
  also strip `\r` defensively, for the same class of reason - even
  though `resources.json` on disk was confirmed clean (0 carriage
  returns) this time, so it was not itself the source of an earlier
  garbled/overlapping line of terminal output. The defence costs
  nothing to keep in front of it regardless of which tool touches the
  file next.

- `infra/lib.sh`'s `gcp_authenticate()` verified project access with
  `gcloud projects describe`, which itself requires the Cloud Resource
  Manager API - not guaranteed enabled on a project before
  `provision.sh` has had the chance to enable it (`gcloud services
  enable ...` is the very next step). Found by actually installing
  `gcloud` and running against a real project for the first time: the
  check failed with "Cloud Resource Manager API has not been used in
  this project," and this toolkit's own diagnostic message
  ("not enough IAM roles?") would have sent the wrong signal - the
  account had `roles/owner`. Removed the check entirely; `set -euo
  pipefail` already stops the script on any real gcloud failure, and
  that failure carries gcloud's own specific, actionable error, which
  is more reliable than a guess of ours. Added
  `cloudresourcemanager.googleapis.com` to `resources.json`'s `apis`
  list (first in the list) so `provision.sh` enables it early anyway,
  since other operations resolve project metadata through it too.

- `infra/deploy.sh` and `infra/provision.sh` each had one `run gcloud ...
  add-iam-policy-binding ... >/dev/null` call whose trailing redirect
  silenced `run()`'s own dry-run preview line along with the real command's
  output, since the redirect at the call site applies to the whole
  invocation, function included. Under `--dry-run` this made the Cloud Run
  job's invoker-role binding disappear from the printed plan with no
  indication it was ever going to run. Fixed by adding a `run_quiet()`
  helper to `lib.sh` that redirects only the *real* command's output,
  never the dry-run printf, and updating both call sites (plus the
  matching `remove-iam-policy-binding` call in `destroy.sh`, which had the
  same shape). Found by stubbing `gcloud` and diffing the dry-run output
  against the script's own logic, not by inspection.

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
