# Backend

One Python 3.14 package serves the bot, workers and FastAPI API.
Use `uv` from the root or `uv --directory backend`; never bare pip.

## Layers

`domain` owns entities and Protocol ports. `application` depends on domain only.
`infrastructure` implements ports and wraps clients. It never imports application.
`entrypoints` owns composition roots. Import-linter enforces these directions
and forbids SDKs in the core. Ruff enforces formatting and complexity; mypy is strict.

## Settings

Use `class BotSettings(BaseAppSettings, ExtractorSettingsMixin)` and equivalents.
The extractor mixin is a BaseModel, never BaseSettings. The base reads the root
`.env` in source checkouts. Validate each process's own variables at startup.
Missing optional provider keys degrade behavior instead of preventing startup.

## Checks

Run `uv run --locked --directory backend ruff check .`, `ruff format --check .`,
`mypy`, `lint-imports` and `pytest -m 'not integration'` through the same prefix.
From S1.2, `pytest -m integration` requires the real local PostgreSQL and Redis.
Integration tests fail with an actionable message when infrastructure is down;
they never silently skip.
Collect-only runs do not probe the stores. `pytest tests/unit` also runs without them.

## Shared infrastructure and test isolation

Use root `yarn infra:up` and `yarn infra:stop`; direct Compose bypasses the guard.
The shared stack belongs to the machine. Leave it running; never delete its volumes.
Each writing test session creates `taskbot_test_<unix-time>_<random>`, applies
schema/migrations and drops its database. S2 owns that fixture and stale cleanup.
Tests never write to the development database. Each Redis session uses unique
prefixes for keys, channels, locks and Celery queues and removes its own keys;
S3 owns that fixture. No test assumes exclusive use. S1's tests only read.

## API schema and image

Change Pydantic response models, then run root `yarn api:schema`; generated
TypeScript and Zod files are never edited by hand. `yarn api:check` detects drift.
Route names become OpenAPI operation ids; choose unique names.
A stage that adds dependencies or a top-level backend directory, such as
`alembic/`, reruns the backend smoke build and records its result.
