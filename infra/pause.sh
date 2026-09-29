#!/usr/bin/env bash
# Pause the "test" environment's actual 24/7 cost driver without tearing
# anything down: stops Cloud SQL and pauses the two Cloud Scheduler jobs
# that would otherwise keep firing into a stopped database every minute.
#
# The Cloud Run web service and Jobs are left alone on purpose - they
# already cost ~nothing at rest (min_instances=0, and a Job only bills for
# an execution that actually runs). Cloud SQL is different: it is a real
# machine that bills continuously while it exists, whether or not anything
# ever queries it, which is almost always the actual source of a "test"
# environment burning credits fast. Reverse with ./infra/resume.sh.
#
# While paused, the web service stays reachable but any request that
# touches the database (which is nearly all of them, including login) will
# fail until you resume - this pauses spend, it does not gracefully take
# the site offline.
#
# Usage:
#   ./infra/pause.sh              # stop Cloud SQL, pause both schedules
#   ./infra/pause.sh --dry-run    # print every gcloud command, run nothing

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

log_step "Pausing project $PROJECT_ID ($REGION)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY RUN - no gcloud command below will actually execute."

# --------------------------------------------------------------------------
# Cloud Scheduler - paused first, so the dispatcher/poller stop trying to
# reach a database that is about to disappear out from under them.
# --------------------------------------------------------------------------
log_step "Cloud Scheduler"
for name in "$DISPATCHER_SCHEDULE" "$POLLER_SCHEDULE"; do
    if ! scheduler_job_exists "$name"; then
        log_warn "$name does not exist - skipping"
        continue
    fi
    state="$(query gcloud scheduler jobs describe "$name" --location="$REGION" --project="$PROJECT_ID" --format='value(state)' --quiet)"
    if [[ "$state" == "PAUSED" ]]; then
        log_ok "$name is already paused"
    else
        run gcloud scheduler jobs pause "$name" \
            --location="$REGION" --project="$PROJECT_ID" --quiet
        log_ok "Paused $name"
    fi
done

# --------------------------------------------------------------------------
# Cloud SQL - the actual cost driver: it bills continuously while running,
# unlike Cloud Run, which scales to zero on its own.
# --------------------------------------------------------------------------
log_step "Cloud SQL"
if ! sql_instance_exists "$DB_INSTANCE"; then
    log_warn "Instance $DB_INSTANCE does not exist - skipping"
else
    state="$(query gcloud sql instances describe "$DB_INSTANCE" --project="$PROJECT_ID" --format='value(state)' --quiet)"
    if [[ "$state" == "RUNNABLE" ]]; then
        run gcloud sql instances patch "$DB_INSTANCE" \
            --activation-policy=NEVER \
            --project="$PROJECT_ID" --quiet
        log_ok "Stopped Cloud SQL instance $DB_INSTANCE"
    else
        log_ok "Instance $DB_INSTANCE is already '$state' - nothing to do"
    fi
fi

log_step "Done"
log "Cloud SQL is stopped and both schedules are paused."
log "The web service is still reachable, but anything that touches the"
log "database will error until you run ./infra/resume.sh."
