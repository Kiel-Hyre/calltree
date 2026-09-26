#!/usr/bin/env bash
# Provision the base GCP infrastructure for the "test" environment described
# in resources.json: APIs, Artifact Registry, the Cloud Run runtime service
# account, Cloud SQL, Firestore, the Pub/Sub topic, and Secret Manager.
#
# Idempotent - safe to re-run. Does NOT deploy the application; that is
# deploy.sh, which needs the resources this script creates to already exist
# (and needs this project's Cloud Run URL, which does not exist yet).
#
# Usage:
#   ./infra/provision.sh              # provision, prompting before nothing
#                                      # destructive (this script creates only)
#   ./infra/provision.sh --dry-run    # print every gcloud command, run nothing
#
# Requires: gcloud, jq, and credentials.json (a GCP service account key) in
# the repo root - see infra/README.md.

set -euo pipefail

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --yes|-y) FORCE=true ;;
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

log_step "Provisioning project $PROJECT_ID ($REGION), environment '$(res '.environment')'"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY RUN - no gcloud command below will actually execute."

# --------------------------------------------------------------------------
# APIs
# --------------------------------------------------------------------------
log_step "Enabling required APIs"
mapfile -t apis < <(res '.apis[]')
run gcloud services enable "${apis[@]}" --project="$PROJECT_ID"
log_ok "APIs enabled: ${apis[*]}"

# --------------------------------------------------------------------------
# Artifact Registry
# --------------------------------------------------------------------------
log_step "Artifact Registry"
AR_REPO="$(res '.artifact_registry.repository')"
AR_DESC="$(res '.artifact_registry.description')"
if artifact_repo_exists "$AR_REPO"; then
    log_ok "Repository $AR_REPO already exists"
else
    run gcloud artifacts repositories create "$AR_REPO" \
        --repository-format=docker \
        --location="$REGION" \
        --description="$AR_DESC" \
        --project="$PROJECT_ID"
    log_ok "Created repository $AR_REPO"
fi

# --------------------------------------------------------------------------
# Runtime service account
# --------------------------------------------------------------------------
log_step "Cloud Run runtime service account"
SA_ID="$(res '.service_account.account_id')"
SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
if service_account_exists "$SA_EMAIL"; then
    log_ok "Service account $SA_EMAIL already exists"
else
    run gcloud iam service-accounts create "$SA_ID" \
        --display-name="$(res '.service_account.display_name')" \
        --project="$PROJECT_ID"
    log_ok "Created service account $SA_EMAIL"
fi

mapfile -t roles < <(res '.service_account.roles[]')
for role in "${roles[@]}"; do
    run_quiet gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet
done
log_ok "Bound roles: ${roles[*]}"

# --------------------------------------------------------------------------
# Cloud SQL (Postgres)
# --------------------------------------------------------------------------
log_step "Cloud SQL"
DB_INSTANCE="$(res '.database.instance_id')"
DB_NAME="$(res '.database.database_name')"
DB_USER="$(res '.database.app_user')"
DB_PASSWORD="$(env_get POSTGRES_PASSWORD)"
[[ -n "$DB_PASSWORD" ]] || die "POSTGRES_PASSWORD is blank in .env - set a real password before provisioning the database."

if sql_instance_exists "$DB_INSTANCE"; then
    log_ok "Instance $DB_INSTANCE already exists (leaving its tier/storage as-is; edit it by hand if resources.json has changed)"
else
    run gcloud sql instances create "$DB_INSTANCE" \
        --database-version="$(res '.database.database_version')" \
        --edition="$(res '.database.edition')" \
        --cpu="$(res '.database.cpu')" \
        --memory="$(res '.database.memory')" \
        --storage-size="$(res '.database.storage_size_gb')" \
        --storage-type="$(res '.database.storage_type')" \
        --availability-type="$(res '.database.availability_type')" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --quiet
    log_ok "Created Cloud SQL instance $DB_INSTANCE"
fi

