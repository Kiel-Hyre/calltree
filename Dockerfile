# syntax=docker/dockerfile:1

# =========================================================================
# Stage 1 - build the Vue 3 single-page application
# =========================================================================
FROM node:22-alpine AS frontend

WORKDIR /build

# Copy manifests first so a source-only change reuses the install layer.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# =========================================================================
# Stage 2 - Python runtime
# =========================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=calltree.settings

WORKDIR /app

# curl is used by the container health check; libpq is needed by psycopg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY manage.py ./
COPY calltree/ ./calltree/
COPY core/ ./core/
COPY ingestion/ ./ingestion/
COPY engine/ ./engine/
COPY dissemination/ ./dissemination/
COPY accountability/ ./accountability/
COPY api/ ./api/
COPY static/ ./static/
COPY templates/ ./templates/
COPY entrypoint.sh ./

# The compiled SPA, exactly where settings.FRONTEND_DIST expects it.
COPY --from=frontend /build/dist/ ./frontend/dist/

# Collect static with throwaway settings: the real SECRET_KEY and database
# are runtime concerns and must not be baked into the image.
RUN DJANGO_SECRET_KEY=build-only-key \
    DJANGO_DEBUG=false \
    DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput

RUN chmod +x entrypoint.sh \
    && mkdir -p /app/data \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Cloud Run injects PORT; 8080 is its default.
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/api/healthz/" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["web"]
