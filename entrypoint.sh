#!/usr/bin/env sh
# Container entrypoint. The first argument selects the process role:
#
#   web         gunicorn serving the API and the SPA (default)
#   worker      notification dispatcher + reminder/escalation sweeper
#   poller      USGS feed ingestion loop
#   migrate     apply migrations and exit
#   <anything>  run verbatim, e.g. `manage.py shell`
set -eu

ROLE="${1:-web}"

run_migrations() {
    echo "==> Applying database migrations"
    python manage.py migrate --noinput
}

seed_superuser() {
    # Only when a password is supplied; never invent one.
    if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
        echo "==> Ensuring the Safety Officer account exists"
        # SEED_DEMO_ENABLED gates only the demo locations/employees:
        # seed_demo is update_or_create on fixed IDs, so leaving it on would
        # silently recreate anything deleted through the personnel
        # directory every time this runs - and this runs on every `migrate`
        # job execution, i.e. every deploy. Off by default for that reason.
        # The admin account itself is always ensured/kept in sync with
        # .env regardless: it is a Django auth User, not personnel-directory
        # data, and a fresh database needs some way in.
        demo_flag=""
        if [ "${SEED_DEMO_ENABLED:-false}" != "true" ]; then
            demo_flag="--skip-demo-data"
        fi
        python manage.py seed_demo \
            --admin-username "${DJANGO_SUPERUSER_USERNAME:-safetyofficer}" \
            --admin-email "${DJANGO_SUPERUSER_EMAIL:-safety@example.com}" \
            --admin-password "${DJANGO_SUPERUSER_PASSWORD}" \
            $demo_flag
    fi
}

case "$ROLE" in
    web)
        # RUN_MIGRATIONS_ON_START is convenient for Compose and single-instance
        # deploys. With several Cloud Run instances, run `migrate` as a
        # separate job instead so they do not race.
        if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
            run_migrations
            seed_superuser
        fi
        echo "==> Starting gunicorn on :${PORT:-8080}"
        exec gunicorn calltree.wsgi:application \
            --bind "0.0.0.0:${PORT:-8080}" \
            --workers "${GUNICORN_WORKERS:-2}" \
            --threads "${GUNICORN_THREADS:-8}" \
            --timeout "${GUNICORN_TIMEOUT:-60}" \
            --graceful-timeout 30 \
            --access-logfile - \
            --error-logfile -
        ;;

    worker)
        echo "==> Starting the notification dispatcher"
        exec python manage.py run_dispatcher \
            --loop --interval "${DISPATCHER_INTERVAL:-20}"
        ;;

    poller)
        echo "==> Starting the USGS ingestion poller"
        exec python manage.py poll_usgs \
            --loop --interval "${USGS_POLL_INTERVAL_SECONDS:-60}"
        ;;

    migrate)
        run_migrations
        seed_superuser
        ;;

    *)
        exec python manage.py "$@"
        ;;
esac
