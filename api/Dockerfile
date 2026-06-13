# syntax=docker/dockerfile:1

# --- Builder : installe les dépendances avec uv ---
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.4.*

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

# Environnement virtuel autonome, copiable tel quel dans le runner
RUN uv venv /opt/venv \
    && . /opt/venv/bin/activate \
    && uv pip install --no-cache .

# --- Runner : image minimale, utilisateur non-root ---
FROM python:3.11-slim AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Outils pour le healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
