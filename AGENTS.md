# Contributor and agent guide

This file holds the rules for everyone who changes this repository: people and coding agents. `CLAUDE.md` imports it, so the rules live in one place. Module guides (`backend/AGENTS.md`, `frontend/AGENTS.md`) hold the details of each side; this file holds what crosses them.

## Project

AI Task Bot turns Telegram text and voice messages into tasks. A worker transcribes voice messages through Redis queues, PostgreSQL stores tasks and users, and a web dashboard shows the tasks in real time. The bot, the worker and the API (FastAPI) are written in Python; the dashboard is a React application written in TypeScript. `README.md` explains setup, `docs/development.md` and `docs/deployment.md` are the developer and operator guides, `docs/architecture/` holds the diagrams, and `docs/plans/` holds the design of each part.

### System map

| Process (Compose service) | Runs | Talks to |
|---|---|---|
| `bot` | aiogram long polling, one instance per token (Redis lock `bot:poll`, heartbeat `heartbeat:bot`) | Telegram, PostgreSQL, Redis, Anthropic |
| `worker-transcribe` | Celery, queue `transcription`, prefork, concurrency 1 | Telegram (file download), OpenAI or whisper, Anthropic, PostgreSQL, Redis |
| `worker-notify` | Celery, queue `notification`, concurrency 2, embedded beat for the recovery sweep | Telegram (edit and reply), PostgreSQL, Redis |
| `api` | FastAPI: REST, realtime tickets, server-sent events | PostgreSQL, Redis |
| `web` | nginx: the built dashboard, `/api` proxied to `api` | `api` |
| `migrate` | one-shot `alembic upgrade head`; ends `exited (0)` before the Python services start | PostgreSQL |
| `postgres`, `redis` | stores | — |
| `whisper` (profile `whisper`) | whisper.cpp server on the CPU, opt-in | — |
| `seed` (profile `tools`) | creates a user, tasks and a dashboard link without Telegram | PostgreSQL, Redis |

The five Python services are commands on one `backend` image. The two worker services run the same launcher and differ by `WORKER_ROLE`.

## Commands

- Python: `uv`. Never install packages with bare `pip`.
- TypeScript: `yarn`. Never run `npm install`.

| Command | What it does |
|---|---|
| `yarn infra:up` / `yarn infra:stop` | start or stop the shared development PostgreSQL and Redis (stop removes nothing) |
| `yarn verify` | the single verification command: file size, scripts, backend lock, Ruff, strict mypy, import contracts, pytest (unit and integration), API schema drift, frontend lint, format, typecheck and tests; needs `yarn infra:up` first |
| `yarn test:unit` | unit tests only, both sides |
| `yarn db:migrate` | `alembic upgrade head` against the development database |
| `yarn api:schema` | export `backend/openapi.json` and regenerate the dashboard's types and Zod schemas from it |
| `yarn dev:bot`, `yarn dev:api`, `yarn dev:web` | run the bot, the API with reload, the dashboard with Vite |
| `yarn dev:worker transcribe` / `notify` | run a worker role through the launcher |
| `yarn dev:whisper` | start the local whisper.cpp server for `TRANSCRIPTION_PROVIDER=whisper_server` |
| `yarn dev:seed <telegram user id> [text]` | create tasks and print a dashboard link without the bot |
| `yarn live:transcription`, `yarn live:extraction` | owner-controlled live checks against the paid providers; never part of `yarn verify` |
| `docker compose up --build -d --wait` | the delivered stack (see `README.md`) |

## General code rules

- Code, comments, documentation, design notes and commit messages are written in English.
- Choose the simplest design that meets the requirement. An abstraction earns its place by a second real use or by a test seam; do not add one for a hypothetical case.
- SOLID: a module, class or function has one responsibility; new behaviour extends through an interface instead of a new branch in an existing flow. Domain and application code depend on interfaces; adapters wrap the Telegram, transcription, Redis and database clients.
- DRY: each concept has one canonical implementation. Reuse it before writing a new helper. Data shared across the language boundary has one schema, and the other side's types are generated from it, never written by hand.
- Every external input is validated at the boundary where it enters: Telegram updates, HTTP requests, queue payloads, real-time events, environment variables and the model's output.
- Prefer early returns to nested conditions.
- Names describe what a thing is or does.
- An implementation file stays under about 700 lines. Extract a cohesive responsibility instead of growing a file past that.
- Code passes typecheck and lint. A suppressed rule carries a comment that says why.

