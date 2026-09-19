# Working in this repository

Guidance for AI coding agents (Claude Code and friends) and for anyone new to
the project. [`README.md`](README.md) explains what the system does; this file
explains how to change it.

---

## The one standing rule: log every change

**Before you finish a task that touched a tracked file, add an entry to
[`CHANGELOG.md`](CHANGELOG.md) under `## [Unreleased]`.**

This is not optional bookkeeping. This project is the implementation of a
research study, and the change log is the record of how the prototype
evolved — it is read by people who were not in the session that produced the
change.

How to write the entry:

- Put it under the right category: **Added**, **Changed**, **Fixed**,
  **Removed**, **Deprecated**, **Security**.
- Say what changed *and why*. `Fixed a bug in activate_drill` is useless in
  six months; the entry in 1.0.0 that names the stale-instance cause is not.
- Name the affected module, so a reader can jump to it.
- One entry per meaningful change. Do not batch unrelated work into one line,
  and do not log a change per file when one change touched five files.
- Skip it only for changes with no effect on behaviour or interface:
  formatting, comment typos, a test rename. When unsure, log it.

On release, rename `[Unreleased]` to the new version with the date and open a
fresh `[Unreleased]`.

---

## Orientation

Four modules, matching the system architecture. The README has the diagram;
the short version:

| Path | Role |
|---|---|
| `core/` | Models, Firestore mirror, shared services, Django admin |
| `ingestion/` | Module 1 — USGS feed, webhook and email parsing |
| `engine/` | Module 2 — proximity, Trigger Filter, call tree orchestration |
| `dissemination/` | Module 3 — Pub/Sub, M360 SMS, SMTP |
| `accountability/` | Module 4 — inbound replies, status links |
| `api/` | DRF serializers, viewsets, webhooks |
| `frontend/` | Vue 3 SPA, builds to `frontend/dist` |
| `tests/` | pytest suite |

---

## Conventions

**Logic lives in `services.py`, not in views.** Views validate, call a
service, and serialize the result. The same call tree behaviour has to run
identically from the dashboard, a webhook and a management command, so
anything a view does exclusively is in the wrong place.

**Configuration comes from the environment.** No hardcoded URLs, keys,
thresholds or magic numbers. Add the variable to `calltree/settings.py` with
a sensible default, and document it in `.env.example` in the same change.

**External gateways must be disableable.** Every integration checks an
`*_ENABLED` flag and simulates when off. This is what lets the suite run
without network access and lets the app run without a GCP project. A new
integration follows the same pattern.

**Failures degrade, they do not raise.** A gateway returns a `DeliveryResult`;
the Firestore mirror swallows and logs. One unreachable person must never
abort a broadcast, and a mirror outage must never stop an alert.

**Nothing disappears silently.** An unreachable employee gets a `SKIPPED`
notification row with a reason. An unmatched inbound SMS is stored for
reconciliation. Operator actions go to `AuditLog`. If you add a path where
something can be dropped, leave a record of the drop.

---

## Behaviours that are load-bearing

These are pinned by tests. If a change makes one of these tests fail, the
change is probably wrong — check before you edit the test.

- **HELP outranks SAFE.** A later "SAFE" never overwrites an earlier "HELP".
  A duplicate reply must not quietly cancel a rescue.
- **Delivery is idempotent.** Pub/Sub is at-least-once. `deliver_notification`
  short-circuits on an already-sent row.
- **Dispatch waits for commit.** Notifications are handed off in
  `transaction.on_commit`, so a rolled-back activation sends nothing.
- **GeoJSON is `[longitude, latitude]`.** Swapping them misplaces every
  epicentre and silently breaks the Trigger Filter.
- **Both Trigger Filter conditions must hold.** Magnitude at or above the
  threshold *and* distance within the radius. Per-location values win over
  the global defaults.
- **A poison webhook payload is acked, not retried.** Returning non-2xx to
  Pub/Sub makes it redeliver forever.

---

## Testing

```bash
pytest                                   # the whole suite
pytest tests/test_engine_services.py -v
pytest -k trigger_filter
```

New behaviour needs a test in the matching `tests/test_*_services.py`, and a
bug fix needs the test that would have caught it.

Notes on the fixtures:

- `tests/conftest.py` disables every gateway for the whole suite. Do not
  re-enable one globally; override it in the test that needs it, with
  `responses` for the HTTP shape.
- Anything that triggers a broadcast needs
  `django_capture_on_commit_callbacks(execute=True)`, because dispatch is
  deferred to commit.
- Dispatch is serialised to one thread in tests: the SQLite test database
  will not take concurrent writers. Production uses Postgres.

---

## Common tasks

**Change the data model** — edit `core/models.py`, then:
```bash
python manage.py makemigrations core && python manage.py migrate
```
Commit the migration alongside the model change.

**Add an API endpoint** — service function in the owning module, serializer
in `api/serializers.py`, view in `api/views.py`, route in `api/urls.py`, test
in `tests/test_api.py`.

**Work on the UI** — `npm run dev` in `frontend/` proxies `/api` to Django on
port 8000. Build with `npm run build` before testing the Django-served path;
`frontend/dist` is gitignored and rebuilt by the Docker image.

**Add a configuration variable** — `calltree/settings.py` *and*
`.env.example`, in the same change. An undocumented variable is a variable
the next person will not find.

---

## What to be careful with

- **`M360_ENABLED=true` spends real money** and sends to real phones. Leave it
  off unless a live test is the point of the task.
- **`AUTO_TRIGGER_ENABLED=true` removes the human in the loop.** A matching
  USGS event will broadcast on its own.
- **Never commit `.env`**, service-account JSON, or real credentials in a
  fixture. `.gitignore` and `.dockerignore` already exclude them; do not add
  an exception.
- **Do not weaken the webhook token checks.** They are the only thing standing
  between the open internet and the broadcast trigger.
