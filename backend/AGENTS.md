# Backend

One Python 3.14 package serves the bot, workers and FastAPI API.
Use `uv` from the root or `uv --directory backend`; never bare pip.

## Layers

`domain` owns entities and Protocol ports. `application` depends on domain only.
`infrastructure` implements ports and wraps clients. It never imports application.
`entrypoints` owns composition roots. Import-linter enforces these directions
and forbids SDKs in the core. Ruff enforces formatting and complexity; mypy is strict.

The domain now owns frozen task/user/message/voice-job entities and Protocol ports.
Use MessageText.parse for messages and TaskDraft for extracted text; these are the
canonical cleaners and limits. VoiceJob transitions return a new entity or refuse
without mutation. Transcription and notification refusals have separate typed
outcomes. A real task status change strictly increases updated_at by at least one
microsecond; consumers must preserve that precision. Events exclude routing user_id
from serialization. The heuristic extractor keeps exactly one task without language
rules; the unbuilt Claude option returns that fallback marked degraded.

Test service doubles live in tests/fakes. The read-only owner-row replay is
`uv run --locked --directory backend python tests/acceptance/replay_s2.py` from root.

Application use cases receive ports. Extract before opening a unit of work.
Composable within methods record events but never commit or return them; standalone
execute results carry the events returned by commit. Publish only after commit with
publish_events; publication failures are logged without undoing persisted work.
ChangeTaskStatus locks the owner-filtered task before reading the clock. Token
authentication rejects malformed input before hashing and reads id/version together.
Repository contracts in tests/contract currently run against the transactional fake;
the PostgreSQL leg uses the same tests once the persistence adapter is installed.

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