if query gcloud sql databases describe "$DB_NAME" --instance="$DB_INSTANCE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    log_ok "Database $DB_NAME already exists"
else
    run gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE" --project="$PROJECT_ID"
    log_ok "Created database $DB_NAME"
fi

# Note: --password on this command line means the password is visible in
# shell history and in a process listing (ps) for as long as this runs -
# a known limitation of gcloud sql users create/set-password, which has no
# stdin/file alternative. Acceptable for a throwaway test-tier password;
# rotate it and consider a more controlled execution path (CI runner, not an
# interactive shell) before this instance holds anything that matters.
if query gcloud sql users list --instance="$DB_INSTANCE" --project="$PROJECT_ID" --format='value(name)' 2>/dev/null | grep -qx "$DB_USER"; then
    # Re-assert the password every run: it is read from .env, which is the
    # single source of truth, so a rotated local .env value is what wins.
    run gcloud sql users set-password "$DB_USER" --instance="$DB_INSTANCE" --password="$DB_PASSWORD" --project="$PROJECT_ID" --quiet
    log_ok "User $DB_USER already exists (password re-synced from .env)"
else
    run gcloud sql users create "$DB_USER" --instance="$DB_INSTANCE" --password="$DB_PASSWORD" --project="$PROJECT_ID" --quiet
    log_ok "Created user $DB_USER"
fi

# --------------------------------------------------------------------------
# Firestore
# --------------------------------------------------------------------------
log_step "Firestore"
FS_DB="$(res '.firestore.database_id')"
if firestore_db_exists "$FS_DB"; then
    log_ok "Firestore database $FS_DB already exists"
else
    run gcloud firestore databases create \
        --database="$FS_DB" \
        --location="$REGION" \
        --type="$(res '.firestore.type')" \
        $( [[ "$(res '.firestore.delete_protection')" == "true" ]] && echo --delete-protection || echo --no-delete-protection ) \
        --project="$PROJECT_ID"
    log_ok "Created Firestore database $FS_DB"
fi

# --------------------------------------------------------------------------
# Pub/Sub topic
#
# The push subscription is created in deploy.sh: it targets the deployed
# Cloud Run URL, which does not exist until the first deploy.
# --------------------------------------------------------------------------
log_step "Pub/Sub topic"
TOPIC="$(res '.pubsub.topic')"
if pubsub_topic_exists "$TOPIC"; then
    log_ok "Topic $TOPIC already exists"
else
    run gcloud pubsub topics create "$TOPIC" --project="$PROJECT_ID"
    log_ok "Created topic $TOPIC"
fi

# --------------------------------------------------------------------------
# Secret Manager
#
# Values come from the local .env, never from this script's arguments or
# resources.json, so a secret's value is never typed on a command line or
# recorded in shell history.
# --------------------------------------------------------------------------
log_step "Secret Manager"
secret_count="$(res '.secrets | length')"
for i in $(seq 0 $((secret_count - 1))); do
    name="$(res ".secrets[$i].name")"
    env_var="$(res ".secrets[$i].env_var")"
    required="$(res ".secrets[$i].required")"
    value="$(env_get "$env_var")"

    if [[ -z "$value" ]]; then
        if [[ "$required" == "true" ]]; then
            die "$env_var is required (backs secret $name) but is blank in .env."
        fi
        log_warn "$env_var is blank in .env - skipping secret $name (that feature stays off/simulated until it is set)."
        continue
    fi

    if secret_exists "$name"; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log "[dry-run] gcloud secrets versions add $name --data-file=- --project=$PROJECT_ID (new value from \$$env_var)"
        else
            printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project="$PROJECT_ID" >/dev/null
        fi
        log_ok "Updated secret $name from \$$env_var"
    else
        if [[ "$DRY_RUN" == "true" ]]; then
            log "[dry-run] gcloud secrets create $name --data-file=- --project=$PROJECT_ID (value from \$$env_var)"
        else
            printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --project="$PROJECT_ID" >/dev/null
        fi
        log_ok "Created secret $name from \$$env_var"
    fi
done

log_step "Done"
log "Base infrastructure is provisioned. Next: ./infra/deploy.sh"
