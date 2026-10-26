FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --uid 10001 taskbot
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 backend/alembic.ini ./alembic.ini
COPY --chown=10001:10001 backend/alembic ./alembic
ENV PATH="/app/.venv/bin:$PATH"
USER taskbot
RUN python -c "from taskbot.entrypoints.api.app import create_app; create_app(); import celery, aiogram, openai, anthropic, sqlalchemy, asyncpg, alembic, redis, httpx"
CMD ["python", "-m", "uvicorn", "--factory", "taskbot.entrypoints.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
