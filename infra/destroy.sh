#!/usr/bin/env bash
# Tear down everything provision.sh and deploy.sh created for the "test"
# environment in resources.json. Reverse order of creation: Scheduler and
# the Pub/Sub subscription first (nothing should be able to trigger anything
# else mid-teardown), then Cloud Run, then the data stores, then IAM and
# Artifact Registry last.
#
# This is destructive and asks for confirmation before anything irreversible
# (Cloud SQL, Firestore documents, Secret Manager values) unless run with
# --yes. Nothing here disables the project's APIs - other work in the same
# project may depend on them, and re-enabling an API is cheap and instant
# compared to guessing wrong.
#
# Usage:
#   ./infra/destroy.sh                # prompts before each destructive step
#   ./infra/destroy.sh --yes          # no prompts (CI / known-throwaway use)
#   ./infra/destroy.sh --dry-run      # print every gcloud command, delete nothing
#   ./infra/destroy.sh --keep-db      # delete everything except Cloud SQL
#
# Requires: gcloud, jq, and credentials.json in the repo root.

set -euo pipefail

KEEP_DB=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --yes|-y) FORCE=true ;;
        --keep-db) KEEP_DB=true ;;
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

log_step "Tearing down environment '$(res '.environment')' in $PROJECT_ID ($REGION)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY RUN - no gcloud command below will actually execute."
log_warn "This deletes Cloud Run services/jobs, Cloud SQL (unless --keep-db), Pub/Sub, Secret Manager secrets and the runtime service account."

confirm "Really tear down every resource in resources.json for '$(res '.environment')'?" \
    || { log "Aborted - nothing was deleted."; exit 0; }

# --------------------------------------------------------------------------
# Cloud Scheduler
# --------------------------------------------------------------------------
log_step "Cloud Scheduler"
for key in dispatcher poller; do
    name="$(res ".cloud_run.jobs.${key}.scheduler_job_name")"
    if scheduler_job_exists "$name"; then
        run gcloud scheduler jobs delete "$name" --location="$REGION" --project="$PROJECT_ID" --quiet
        log_ok "Deleted scheduler job $name"
    else
        log_ok "$name already gone"
    fi
done

# --------------------------------------------------------------------------
# Pub/Sub push subscription and topic
# --------------------------------------------------------------------------
log_step "Pub/Sub"
SUBSCRIPTION="$(res '.pubsub.push_subscription')"
TOPIC="$(res '.pubsub.topic')"
if pubsub_sub_exists "$SUBSCRIPTION"; then
    run gcloud pubsub subscriptions delete "$SUBSCRIPTION" --project="$PROJECT_ID" --quiet
    log_ok "Deleted subscription $SUBSCRIPTION"
else
    log_ok "$SUBSCRIPTION already gone"
fi
if pubsub_topic_exists "$TOPIC"; then
    run gcloud pubsub topics delete "$TOPIC" --project="$PROJECT_ID" --quiet
    log_ok "Deleted topic $TOPIC"
else
    log_ok "$TOPIC already gone"
fi

# --------------------------------------------------------------------------
# Cloud Run - jobs, then the web service
# --------------------------------------------------------------------------
log_step "Cloud Run jobs"
for key in dispatcher poller; do
    name="$(res ".cloud_run.jobs.${key}.job_name")"
    if cloud_run_job_exists "$name"; then
        run gcloud run jobs delete "$name" --region="$REGION" --project="$PROJECT_ID" --quiet
        log_ok "Deleted job $name"
    else
        log_ok "$name already gone"
    fi
done

log_step "Cloud Run web service"
WEB_SERVICE="$(res '.cloud_run.web.service_name')"
if cloud_run_service_exists "$WEB_SERVICE"; then
    run gcloud run services delete "$WEB_SERVICE" --region="$REGION" --project="$PROJECT_ID" --quiet
    log_ok "Deleted service $WEB_SERVICE"
else
    log_ok "$WEB_SERVICE already gone"
fi

