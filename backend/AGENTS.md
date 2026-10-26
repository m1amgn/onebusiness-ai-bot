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
from serialization. Extraction carries exactly one TaskDraft in its task field.
The heuristic extractor keeps one task without language rules; the unbuilt Claude option returns
that fallback marked degraded. Storage positions remain fixed by migration 0001;
each new message creates its task at position 0.

Test service doubles live in tests/fakes. The read-only owner-row replay is
`uv run --locked --directory backend python tests/acceptance/replay_s2.py` from root.

Application use cases receive ports. Extract before opening a unit of work.
Composable within methods record events but never commit or return them; standalone
execute results carry the events returned by commit. Publish only after commit with
publish_events; publication failures are logged without undoing persisted work.
CreateTasksFromMessage.within returns CreatedMessage or DuplicateMessage; standalone
execute returns TasksCreated (which adds events) or DuplicateMessage. S5 composes the
within result with voice-job completion in one unit of work. TaskView.from_task in
domain/events.py converts an entity for events; entities do not import wire models.
ChangeTaskStatus locks the owner-filtered task before reading the clock. Token
authentication rejects malformed input before hashing and reads id/version together.
Repository contracts in tests/contract run against both the transactional fake and
real PostgreSQL. Only the PostgreSQL parameter is marked integration. Tests that
need a real unit of work request postgres_uows; sql_sessions uses a two-slot pool.

SQL repositories use Core statements with table-column RETURNING, not ORM objects;
the session identity map stays empty. Row mappings are validated into frozen domain
entities. SqlAlchemyUnitOfWork owns commit, rollback and event collection. Atomic
guarded writes own voice-job transitions; sweeps lock at most 100 rows with SKIP
LOCKED and stamp them in the same transaction. SystemClock uses aware UTC; tokens
use secrets.token_urlsafe(32), and only their SHA-256 hashes enter the database.

## Schema and test databases

Typed tables are in infrastructure/sqlalchemy/tables.py. Alembic revisions are
immutable schema snapshots under alembic/versions; never import current models
from a historical revision. Run root yarn db:migrate for development migrations.
The migration environment serializes upgrades with a transaction advisory lock.
Generated revisions run Ruff formatting/lint hooks; use uv for Alembic commands.
A stage adding a migration records the shared development-head change in its diary.
S2 adds revision 0001; apply it with yarn db:migrate before using development data.
Tests migrate only owned databases; they never advance the development head.

Writing integration tests request database_url and clean_database. The session
owns a taskbot_test_<unix-time>_<8 hex> database, migrates it and holds an anchor
connection until teardown. Cleanup checks its name and current_database before
truncating tables. Never pass the development database to destructive test helpers.
Stale cleanup uses age only for candidates, skips live connections and uses plain
DROP without FORCE so a late connection is protected too. FORCE is reserved for
the context's own database. Use small test pools; the shared server has 100 slots.
Migrations needing an empty database use tests.databases.test_database instead of
the session database. Keep both pytest async loop scopes at session. Subprocess
checks use tests.processes.run_process, which kills the whole group on timeout.

## Seed entry point

Root yarn dev:seed runs entrypoints/seed. Its composition root binds validated
SeedSettings, SQL units of work, clock, extractor and token adapters. The reusable
seed function invokes standalone task creation and token issue; duplicate messages
still rotate the token. CLI message ids are negative microsecond timestamps to
avoid real Telegram ids. The CLI validates user ids as positive signed int64 values
before it loads settings. Print tokens only in this explicit development output;
result representations hide token/link. One post-commit call site currently logs
unpublished events; S3 connects publish_events there. Always dispose the engine.

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
