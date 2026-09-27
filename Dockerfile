# --- Stage 1: build the React (Vite + shadcn/ui) single-page app -----------
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend ./frontend
# Vite is configured to emit into ../app/static/spa (see frontend/vite.config.ts).
RUN cd frontend && npm run build

# --- Stage 2: Python app -----------------------------------------------------
FROM python:3.11-slim

# Link the published GHCR package back to the source repository.
LABEL org.opencontainers.image.source="https://github.com/jasii/simple-music-tracker" \
      org.opencontainers.image.description="Simple Music Tracker - artist library and upcoming release tracker" \
      org.opencontainers.image.licenses="MIT"

# Avoid .pyc files and buffer issues; keep logs flowing in Docker.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMT_DB_PATH=/data/tracker.db \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Drop in the built SPA produced by the frontend stage.
COPY --from=frontend /build/app/static/spa ./app/static/spa

# The app runs as this account, re-numbered at start to PUID/PGID (default
# 1000:1000) so files under /data belong to you on the host. See
# docker-entrypoint.sh.
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g app -d /home/app -m -s /usr/sbin/nologin app
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Persisted SQLite database lives here; mount a volume to keep it.
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]

# A single worker with threads keeps the in-process scheduler and scan/refresh
# state consistent (multiple workers would each run their own copy). Threads
# are sized for the /art proxy: a page can request dozens of covers at once,
# each an IO-bound download, and they must not starve API requests.
CMD ["gunicorn", "--workers", "1", "--threads", "32", "--timeout", "120", \
     "--bind", "0.0.0.0:8080", "app.main:app"]
