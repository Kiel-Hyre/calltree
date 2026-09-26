#!/usr/bin/env bash
# Build the image, push it, and deploy the Cloud Run service + jobs described
# in resources.json. Requires provision.sh to have run first (the database,
# secrets, Pub/Sub topic and runtime service account all have to exist).
#
# Idempotent - safe to re-run for a new release. Handles the Cloud Run
# self-reference problem (the app needs its own URL for PUBLIC_BASE_URL and
# the Pub/Sub push target, which do not exist before the first deploy) with
# a two-pass deploy: create/update the service, read back its URL, then patch
# the URL-dependent settings.
#
# Usage:
#   ./infra/deploy.sh                 # build, push, deploy everything
#   ./infra/deploy.sh --skip-build    # redeploy the last pushed image
#   ./infra/deploy.sh --dry-run       # print every gcloud/docker command
#
# Requires: gcloud, jq, docker, and credentials.json in the repo root.

set -euo pipefail

SKIP_BUILD=false
SKIP_MIGRATE=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --yes|-y) FORCE=true ;;
        --skip-build) SKIP_BUILD=true ;;
        --skip-migrate) SKIP_MIGRATE=true ;;
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
[[ "$SKIP_BUILD" == "true" ]] || require_cmd docker
gcp_authenticate

log_step "Deploying to $PROJECT_ID ($REGION)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY RUN - no gcloud/docker command below will actually execute."

AR_REPO="$(res '.artifact_registry.repository')"
SA_ID="$(res '.service_account.account_id')"
SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
DB_INSTANCE="$(res '.database.instance_id')"
DB_NAME="$(res '.database.database_name')"
DB_USER="$(res '.database.app_user')"
DB_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
TOPIC="$(res '.pubsub.topic')"
IMAGE_HOST="${REGION}-docker.pkg.dev"
IMAGE_BASE="${IMAGE_HOST}/${PROJECT_ID}/${AR_REPO}/calltree"

# --------------------------------------------------------------------------
# Preconditions this script does not create itself
# --------------------------------------------------------------------------
sql_instance_exists "$DB_INSTANCE" || die "Cloud SQL instance $DB_INSTANCE does not exist. Run ./infra/provision.sh first."
pubsub_topic_exists "$TOPIC" || die "Pub/Sub topic $TOPIC does not exist. Run ./infra/provision.sh first."
service_account_exists "$SA_EMAIL" || die "Service account $SA_EMAIL does not exist. Run ./infra/provision.sh first."

# --------------------------------------------------------------------------
# Build and push
# --------------------------------------------------------------------------
TAG="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE="${IMAGE_BASE}:${TAG}"
IMAGE_LATEST="${IMAGE_BASE}:latest"

if [[ "$SKIP_BUILD" == "true" ]]; then
    log_step "Skipping build (--skip-build); redeploying $IMAGE_LATEST"
    IMAGE="$IMAGE_LATEST"
else
    log_step "Building $IMAGE"
    run gcloud auth configure-docker "$IMAGE_HOST" --quiet
    run docker build -t "$IMAGE" -t "$IMAGE_LATEST" "$ROOT_DIR"
    log_step "Pushing image"
    run docker push "$IMAGE"
    run docker push "$IMAGE_LATEST"
    log_ok "Pushed $IMAGE"
fi

# --------------------------------------------------------------------------
# Assemble the env vars and secrets every container role shares
# --------------------------------------------------------------------------
DB_PASSWORD="$(env_get POSTGRES_PASSWORD)"
DATABASE_URL="postgres://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${DB_CONNECTION_NAME}"

# Plain (non-secret) env vars: forwarded verbatim from .env, per resources.json's list.
plain_env=""
mapfile -t plain_keys < <(res '.plain_env_vars[]')
for key in "${plain_keys[@]}"; do
    value="$(env_get "$key")"
    [[ -n "$value" ]] || continue
    plain_env+="${key}=${value},"
done

# Values this script computes itself, rather than trusting .env, so they can
# never point at the wrong project, database or host:
plain_env+="GCP_PROJECT_ID=${PROJECT_ID},"
plain_env+="FIRESTORE_DATABASE=$(res '.firestore.database_id'),"
plain_env+="PUBSUB_TOPIC_NOTIFICATIONS=${TOPIC},"
# .run.app accepts a leading-dot wildcard in both settings, which is what
# makes the *first* deploy possible before the service's real URL exists.
plain_env+="DJANGO_ALLOWED_HOSTS=.run.app,"
plain_env+="DJANGO_CSRF_TRUSTED_ORIGINS=https://*.run.app,"
# Corrected to the real URL once it is known - see "Fix up the URL" below.
plain_env+="PUBLIC_BASE_URL=https://placeholder.run.app,"
plain_env="${plain_env%,}"

