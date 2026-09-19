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
        python manage.py seed_demo \
            --admin-username "${DJANGO_SUPERUSER_USERNAME:-safetyofficer}" \
            --admin-email "${DJANGO_SUPERUSER_EMAIL:-safety@example.com}" \
            --admin-password "${DJANGO_SUPERUSER_PASSWORD}"
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
