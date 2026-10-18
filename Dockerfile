# Sağlıktan Keşif — API ve worker için ortak imaj (komut compose'da seçilir)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app app
WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

FROM deps AS runtime
COPY alembic.ini ./
COPY alembic ./alembic
RUN mkdir -p /app/work && chown -R app:app /app
USER app
ENV KESIF_WORK_DIR=/app/work
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1
CMD ["kesif", "api", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
