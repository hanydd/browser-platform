FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.4.21 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY app ./app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
