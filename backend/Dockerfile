FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- test stage: `docker compose --profile test run --rm tests` ----
FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
CMD ["python", "-m", "pytest", "-v"]

# ---- runtime stage ----
FROM base AS runtime
COPY app ./app
RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data
USER appuser
ENV DATABASE_PATH=/data/transit.db FRONTEND_DIR=/nonexistent
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
