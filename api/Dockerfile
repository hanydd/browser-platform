FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv -i https://mirrors.cloud.tencent.com/pypi/simple

WORKDIR /app

ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1

RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV UV_DEFAULT_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY app ./app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
