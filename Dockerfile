FROM python:3.11-slim

# Avoid .pyc files and buffer issues; keep logs flowing in Docker.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMT_DB_PATH=/data/tracker.db \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Persisted SQLite database lives here; mount a volume to keep it.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

# A single worker with threads keeps the in-process scheduler and scan/refresh
# state consistent (multiple workers would each run their own copy).
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "120", \
     "--bind", "0.0.0.0:8080", "app.main:app"]