# Secrets: only ones that actually exist (an optional secret with a blank
# .env value was skipped by provision.sh, and Cloud Run will refuse to
# deploy against a --set-secrets entry naming a secret that is not there).
secret_env=""
secret_count="$(res '.secrets | length')"
for i in $(seq 0 $((secret_count - 1))); do
    name="$(res ".secrets[$i].name")"
    env_var="$(res ".secrets[$i].env_var")"
    secret_exists "$name" || continue
    secret_env+="${env_var}=${name}:latest,"
done
secret_env="${secret_env%,}"

common_flags=(
    --project="$PROJECT_ID"
    --region="$REGION"
    --image="$IMAGE"
    --service-account="$SA_EMAIL"
    --set-cloudsql-instances="$DB_CONNECTION_NAME"
    --set-env-vars="$plain_env"
)
[[ -n "$secret_env" ]] && common_flags+=(--set-secrets="$secret_env")

# --------------------------------------------------------------------------
# Web service - first pass (URL does not exist yet, hence the placeholder
# PUBLIC_BASE_URL and the .run.app wildcards above)
# --------------------------------------------------------------------------
log_step "Deploying web service"
WEB_SERVICE="$(res '.cloud_run.web.service_name')"
web_flags=(
    "${common_flags[@]}"
    --min-instances="$(res '.cloud_run.web.min_instances')"
    --max-instances="$(res '.cloud_run.web.max_instances')"
    --cpu="$(res '.cloud_run.web.cpu')"
    --memory="$(res '.cloud_run.web.memory')"
    --concurrency="$(res '.cloud_run.web.concurrency')"
    --timeout="$(res '.cloud_run.web.timeout_seconds')"
    --port=8080
    --quiet
)
[[ "$(res '.cloud_run.web.allow_unauthenticated')" == "true" ]] \
    && web_flags+=(--allow-unauthenticated) || web_flags+=(--no-allow-unauthenticated)
# Multiple instances could race a startup migration; a dedicated one-off
# job execution (below) runs it exactly once instead.
web_flags+=(--update-env-vars="RUN_MIGRATIONS_ON_START=false")

run gcloud run deploy "$WEB_SERVICE" "${web_flags[@]}"
log_ok "Deployed $WEB_SERVICE"

# --------------------------------------------------------------------------
# Migrations - one execution, not the web service's own startup, so several
# instances booting at once can never run `migrate` concurrently.
# --------------------------------------------------------------------------
DISPATCHER_JOB="$(res '.cloud_run.jobs.dispatcher.job_name')"

deploy_job() {
    # $1 = jq path under .cloud_run.jobs, e.g. "dispatcher"
    local key="$1" job_name args_json cpu memory retries timeout
    job_name="$(res ".cloud_run.jobs.${key}.job_name")"
    args_json="$(res_json ".cloud_run.jobs.${key}.args")"
    cpu="$(res ".cloud_run.jobs.${key}.cpu")"
    memory="$(res ".cloud_run.jobs.${key}.memory")"
    retries="$(res ".cloud_run.jobs.${key}.max_retries")"
    timeout="$(res ".cloud_run.jobs.${key}.task_timeout_seconds")"
    mapfile -t args < <(jq -r '.[]' <<<"$args_json")

    local verb="update"
    cloud_run_job_exists "$job_name" || verb="create"

    run gcloud run jobs "$verb" "$job_name" \
        "${common_flags[@]}" \
        --command="/app/entrypoint.sh" \
        --args="$(IFS=,; echo "${args[*]}")" \
        --cpu="$cpu" \
        --memory="$memory" \
        --max-retries="$retries" \
        --task-timeout="${timeout}s" \
        --quiet
    log_ok "${verb^}d job $job_name"

    run_quiet gcloud run jobs add-iam-policy-binding "$job_name" \
        --region="$REGION" --project="$PROJECT_ID" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/run.invoker" --quiet
}

log_step "Deploying Cloud Run jobs"
deploy_job dispatcher
deploy_job poller

if [[ "$SKIP_MIGRATE" == "true" ]]; then
    log_warn "Skipping migrations (--skip-migrate)."
else
    log_step "Running migrations (one-off execution of $DISPATCHER_JOB)"
    run gcloud run jobs execute "$DISPATCHER_JOB" \
        --region="$REGION" --project="$PROJECT_ID" \
        --args=migrate --wait
    log_ok "Migrations applied"
fi