## Architecture rules

### Layers (checked by tools)

```
entrypoints ──► application ──► domain
     │                              ▲
     └──────► infrastructure ───────┘
```

- `domain` imports nothing from the other layers and no SDK. `application` imports `domain` only. `infrastructure` implements domain ports and wraps one SDK each; it never imports `application`. `entrypoints` are the composition roots and the only place where adapters are bound to ports.
- import-linter enforces the layers and forbids every SDK (aiogram, anthropic, openai, celery, kombu, redis, sqlalchemy, asyncpg, alembic, fastapi, starlette, httpx, httpx2) in `domain` and `application`.
- Dashboard: eslint-plugin-boundaries allows `app → features, components, lib`; `features → components, api, lib`; `components → lib`; `api → lib`; `lib → nothing`.

### Ports and adapters

| Port (`backend/src/taskbot/domain/ports/`) | Adapters (`infrastructure/`) |
|---|---|
| `UserRepository`, `InboundMessageRepository`, `TaskRepository`, `VoiceJobRepository`, `UnitOfWork` | SQLAlchemy Core with `RETURNING`; transactional fakes in tests |
| `Clock`, `TokenGenerator`, `TokenHasher` | aware UTC clock, `secrets.token_urlsafe(32)`, SHA-256 (only hashes are stored) |
| `TaskExtractor` | `claude` (Claude Haiku through the Anthropic SDK), `heuristic` (no model) |
| `Transcriber` | `openai`, `whisper_server`, `fake` (marked `[demo transcript]`) |
| `JobQueue` | Celery `send_task` to `transcription` or `notification` |
| `EventPublisher`, `EventSubscriber`, `TicketStore` | Redis pub/sub on `events:user:<id>`; one-time 30-second tickets with `GETDEL` |
| `UserNotifier`, `VoiceFileSource` | aiogram Bot API |

### Flow rules

- **Extract before the transaction.** A model, transcription, Telegram or file-download call never runs inside a unit of work. Use cases call the extractor first, then open one `UnitOfWork` for the writes.
- **The transaction belongs to the outermost caller.** Composable use cases expose a `within(uow, …)` step; the worker commits the tasks, the message and the voice job's `done` transition together.
- **Publish after commit.** Use cases return domain events; entry points publish them and enqueue jobs after the commit. A failed publish is logged, never raised: the database is the authority and the dashboard refetches on reconnect.
- **One message, one task.** A unique key on the Telegram chat and message id makes a duplicate update a no-op.
- **The extractor never fails a message.** On any provider error, timeout or invalid output it returns the heuristic result marked `degraded`, and `inbound_messages.extractor` records what ran.

### Voice job state machine

```
queued ──claim──► processing ──succeed──► done ──notify──► done + notified_at
   ▲                  │
   └── retry ─────────┤ (transient error, attempts < 5)
                      └──fail(kind)──► failed ──notify──► failed + notified_at
```

- **The database owns attempts.** `claim` is one guarded `UPDATE` that increments `attempts` (ceiling 5) and starts a 450-second lease. Every completion write is guarded by the claimed attempt, so a stale attempt cannot overwrite a newer one. The queue is disposable: nothing about a job's fate lives only in Redis.
- `done` and `failed` are absorbing. Each `failure_kind` maps to one user message (see `backend/AGENTS.md`).
- The recovery sweep (every 60 s on `worker-notify`) re-enqueues a `processing` row whose lease expired and a `queued` row not enqueued for 460 s, and re-sends a notification not delivered within 120 s. It never changes `attempts`.

### Celery rules

- Tasks are synchronous functions; each prefork child builds its own event loop, clients and composition root in `worker_process_init`. Nothing that holds a connection is created at import or inherited across the fork.
- `task_acks_late`, `task_reject_on_worker_lost`, prefetch 1, no result backend, visibility timeout 3600 s.
- Never call `self.retry`. A use case re-enqueues a transient failure through `JobQueue` with a short `countdown` (2, 4, 8, 16 s; any delay is capped at 600 s, far below the visibility timeout); a known error ends the task normally so the message is acknowledged.
- Time limits are per task: transcription 420 s soft, 450 s hard, inside a cooperative 400 s attempt budget; notification and sweep 60 s soft, 90 s hard.

