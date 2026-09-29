#!/usr/bin/env bash
# Reverse ./infra/pause.sh: restarts Cloud SQL and waits for it to come
# back RUNNABLE, then unpauses the two Cloud Scheduler jobs.
#
# Usage:
#   ./infra/resume.sh              # restart Cloud SQL, resume both schedules
#   ./infra/resume.sh --dry-run    # print every gcloud command, run nothing

set -euo pipefail

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

# shellcheck source=./lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

require_cmd gcloud jq
gcp_authenticate

DB_INSTANCE="$(res '.database.instance_id')"
DISPATCHER_SCHEDULE="$(res '.cloud_run.jobs.dispatcher.scheduler_job_name')"
POLLER_SCHEDULE="$(res '.cloud_run.jobs.poller.scheduler_job_name')"

log_step "Resuming project $PROJECT_ID ($REGION)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY RUN - no gcloud command below will actually execute."

# --------------------------------------------------------------------------
# Cloud SQL - restarted first and waited on, so the schedules below don't
# immediately fire into a database that is still coming back up.
# --------------------------------------------------------------------------
log_step "Cloud SQL"
sql_instance_exists "$DB_INSTANCE" || die "Instance $DB_INSTANCE does not exist. Run ./infra/provision.sh first."

state="$(query gcloud sql instances describe "$DB_INSTANCE" --project="$PROJECT_ID" --format='value(state)' --quiet)"
if [[ "$state" == "RUNNABLE" ]]; then
    log_ok "Instance $DB_INSTANCE is already running"
else
    run gcloud sql instances patch "$DB_INSTANCE" \
        --activation-policy=ALWAYS \
        --project="$PROJECT_ID" --quiet
    log_ok "Restart requested for $DB_INSTANCE"

    if [[ "$DRY_RUN" != "true" ]]; then
        log "Waiting for $DB_INSTANCE to come back RUNNABLE (up to 5 min)..."
        for _ in $(seq 1 30); do
            state="$(query gcloud sql instances describe "$DB_INSTANCE" --project="$PROJECT_ID" --format='value(state)' --quiet)"
            [[ "$state" == "RUNNABLE" ]] && break
            sleep 10
        done
        if [[ "$state" == "RUNNABLE" ]]; then
            log_ok "$DB_INSTANCE is RUNNABLE"
        else
            log_warn "$DB_INSTANCE is still '$state' after 5 min - check the console before relying on it."
        fi
    fi
fi

# --------------------------------------------------------------------------
# Cloud Scheduler
# --------------------------------------------------------------------------
log_step "Cloud Scheduler"
for name in "$DISPATCHER_SCHEDULE" "$POLLER_SCHEDULE"; do
    if ! scheduler_job_exists "$name"; then
        log_warn "$name does not exist - skipping"
        continue
    fi
    job_state="$(query gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" --format='value(state)' --quiet)"
    if [[ "$job_state" == "ENABLED" ]]; then
        log_ok "$name is already active"
    else
        run gcloud scheduler jobs resume "$name" \
            --location="$REGION" --project="$PROJECT_ID" --quiet
        log_ok "Resumed $name"
    fi
done

log_step "Done"
log "Cloud SQL is running and both schedules are active again."
