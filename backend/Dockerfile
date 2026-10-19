# 공용 백엔드 이미지: api, workers, business-mcp, rag-mcp
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global user.email "auditor-bot@demo.io" \
    && git config --global user.name "AI Security Auditor" \
    && git config --global --add safe.directory '*'

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/ ./
COPY config/ /app/config/
COPY rules/ /app/rules/
COPY datasets/ /app/datasets/
COPY knowledge/ /app/knowledge/
COPY targets/ /app/targets/

ENV CONFIG_DIR=/app/config RULES_DIR=/app/rules DATASETS_DIR=/app/datasets \
    KNOWLEDGE_DIR=/app/knowledge REPORTS_DIR=/app/reports WORKSPACE_DIR=/workspace