### Providers and visible markers

- `TRANSCRIPTION_PROVIDER`: `openai` (the default in `.env.example` and in Compose), `whisper_server` (the local whisper.cpp server; Compose profile `whisper`), `fake` (tests).
- `TASK_EXTRACTOR`: `claude` (Claude Haiku, `ANTHROPIC_MODEL=claude-haiku-5-5`; the default in `.env.example`) or `heuristic` (Compose's fallback when the variable is empty).
- A missing key never stops a process. `openai` without a key answers every voice message with the "transcription is unavailable" reply; `claude` without a key runs the heuristic marked `degraded`, and the bot's reply adds one line that the model was unavailable.
- What ran is always visible: `/start` names the provider and the extractor, each worker logs them at start, and `voice_jobs.provider` and `inbound_messages.extractor` record them per message.

## Python

- Type hints on every function. The type checker runs in strict mode; `Any` needs a comment that says why.
- Ruff lints and formats.
- Pydantic models validate every boundary; settings come from environment variables through a settings model per entry point.
- Interfaces (ports) are `typing.Protocol` classes in the domain layer. Adapters implement them in the infrastructure layer. One composition root per entry point wires adapters to ports.
- I/O is asynchronous.
- Tests use pytest.

## TypeScript

- TypeScript runs in strict mode.
- Zod validates data that enters the dashboard at runtime. The types and Zod schemas in `frontend/src/api/generated/` are generated from `backend/openapi.json` by `yarn api:schema`; never edit them by hand.
- Type-only imports use `import type`.
- Functions are `const` arrow functions with explicit types where inference does not give them.
- Event handlers start with `handle`: `handleClick`, `handleKeyDown`.

### React

- Style with Tailwind classes. Route conditional classes through `cn()`.
- Component prop types are named `OwnProps`; page prop types are named `PageProps`.
- Use native interactive elements (`button`, `a`). A custom interactive element gets a role, `tabIndex={0}` and keyboard handlers beside pointer handlers; an icon-only control gets an `aria-label`.

## Configuration and secrets

- Configuration comes from environment variables and is validated at startup. `.env.example` lists every variable with a placeholder. Real values live only in an untracked `.env`.
- `.env.example` defaults to full functionality: OpenAI transcription and Claude Haiku extraction. Each key is optional; without it the behaviour degrades visibly as described above.
- Never commit tokens, keys or real chat content. Logs carry reason codes, not message text or secrets.
- A live run uses separate test credentials, and the README says which integrations were verified live.

## Module documentation

- Discovery: before working in a directory that has its own `AGENTS.md`, read it.
- Maintenance: a change that alters a module's responsibilities, entry points or constraints updates that module's `AGENTS.md` in the same commit.
- A change that moves a boundary shown in `docs/architecture/` updates that diagram in the same commit; `docs/architecture/README.md` names the revision the diagrams describe.

## Tests and checks

- A behaviour change comes with a test.
- A fixed defect comes with a regression test that fails without the fix.
- Integration tests run against real PostgreSQL and Redis in containers; only Telegram and the transcription and extraction providers are replaced by test doubles. Each test session isolates itself on the shared stack (its own database and Redis namespace).
- `yarn verify` never calls a paid provider.
- Check behaviour on the dev server. Use a production build to catch type and lint errors.
- The Docker Compose stack is the delivered artifact: run it once on the final commit, not for every check.

## Commits

- One commit per atomic stage that can be reverted on its own.
- The message says what changed, why, how, and any deviation from the design.
- Commit messages carry no tool attribution: no `Co-Authored-By` trailer and no "Generated with" line.

## How this repository is built

The author works with two coding agents, Claude Code and Codex, under a design, design review, implementation and code review cycle. Each design is committed in `docs/plans/` and opens with a status line. Working diaries and coordination records stay local and are not part of the repository. Contributors do not need any of this tooling: every command above runs with `uv`, `yarn` and `docker compose`.
