# Infrastructure toolkit

Provisions and deploys the "test" environment described in
[`resources.json`](resources.json) to Google Cloud Platform: Cloud Run (a web
service plus two scheduled jobs), Cloud SQL for Postgres, Firestore,
Pub/Sub, Secret Manager, and the Artifact Registry image that holds it all.

```
provision.sh   base infra: APIs, Artifact Registry, Cloud SQL, Firestore,
               the Pub/Sub topic, the runtime service account, Secret Manager
deploy.sh      build the image, push it, deploy the service + jobs, wire up
               the Pub/Sub push subscription and Cloud Scheduler
destroy.sh     tear all of it back down, in reverse order
resources.json single source of truth for names/sizing/schedules - every
               script reads this rather than hardcoding anything
lib.sh         shared helpers (sourced, not run directly)
```

Run them in that order: `provision.sh` once, `deploy.sh` for every release,
`destroy.sh` when the test environment is no longer needed. All three are
idempotent - re-running any of them is safe and just reconciles the current
state with `resources.json`.

## Prerequisites

- [`gcloud`](https://cloud.google.com/sdk/docs/install) (the Google Cloud
  SDK's `bin/` directory has both `gcloud.cmd` and a POSIX `gcloud` shell
  script, so this works from Git Bash on Windows once that directory is on
  `PATH`)
- [`jq`](https://jqlang.org/)
- `docker` (only for `deploy.sh`, unless you pass `--skip-build`)
- `credentials.json` - a GCP service account key with enough IAM roles to
  create everything above (Editor is the simple option; the least-privilege
  set is Cloud Run Admin, Cloud SQL Admin, Firestore/Datastore Owner,
  Pub/Sub Admin, Artifact Registry Admin, Secret Manager Admin, Service
  Account Admin, Cloud Scheduler Admin, Service Usage Admin, and Project IAM
  Admin for the role bindings). Place it in the repo root. It is gitignored
  (`*credentials*.json` in `.gitignore`) - never commit it.

## Configuration

Two layers, matching the split the rest of the repo already uses for `.env`:

- **`resources.json`** - topology and sizing (names, region, machine tiers,
  schedules). No secrets live here, ever - only which local env variable
  backs each Secret Manager entry.
- **`infra/test.env`** (gitignored; copy from
  [`test.env.example`](test.env.example)) - overrides the repo root's
  `.env` for values that should differ between local development and this
  cloud environment. The most important one: local dev runs with
  `DJANGO_DEBUG=true`, which must not ship to anything reachable from the
  internet, so `infra/test.env` sets it back to `false`. Anything **not**
  listed in `infra/test.env` is inherited from the root `.env` as-is -
  notably `M360_ENABLED` / `TEXTBEE_ENABLED`, since whether SMS actually
  sends is a decision for the environment as a whole, not something that
  should silently differ between local and cloud.

```bash
cp infra/test.env.example infra/test.env
# edit infra/test.env if you need more overrides than the DJANGO_DEBUG one
```

Every secret Cloud Run receives (`DJANGO_SECRET_KEY`, the database
password, webhook tokens, the TextBee/M360 API keys, ...) is read from
whichever of `infra/test.env` / the root `.env` resolves it, uploaded to
Secret Manager by `provision.sh`, and referenced by `deploy.sh` via
`--set-secrets` - the value is never a `gcloud` command-line argument or a
line in `resources.json`, so it never lands in shell history or in this
repo. The one exception, inherent to the `gcloud sql users create` /
`set-password` API and not something this toolkit can avoid, is the
database password: it is passed as a `--password` flag, so it is briefly
visible in a process listing while `provision.sh` runs. Acceptable for a
test-tier password; treat that command as sensitive if this ever holds
anything that matters.

## Usage

```bash
# One-time setup
cp infra/test.env.example infra/test.env    # then edit if needed
./infra/provision.sh                        # or --dry-run to preview first

# Every release
./infra/deploy.sh                           # or --skip-build to redeploy
                                             # the last pushed image

# When you are done with the test environment
./infra/destroy.sh                          # prompts before each destructive
                                             # step; --yes skips the prompts,
                                             # --keep-db spares Cloud SQL
```

Every script accepts `--dry-run`, which prints every `gcloud`/`docker`
command it would run without executing any of them - the fastest way to see
exactly what a script is about to do to your project before it does it.

### Why `deploy.sh` runs in two passes

The app needs its own URL to build `PUBLIC_BASE_URL` (the link in every SMS
and email) and to hand Pub/Sub a push endpoint - but that URL does not exist
before the first deploy. `deploy.sh` handles this itself: it deploys with
`.run.app` wildcard values that work for a brand-new service (Django's
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` both accept a leading-dot/`*`
wildcard), reads back the real URL Cloud Run assigned, and patches
`PUBLIC_BASE_URL` (and the concrete CSRF origin) in a second, fast
metadata-only update. Nothing needs to be run twice by hand for this.

### Why the dispatcher and poller are Cloud Run Jobs, not services

`entrypoint.sh`'s `worker`/`poller` roles loop forever with no HTTP server,
which Cloud Run **services** cannot run (they must bind `$PORT` and answer
requests). Cloud Run **Jobs** run to completion instead, which is what
`manage.py run_dispatcher` / `poll_usgs` already do without their `--loop`
flag - one pass, then exit. Cloud Scheduler invokes each job's execution on
a cron schedule (`resources.json`'s `cloud_run.jobs.*.schedule`), authenticated
via the same runtime service account holding `roles/run.invoker` on that job.

Migrations run the same way: a one-off execution of the dispatcher job with
its args overridden to `migrate` (`gcloud run jobs execute ... --args=migrate`),
rather than on the web service's own startup - with more than one web
instance, several booting at once must never race a `migrate --noinput`.

## Adding a resource

Add it to `resources.json`, then add the matching idempotent block to
`provision.sh` (or `deploy.sh`, if it depends on the app's URL) following
the existing pattern: a `*_exists()` check in `lib.sh`, then create-or-update
through `run` (or `run_quiet` for a call whose successful output is noise).
Add the matching delete to `destroy.sh`, in reverse dependency order.
