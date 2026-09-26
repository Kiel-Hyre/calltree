#!/usr/bin/env bash
# Shared helpers for provision.sh / deploy.sh / destroy.sh.
#
# Sourced, never executed directly:  source "$(dirname "$0")/lib.sh"

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$INFRA_DIR/.." && pwd)"
RESOURCES_JSON="$INFRA_DIR/resources.json"
ENV_FILE="$ROOT_DIR/.env"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-$ROOT_DIR/credentials.json}"

# This is a test environment: provision/deploy read the same root .env as
# local dev, unmodified - including its DJANGO_DEBUG. Keep that in mind
# before pointing this toolkit at anything that isn't disposable.

DRY_RUN="${DRY_RUN:-false}"
FORCE="${FORCE:-false}"

# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
_c_info=$'\033[36m'; _c_warn=$'\033[33m'; _c_err=$'\033[31m'; _c_ok=$'\033[32m'; _c_reset=$'\033[0m'

log()      { printf '%s\n' "$*"; }
log_step() { printf '\n%s==>%s %s\n' "$_c_info" "$_c_reset" "$*"; }
log_info() { printf '%s\n' "$*"; }
log_ok()   { printf '%s✓%s %s\n' "$_c_ok" "$_c_reset" "$*"; }
log_warn() { printf '%s! %s%s\n' "$_c_warn" "$*" "$_c_reset" >&2; }
log_err()  { printf '%sERROR:%s %s\n' "$_c_err" "$_c_reset" "$*" >&2; }
die()      { log_err "$*"; exit 1; }

confirm() {
    # Skipped entirely with --yes/FORCE=true, which the destructive scripts
    # need for non-interactive/CI use - but that is opt-in, never the default.
    local prompt="${1:-Continue?}"
    if [[ "$FORCE" == "true" ]]; then
        return 0
    fi
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# --------------------------------------------------------------------------
# Command execution
#
# Every gcloud/gsutil/docker call in the three scripts goes through run(),
# so DRY_RUN=true (or --dry-run) prints exactly what would happen without
# touching the project. This is how this toolkit gets tested without a
# gcloud install: stub `run`'s underlying commands and diff the transcript.
# --------------------------------------------------------------------------
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '[dry-run] %s\n' "$(quote_args "$@")"
        return 0
    fi
    "$@"
}

# Same as run(), but for commands whose successful real output is noise
# (add-iam-policy-binding's echoed policy document, for example). The
# dry-run preview line still always prints - it must, since a call site
# adding `>/dev/null` to redirect the *real* command's output would also
# swallow run()'s own dry-run printf, making the preview silently disappear.
# That exact mistake is why this function exists rather than a bare
# `run gcloud ... >/dev/null` at each such call site.
run_quiet() {
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '[dry-run] %s\n' "$(quote_args "$@")"
        return 0
    fi
    "$@" >/dev/null
}

# Same as run(), but always executes even under --dry-run: for read-only
# lookups (does this resource already exist?) that the rest of the script's
# branching depends on.
query() {
    "$@"
}

quote_args() {
    local out="" arg
    for arg in "$@"; do
        if [[ "$arg" == *" "* || "$arg" == "" ]]; then
            out+=" '$arg'"
        else
            out+=" $arg"
        fi
    done
    printf '%s' "${out# }"
}

# --------------------------------------------------------------------------
# Prerequisites
# --------------------------------------------------------------------------
require_cmd() {
    local missing=()
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if ((${#missing[@]})); then
        die "Missing required command(s): ${missing[*]}. Install them and retry."
    fi
}

# --------------------------------------------------------------------------
# resources.json access
#
# Every value is piped through `tr -d '\r'`: a native Windows jq.exe can
# write CRLF into a pipe even though the source file and this script are
# both plain LF, and an embedded \r in a value that later gets printed (or
# passed as a --project=... flag right before more text) makes the terminal
# overwrite part of the same line instead of advancing - the exact garbled,
# overlapping output this toolkit produced the first time it ran on a real
# Windows machine. Stripping it here, once, means neither script has to
# know or care which jq build is in front of it.
# --------------------------------------------------------------------------
res() {
    # res '.region' -> asia-southeast1 (raw, unquoted)
    jq -r "$1" "$RESOURCES_JSON" | tr -d '\r'
}

res_json() {
    # res_json '.database' -> the sub-object as compact JSON, for jq -c iteration
    jq -c "$1" "$RESOURCES_JSON" | tr -d '\r'
}

PROJECT_ID="$(res '.project_id')"
REGION="$(res '.region')"
PREFIX="$(res '.naming_prefix')"

# --------------------------------------------------------------------------
# .env access
#
# Reads a single key from the root .env without sourcing the whole file
# (which would also execute anything malformed in it) and without ever
# echoing the value to the terminal by default.
# --------------------------------------------------------------------------
env_get() {
    local key="$1" default="${2:-}"
    [[ -f "$ENV_FILE" ]] || { printf '%s' "$default"; return; }
    local line
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1)"
    if [[ -z "$line" ]]; then
        printf '%s' "$default"
    else
        # Strip a stray trailing \r: .env can pick up CRLF line endings from
        # a Windows editor even though it is untracked by git (so
        # .gitattributes' `eol=lf` normalisation never touches it). Left in,
        # it would ride along inside every secret value this feeds to
        # `gcloud secrets create --data-file=-`, silently corrupting it with
        # an invisible trailing character the app's own value never has.
        printf '%s' "${line#*=}" | tr -d '\r'
    fi
}

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
gcp_authenticate() {
    [[ -f "$CREDENTIALS_FILE" ]] || die "Service account key not found at $CREDENTIALS_FILE (see infra/README.md)."

    local active
    active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
    local expected
    expected="$(jq -r '.client_email' "$CREDENTIALS_FILE")"

    if [[ "$active" != "$expected" ]]; then
        log_info "Activating service account $expected"
        run gcloud auth activate-service-account --key-file="$CREDENTIALS_FILE" --project="$PROJECT_ID"
    fi
    run gcloud config set project "$PROJECT_ID" --quiet

    # No extra "can this identity reach the project?" probe beyond the
    # activation above: `set -euo pipefail` already stops the script the
    # moment any real gcloud call fails, and that failure carries gcloud's
    # own specific, actionable error - which is more reliable than a guess
    # of ours. A first version of this function called `gcloud projects
    # describe` here as a sanity check and printed "not enough IAM roles?"
    # on failure; on a fresh project the real cause was almost always the
    # unrelated fact that the Cloud Resource Manager API - which that exact
    # describe call itself depends on - was not enabled yet, so the
    # generated diagnostic was actively wrong. `gcloud services enable`,
    # the very next step in provision.sh, is what actually proves access.
}

# --------------------------------------------------------------------------
# Existence checks (used to make every script idempotent - safe to re-run)
# --------------------------------------------------------------------------
sql_instance_exists() {
    query gcloud sql instances describe "$1" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

secret_exists() {
    query gcloud secrets describe "$1" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

pubsub_topic_exists() {
    query gcloud pubsub topics describe "$1" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

pubsub_sub_exists() {
    query gcloud pubsub subscriptions describe "$1" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

artifact_repo_exists() {
    query gcloud artifacts repositories describe "$1" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

service_account_exists() {
    query gcloud iam service-accounts describe "$1" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

firestore_db_exists() {
    query gcloud firestore databases describe --database="$1" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

cloud_run_service_exists() {
    query gcloud run services describe "$1" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

cloud_run_job_exists() {
    query gcloud run jobs describe "$1" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}

scheduler_job_exists() {
    query gcloud scheduler jobs describe "$1" --location="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1
}