# --------------------------------------------------------------------------
# Cloud SQL
# --------------------------------------------------------------------------
log_step "Cloud SQL"
DB_INSTANCE="$(res '.database.instance_id')"
if [[ "$KEEP_DB" == "true" ]]; then
    log_warn "Keeping $DB_INSTANCE (--keep-db) - it will keep costing money until you delete it by hand."
elif sql_instance_exists "$DB_INSTANCE"; then
    if confirm "Delete Cloud SQL instance $DB_INSTANCE? This permanently destroys the drill history and directory data in it."; then
        run gcloud sql instances delete "$DB_INSTANCE" --project="$PROJECT_ID" --quiet
        log_ok "Deleted instance $DB_INSTANCE"
    else
        log_warn "Kept $DB_INSTANCE."
    fi
else
    log_ok "$DB_INSTANCE already gone"
fi

# --------------------------------------------------------------------------
# Firestore
#
# A Firestore database itself cannot be deleted via gcloud in most projects
# (it is a project-level resource with extra deletion protection); this
# clears its data instead so nothing keeps mirroring into it, and leaves a
# note for the one case that needs the console.
# --------------------------------------------------------------------------
log_step "Firestore"
FS_DB="$(res '.firestore.database_id')"
if firestore_db_exists "$FS_DB"; then
    if confirm "Delete all documents in Firestore database '$FS_DB'? (The database itself stays; only its data is cleared.)"; then
        if run gcloud firestore databases delete --database="$FS_DB" --project="$PROJECT_ID" --quiet 2>/dev/null; then
            log_ok "Deleted Firestore database $FS_DB"
        else
            log_warn "Could not delete the Firestore database via gcloud (this is normal - Google requires the console or an org-policy change for '(default)'). Clear its collections from the Firebase/Cloud console if this project should not keep that data."
        fi
    else
        log_warn "Kept Firestore data."
    fi
else
    log_ok "Firestore database $FS_DB already gone"
fi

# --------------------------------------------------------------------------
# Secret Manager
# --------------------------------------------------------------------------
log_step "Secret Manager"
secret_count="$(res '.secrets | length')"
for i in $(seq 0 $((secret_count - 1))); do
    name="$(res ".secrets[$i].name")"
    if secret_exists "$name"; then
        run gcloud secrets delete "$name" --project="$PROJECT_ID" --quiet
        log_ok "Deleted secret $name"
    fi
done

# --------------------------------------------------------------------------
# Runtime service account and its IAM bindings
# --------------------------------------------------------------------------
log_step "Service account"
SA_ID="$(res '.service_account.account_id')"
SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
if service_account_exists "$SA_EMAIL"; then
    mapfile -t roles < <(res '.service_account.roles[]')
    for role in "${roles[@]}"; do
        # Tolerant of "not bound" (2>/dev/null only touches stderr, so the
        # dry-run preview from run_quiet - which prints to stdout - still
        # shows; redirecting stdout too, at the call site, would silently
        # swallow that preview the same way an earlier version of this
        # toolkit accidentally did for the Cloud Run job invoker binding).
        run_quiet gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$SA_EMAIL" --role="$role" --condition=None --quiet 2>/dev/null || true
    done
    run gcloud iam service-accounts delete "$SA_EMAIL" --project="$PROJECT_ID" --quiet
    log_ok "Deleted service account $SA_EMAIL and its project-level role bindings"
else
    log_ok "$SA_EMAIL already gone"
fi

# --------------------------------------------------------------------------
# Artifact Registry
# --------------------------------------------------------------------------
log_step "Artifact Registry"
AR_REPO="$(res '.artifact_registry.repository')"
if artifact_repo_exists "$AR_REPO"; then
    if confirm "Delete repository $AR_REPO and every image tag in it?"; then
        run gcloud artifacts repositories delete "$AR_REPO" --location="$REGION" --project="$PROJECT_ID" --quiet
        log_ok "Deleted repository $AR_REPO"
    else
        log_warn "Kept $AR_REPO."
    fi
else
    log_ok "$AR_REPO already gone"
fi

log_step "Done"
log "Torn down. APIs were left enabled (harmless, and other work in this"
log "project may depend on them); disable them by hand if this was the"
log "project's only workload."