# --------------------------------------------------------------------------
# Fix up the URL-dependent settings now that the service has a real URL
# --------------------------------------------------------------------------
log_step "Resolving the deployed URL"
if [[ "$DRY_RUN" == "true" ]]; then
    SERVICE_URL="https://${WEB_SERVICE}-dry-run.a.run.app"
    log "[dry-run] would read the real URL with: gcloud run services describe $WEB_SERVICE --region=$REGION --format=value(status.url)"
else
    SERVICE_URL="$(gcloud run services describe "$WEB_SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')"
fi
log_ok "Service URL: $SERVICE_URL"

log_step "Patching PUBLIC_BASE_URL and CSRF origin to the real URL"
run gcloud run services update "$WEB_SERVICE" \
    --project="$PROJECT_ID" --region="$REGION" --quiet \
    --update-env-vars="PUBLIC_BASE_URL=${SERVICE_URL},DJANGO_CSRF_TRUSTED_ORIGINS=https://*.run.app;${SERVICE_URL}"
log_ok "PUBLIC_BASE_URL is now $SERVICE_URL"

# --------------------------------------------------------------------------
# Pub/Sub push subscription - targets the now-known URL, carries the shared
# secret as ?token=..., since that is the endpoint's own auth pattern.
# --------------------------------------------------------------------------
log_step "Pub/Sub push subscription"
SUBSCRIPTION="$(res '.pubsub.push_subscription')"
PUBSUB_PUSH_TOKEN="$(env_get PUBSUB_PUSH_TOKEN)"
PUSH_ENDPOINT="${SERVICE_URL}/api/webhooks/pubsub/"
[[ -n "$PUBSUB_PUSH_TOKEN" ]] && PUSH_ENDPOINT="${PUSH_ENDPOINT}?token=${PUBSUB_PUSH_TOKEN}"

if pubsub_sub_exists "$SUBSCRIPTION"; then
    run gcloud pubsub subscriptions update "$SUBSCRIPTION" \
        --push-endpoint="$PUSH_ENDPOINT" \
        --project="$PROJECT_ID"
    log_ok "Updated push subscription $SUBSCRIPTION -> $PUSH_ENDPOINT"
else
    run gcloud pubsub subscriptions create "$SUBSCRIPTION" \
        --topic="$TOPIC" \
        --push-endpoint="$PUSH_ENDPOINT" \
        --ack-deadline="$(res '.pubsub.ack_deadline_seconds')" \
        --min-retry-delay="$(res '.pubsub.min_retry_backoff')" \
        --max-retry-delay="$(res '.pubsub.max_retry_backoff')" \
        --project="$PROJECT_ID"
    log_ok "Created push subscription $SUBSCRIPTION -> $PUSH_ENDPOINT"
fi
[[ -z "$PUBSUB_PUSH_TOKEN" ]] && log_warn "PUBSUB_PUSH_TOKEN is blank: this endpoint accepts pushes with no credential."

# --------------------------------------------------------------------------
# Cloud Scheduler - periodic triggers for the two Cloud Run Jobs
# --------------------------------------------------------------------------
log_step "Cloud Scheduler"
deploy_schedule() {
    local key="$1" job_name schedule scheduler_name run_api
    job_name="$(res ".cloud_run.jobs.${key}.job_name")"
    schedule="$(res ".cloud_run.jobs.${key}.schedule")"
    scheduler_name="$(res ".cloud_run.jobs.${key}.scheduler_job_name")"
    run_api="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${job_name}:run"

    if scheduler_job_exists "$scheduler_name"; then
        run gcloud scheduler jobs update http "$scheduler_name" \
            --schedule="$schedule" --uri="$run_api" --http-method=POST \
            --oauth-service-account-email="$SA_EMAIL" \
            --location="$REGION" --project="$PROJECT_ID"
        log_ok "Updated schedule $scheduler_name ($schedule -> $job_name)"
    else
        run gcloud scheduler jobs create http "$scheduler_name" \
            --schedule="$schedule" --uri="$run_api" --http-method=POST \
            --oauth-service-account-email="$SA_EMAIL" \
            --location="$REGION" --project="$PROJECT_ID"
        log_ok "Created schedule $scheduler_name ($schedule -> $job_name)"
    fi
}
deploy_schedule dispatcher
deploy_schedule poller

log_step "Done"
log "Web:        $SERVICE_URL"
log "Admin:      $SERVICE_URL/admin/"
log "Health:     $SERVICE_URL/api/healthz/"
log ""
log "The Safety Officer account (DJANGO_SUPERUSER_PASSWORD in .env) was seeded"
log "by the migration job's seed_demo step, same as local/Docker Compose."
