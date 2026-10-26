# AI Task Bot — umbrella design: decomposition and shared constraints

> **STATUS 2026-10-24: ACTIVE.** Umbrella design, round 4 with amendment 1 (section 12: S1's departures from this text, the integration-test isolation rule R-ISO, the delivered stack's database credentials, the S7 lint question) and amendment 2 (section 13: S2's departures X1–X13).

This document decides how the whole assessment is split into stages and which constraints every stage shares. It does not design the inside of a stage: each stage becomes its own workflow with its own design, review and acceptance. Product questions inside a stage are listed under that stage as open questions and are answered there.

## 1. What is being built

A task-management application with two entry points for the user:

- A Telegram bot. A text message creates tasks at once (R01, R02). A voice message is transcribed by a background worker and then creates tasks (R03). The bot answers quickly at every step, including transcription failures (R12).
- A web dashboard. It shows the user's tasks, lets the user move each task between `Pending`, `In Progress` and `Completed` (R09), and shows new tasks and status changes without a page reload (R08). It is responsive (R06) and uses one view, a list or a Kanban board (R07).

PostgreSQL stores users, inbound messages and tasks (R04). Redis carries the asynchronous work: transcription jobs and notification jobs (R05). Docker Compose runs the whole application: web frontend, backend API, PostgreSQL, Redis and the workers (R10, R11).

One addition beyond the assignment, decided by the repository owner (D-10): the text of a message, typed or dictated, is decomposed into one or more tasks, each with a title and a description, by a small language model. Without an API key a deterministic heuristic does the same job with one task per message, so the application is complete without any external account.

Evaluation criteria the design optimises for: clean modular code; effective use of Redis queues; proper error handling in voice processing; speed of transcription feedback in the bot.

## 2. Decisions taken at the umbrella level

Every item below was decided by the repository owner on 2026-10-23 and binds every stage. The owner's answer is quoted where it was given in words.

| # | Decision | Owner's answer |
|---|---|---|
| D-L | Languages: the bot is Python, the dashboard is TypeScript; the assessment shows both. | «Я предлагаю бота писать на Python, нужно показать умения и фронт на typescript и python» |
| D-01 | Persistence: SQLAlchemy 2 (async, asyncpg) + Alembic migrations. Integer primary keys. | «SQLAlchemy 2 async + Alembic» (recommended option) |
| D-02 | Queue: Celery 5.6 with the Redis broker behind a `JobQueue` port; two queues (`transcription`, `notification`), late acknowledgement, prefetch 1, no result backend. Two earlier choices were withdrawn after verification: arq (maintenance-only) and taskiq (its one-worker-several-streams mode is being deprecated, it has no delayed delivery for Redis Streams and an open receive-latency issue). | «Делаем на Celery тогда, это классика, пусть будет. Переписывай, только очень внимательно» |
| D-03 | Python 3.14, pinned (`requires-python = ">=3.14,<3.15"`, `.python-version`). Python 3.15 (released 2026-10-09) is blocked by one dependency: aiogram declares `requires-python <3.15`. Revisit when an aiogram release lifts that cap. | «3.14» |
| D-04 | API: FastAPI. Bot, worker and API are Python processes on one domain core. Dashboard: a simple React + TypeScript application, not Next.js. Every API request carries a token; the React app sends it. | «Да, API на fast api, фронт может быть простеньким react приложением, но API с защитой по токену, и react отправляет запросы с этой защитой» |
| D-05 | Personal tokens. The bot issues each Telegram user a personal dashboard token; the API and the real-time channel accept only a valid token; a user sees and changes only their own tasks. | «токен — личный» |
| D-06 | Layout: one Python package with three entry points (bot, worker, api) and one container image; the dashboard is a separate Vite project. Layers are enforced by import-linter. | «Да, один пакет» |
| D-07 | Real-time transport: Server-Sent Events from the API, fed by Redis pub/sub. The token is exchanged for a one-time short-lived ticket that goes in the SSE URL; the dashboard owns the reconnect loop (3.6). | «SSE» |
| D-08 | Delivery shape: a separate `web` container (nginx) serves the built dashboard and proxies `/api` to the API, so the browser talks to one origin. On every (re)connect the dashboard refetches the task list once; no event replay. | «B. Отдельный web-контейнер nginx» |
| D-09 | Two transcription adapters behind one `Transcriber` port, switched by `TRANSCRIPTION_PROVIDER` in `.env`: `openai` (the Docker stack's default; a reviewer puts a key in `.env`) and `whisper_server` (HTTP to a whisper.cpp `whisper-server`: in development the owner's Homebrew install on the Mac with the large-v3-turbo model on Metal, never inside Docker; for a reviewer without a key, an optional Compose profile starts the whisper.cpp server image with a small model). OpenAI gets one live check that it works; every other transcription test runs on the local server or the `fake`. | «Давай все таки сделаем адаптер к OpenAI и к локальному whisper. Локальный whisper будет работать не в докер, а на Metal мака, а в .env можно сделать переключение напрямую на OpenAI»; «Проверку к OpenAI сделаем, что нормально работаем, а остальное будет тестить на локальной моделе»; «Один адаптер, провайдер выбирается в .env … При разработке адаптер ходит в наш whisper-server … Ревьюер подставляет ключ OpenAI или включает Whisper-контейнер с маленькой моделью» |
| D-10 | Task extraction: a `TaskExtractor` port turns the text of a message into a list of tasks (title, description). Adapters: `claude` (Claude Haiku, `claude-haiku-5-5`, structured output validated against a schema) and `heuristic` (no key; one task: the text on one line, cut to 120 characters, is the title, the whole text is the description; amended by S2-D6, section 13 X13). Applies to voice and text alike; when the API fails or is not configured the heuristic runs, so no message is lost. | «кто-то же должен это разложить на задачи, тему, описание и так далее?», then «И голос, и текст» |

Settled by the owner's own prior work rather than by a question: Telegram library aiogram 3 with long polling (no public URL is needed on a reviewer's machine); settings through a pydantic-settings model; a composition root per entry point.

Settled by the repository guide (`AGENTS.md`): uv and ruff for Python, strict type checking (mypy `--strict`), `typing.Protocol` ports in the domain layer, async I/O, pytest; yarn, strict TypeScript, Zod, Tailwind, `cn()` for the dashboard; integration tests against real PostgreSQL and Redis; Telegram, transcription and the extraction model replaced by test doubles; one Compose run on the final commit.

## 3. Architecture

### 3.1 Processes and containers

```
            Telegram Bot API (long polling; file download)              browser
                 ▲                      ▲                                  ▲
                 │ updates, replies     │ getFile, editMessage            │ http :8080, one origin
        ┌────────┴────────┐    ┌────────┴──────────────┐          ┌───────┴────────┐
        │  bot (Python)   │    │ worker-transcribe      │          │  web (nginx)   │ static React build
        │  aiogram 3      │    │  celery -Q transcription│         │  /api → api    │ SSE location unbuffered
        │  TaskExtractor  │    │  Transcriber, TaskExtractor│      └───────┬────────┘
        └──┬─────┬─────┬──┘    │ worker-notify           │                 │ REST (bearer) + SSE (ticket)
   users,  │     │     │       │  celery -Q notification -B│                ▼
   msgs,   │     │     │ send_task └──┬─────┬─────┬──────┘         ┌─────────────────┐
   tasks,  │     │     └────────► │     │     │ events              │  api (FastAPI)  │
   voice   │     │ events  ┌──────┴─────┴─────┴──┐         ┌───────┤  REST, SSE,     │
   jobs    │     └────────►│  redis               │◄────────┘       │  tickets        │
           │               │  queues (2), pub/sub,│ subscribe,      └────────┬────────┘
           │               │  tickets, locks      │ tickets                  │ tasks, users
           ▼               └──────────────────────┘                          │
        ┌─────────────────────────────────────────────────────────────────────┴──┐
        │  postgres: users, inbound_messages, tasks, voice_jobs   ◄── migrate    │
        └────────────────────────────────────────────────────────────────────────┘
                                   Anthropic API (optional, bot and worker-transcribe) ──► Claude Haiku
                                   OpenAI API (worker-transcribe, TRANSCRIPTION_PROVIDER=openai) ──► transcription
                                   whisper-server /inference (TRANSCRIPTION_PROVIDER=whisper_server):
                                     dev: Homebrew whisper.cpp on the owner's Mac, Metal, `yarn dev:whisper`
                                     Compose profile `whisper`: ghcr.io/ggml-org/whisper.cpp:main, small model, CPU
```

Eight Compose services: `postgres` (image `postgres:18-alpine`), `redis` (image `redis:8`), `migrate` (one-shot `alembic upgrade head`, must end `exited (0)`; the Python services wait on `service_completed_successfully`), `api`, `bot`, `worker-transcribe`, `worker-notify` (the five Python services are five commands on one `backend` image), `web` (nginx image with the built dashboard). A ninth service, `whisper` (image `ghcr.io/ggml-org/whisper.cpp:main`, `whisper-server --host 0.0.0.0 --convert -l auto` on a small ggml model downloaded once into a named volume), sits behind the Compose profile `whisper` and starts only when `COMPOSE_PROFILES=whisper` is set in `.env`; the stack's acceptance counts it only in that mode. Health checks: `postgres` by `pg_isready`, `redis` by `redis-cli ping`, `api` by `GET /health` (public, no auth), the two workers by `CMD-SHELL celery -A taskbot.entrypoints.worker inspect ping -d $$WORKER_ROLE@$$HOSTNAME --timeout 5` (each worker has an explicit node name `-n $WORKER_ROLE@%h`; the main worker process answers even while a child transcribes, so configuration errors are caught by the main-process pre-flight of 3.6, not by this check), the bot by a heartbeat key in Redis (`heartbeat:bot`, 30 s TTL, refreshed every 10 s), `web` by `wget -qO- http://127.0.0.1/` (busybox in the nginx image). The `api` and bot probes are `python -c` one-liners from the runtime image (no curl in `python:3.14-slim`). "Every service healthy" in S8 means: seven services `healthy`, `migrate` `exited (0)`.

Two queues, two worker services, one Celery application. Both services run the same command, `python -m taskbot.entrypoints.worker`, and differ by one variable, `WORKER_ROLE`: `transcribe` execs `celery -A taskbot.entrypoints.worker worker -Q transcription -n transcribe@%h --pool prefork --concurrency 1` (one job at a time: a single whisper-server handles one request at a time well, and one upload at a time is enough for OpenAI at assessment scale); `notify` execs `-Q notification -n notify@%h --concurrency 2 -B -s /tmp/celerybeat-schedule` (embedded beat schedules the recovery sweep every 60 s on the `notification` queue with `expires=60`, so sweeps do not pile up while the worker is down; one replica per service is an assumption of section 8, so the embedded scheduler is enough). The same variable tells the per-child composition root which adapters to build. The Celery `app` object (configuration and routes, no tasks) lives in `infrastructure/celery/app.py` so that the bot and the API can `send_task` through the `JobQueue` adapter; `entrypoints/worker/__init__.py` imports it, registers the tasks and signals and exports it as `app` for `-A`. Settings: `task_acks_late = True`, `task_reject_on_worker_lost = True`, `worker_prefetch_multiplier = 1`, `task_ignore_result = True` (no result backend), JSON serializer, `broker_transport_options = {"visibility_timeout": 3600}`, `task_routes` by task name, `worker_hijack_root_logger = False`; time limits are set per task (3.6), not globally. Every delay the application schedules is a `countdown` of at most a few minutes (the retry schedule stays under 20 s; Telegram's `retry_after` is passed through as is), far below the visibility timeout, so a delayed message is never redelivered early. The bot runs as one instance per token (long polling); it takes a Redis lock (`SET bot:poll NX EX 30`, renewed every 10 s) and exits with a clear message when the lock is held, so a forgotten dev bot cannot silently fight the Compose bot.

Celery tasks are synchronous functions (Celery 5.6 has no `async def` tasks). The main worker process runs a pre-flight in the `worker_init` signal (owned by S5): it validates the shape of the role's settings — an unknown `TRANSCRIPTION_PROVIDER` or `TASK_EXTRACTOR` name, a malformed `WHISPER_SERVER_URL`, a missing `TELEGRAM_BOT_TOKEN` — and exits with the variable's name when they are wrong. **A missing credential never stops a worker:** with `openai` and an empty key the transcribe role starts, logs one warning, and builds an adapter that answers every job with `provider_unavailable`, so the user gets that reply through the normal notification path; with `claude` and an empty key the extractor is the heuristic, marked `degraded`. The notify role validates only what it uses (bot token, broker, database) and never a transcription or extraction setting. Each prefork child then creates its own event loop, database engine, Redis clients, transcriber and extractor clients and composition root in the `worker_process_init` signal; a task runs its use case with `loop.run_until_complete(...)`. Nothing that holds a connection is created at import time or inherited across the fork, and nothing in the child initializer downloads, loads or connects: Celery kills a child that is not up within `worker_proc_alive_timeout` (4 s by default), so the child builds clients lazily and the heavy work stays in the providers.

### 3.2 Repository layout

```
backend/
  pyproject.toml            uv project (Python 3.14 pinned); ruff (lint, format, complexity rules), mypy --strict, import-linter, pytest
  alembic/                  migrations (env.py takes a PostgreSQL advisory lock around upgrade)
  src/taskbot/
    domain/                 entities, value objects, state machines, domain events, ports (typing.Protocol)
    application/            use cases; depend on domain ports only
    infrastructure/         adapters: sqlalchemy/, redis/, celery/ (app, job_queue), telegram/, transcription/ (openai, whisper_server, fake), extraction/ (claude, heuristic), settings/
    entrypoints/            bot/, worker/ (tasks, signals, role launcher), api/, seed/ — packages, one composition root each
  tests/fakes/              test-only doubles, among them the recording extractor fake (section 13 X10)
  tests/
    unit/                   use cases and state machines with in-memory fakes
    integration/            adapters against real PostgreSQL and Redis (opt-in; see section 4)
frontend/
  package.json              Vite + React 19 + TypeScript 6; ESLint 10 flat config with eslint-plugin-boundaries; prettier; vitest
  src/{app,features,components,api,lib}
docker/                     backend.Dockerfile (python:3.14-slim; no model runtime, no ffmpeg), web.Dockerfile, nginx.conf
docker-compose.yml
package.json                root; `yarn verify`, `yarn infra:*`, `yarn db:migrate`, `yarn dev:*`
.nvmrc                      node 26
docs/plans/                 this design and one design per stage
```

### 3.3 Dependency direction (checked by tools)

```
entrypoints ──► application ──► domain
     │                              ▲
     └──────► infrastructure ───────┘
```

- `domain` imports nothing from the other layers and no third-party SDK.
- `application` imports `domain` only.
- `infrastructure` imports `domain` (to implement ports) and the SDK it wraps; never `application`.
- `entrypoints` import everything and are the only place where adapters are bound to ports.

Python, exact import-linter contracts in `pyproject.toml` (with `include_external_packages = true`):

```toml
[[tool.importlinter.contracts]]
name = "layers"
type = "layers"
layers = ["taskbot.entrypoints", "taskbot.application | taskbot.infrastructure", "taskbot.domain"]

[[tool.importlinter.contracts]]
name = "no sdk in the core"
type = "forbidden"
source_modules = ["taskbot.domain", "taskbot.application"]
forbidden_modules = ["aiogram", "anthropic", "openai", "celery", "kombu", "redis", "sqlalchemy", "asyncpg", "alembic", "fastapi", "starlette", "httpx", "httpx2"]
```

The `|` makes `application` and `infrastructure` siblings: neither may import the other.

Dashboard, `eslint-plugin-boundaries` element types and allowed edges: `app → features, components, lib`; `features → components, api, lib`; `components → lib`; `api → lib`; `lib → nothing`. Everything else is an error.

### 3.4 Ports (domain), adapters (infrastructure), owners

| Port | Purpose | Adapter(s) | Built in |
|---|---|---|---|
| `UserRepository`, `InboundMessageRepository`, `TaskRepository`, `VoiceJobRepository` | persistence | SQLAlchemy async; in-memory fakes for unit tests | S2 |
| `UnitOfWork` | one transaction per use case; collects domain events and returns them to the caller on commit | SQLAlchemy session; fake | S2 |
| `TokenGenerator`, `TokenHasher`, `Clock` | random tokens, SHA-256 hashing, time | `secrets`, `hashlib`, `datetime`; fixed fakes | S2 |
| `TaskExtractor` | `extract(text) → Extraction(tasks, extractor, degraded)` (1..10 tasks of `title ≤ 120 chars`, `description ≤ 2000 chars`, in message order; `extractor` = the adapter that produced it, `degraded` = whether a fallback ran); `ExtractedTasks(tasks)` is only the model-facing schema (section 13 X1); never raises for a provider failure: it returns the heuristic result marked `degraded` | `heuristic` (pure Python, no language rules: the text on one line, cut to 120 characters, is the title, the whole text is the description, one task); recording fake in `tests/fakes/` | S2 |
| | | `claude` (Anthropic SDK, `AsyncAnthropic(max_retries=0, timeout=<deadline>)`, the deadline a constructor argument set by each composition root — 8 s in the bot, 30 s in the worker; `messages.parse` with the Pydantic `ExtractedTasks` schema as `output_format`, model `claude-haiku-5-5`, `output_config={"effort": "low"}`, `max_tokens` 4096; on any `APIError`, timeout, `refusal` or `max_tokens` stop reason, `None` parsed output or a schema/cap validation failure — `pydantic.ValidationError`, which `messages.parse` raises itself and which is not an `APIError` — it falls back to `heuristic`) | S9 |
| `JobQueue` | `enqueue(job, *, delay_seconds=0)` of JSON payloads (Pydantic models) to the `transcription` or `notification` queue; at-least-once delivery; acknowledgement after the handler returns | Celery `send_task(name, kwargs=payload, queue=, countdown=delay_seconds)`; in-memory fake | S3 |
| `EventPublisher` | publish `TaskCreated`, `TaskUpdated`, `AuthRevoked` to `events:user:<user_id>` | Redis pub/sub; recording fake | S3 |
| `TicketStore` | one-time 30 s SSE tickets | Redis `SET … EX` + `GETDEL`; fake | S3 |
| `UserNotifier` | send a reply, edit a message by id | aiogram Bot API, with the Telegram error mapping of 3.6; recording fake | S4 |
| `VoiceFileSource` | fetch voice bytes by Telegram file id | aiogram `get_file` + download; fake returning fixture bytes | S4 |
| `Transcriber` | audio bytes → `Transcript` or a typed `TranscriptionError`; takes a deadline | `openai` (`AsyncOpenAI(max_retries=0, timeout=30)`, `audio.transcriptions.create(file=("voice.ogg", bytes), model=OPENAI_TRANSCRIPTION_MODEL)`; one attempt is one call; the SDK's typed errors mapped to the taxonomy of 3.6) — the Docker stack's provider | S5 |
| | | `whisper_server` (`httpx.AsyncClient` multipart `POST {WHISPER_SERVER_URL}/inference` with `file=("voice.ogg", bytes)`, `response_format=json`, `temperature=0`; a 300 s request timeout; the server converts OGG/Opus itself because it runs with `--convert` and ffmpeg beside it; language auto-detected by the server's `-l auto`) — the same adapter talks to the owner's Homebrew server on the Mac (development and every transcription test) and to the `whisper` Compose profile; `fake` (deterministic text marked `[demo transcript]`) | S5 |

Use cases (application) and the stage that builds them: `RegisterOrGetUser`, `CreateTasksFromMessage` (text or transcript → `TaskExtractor` → one `inbound_messages` row and its tasks), `ChangeTaskStatus`, `ListTasks`, `IssueDashboardToken`, `AuthenticateToken` (S2); `IssueRealtimeTicket`, `ConsumeRealtimeTicket` (S3); `AcceptVoiceMessage` (S4); `TranscribeVoiceJob`, `NotifyUser`, `RecoverStuckVoiceJobs` (S5). Every use case receives its ports in the constructor, calls the extractor **before** opening its `UnitOfWork` (a model call never holds a database transaction), runs the writes inside one `UnitOfWork`, and returns the domain events it produced; the entry point publishes them and enqueues jobs **after** the commit. **The transaction belongs to the outermost caller:** a use case that others compose (`RegisterOrGetUser`, `CreateTasksFromMessage`) exposes its writes as a `within(uow, …)` step over a caller-supplied unit of work, plus a thin standalone form that opens its own. The bot and the seed use the standalone forms; `TranscribeVoiceJob` extracts first, then opens one unit of work and runs `CreateTasksFromMessage.within(uow, …)` and the job's `done` transition inside it, so tasks, message and job state commit together or not at all (an S2 acceptance test). A failed publish is logged, never raised: the dashboard refetches on reconnect, the database is already right.

Every external input is a Pydantic model validated where it enters: Telegram updates (aiogram types re-validated into application commands), HTTP bodies, queue payloads (JSON, validated on dequeue), environment, and the model's output (the schema plus length caps; text inside a message is data, never an instruction to the application).

### 3.5 Data model (minimum fixed here; a stage may add a field with a reason)

Amended by section 13 (X4, X6, X7, X9): the S2 design's section 3 is the current statement of the claim, `exhaust`, the clock and the notification columns, including `notify_not_before`.

- `users`: `id` (int), `telegram_user_id` (bigint, unique), display name, `dashboard_token_hash` (unique, nullable), `token_version` (int, incremented by every `/dashboard`), timestamps.
- `inbound_messages`: `id`, `user_id`, `telegram_chat_id` (bigint), `telegram_message_id` (bigint), `kind` (`text` | `voice`), `text` (the typed text or the transcript, NOT NULL: the row is inserted only after extraction), `extractor` (`heuristic` | `claude`, NOT NULL), `degraded` (bool), `created_at`. Unique on (`telegram_chat_id`, `telegram_message_id`).
- `tasks`: `id` (int), `user_id`, `inbound_message_id`, `position` (int, order inside the message), `title` (text), `description` (text, may be empty), `status` (`pending` | `in_progress` | `completed`), timestamps. Unique on (`inbound_message_id`, `position`).
- `voice_jobs`: `id`, `user_id`, `telegram_chat_id`, `telegram_message_id` (unique pair), `inbound_message_id` (nullable until the transcript is stored), `ack_message_id` (nullable), `file_id`, `file_unique_id`, `duration_seconds`, `file_size` (nullable), `status`, `attempts` (int, owned by the row, ceiling 5), `claimed_at` (set by every successful claim; the attempt's lease starts here), `last_enqueued_at` (set by every enqueue for the row: bot, retry, sweep, notification), `failure_kind` (nullable enum, 3.6), `provider` (`fake` | `openai` | `whisper_server`, set by the worker), `notify_attempts` (int), `notified_at` (nullable), `notification_outcome` (`edited` | `replied` | `blocked` | `given_up`, nullable), timestamps.

**Invariant:** one Telegram message yields at most one `inbound_messages` row (hence one set of tasks) and at most one voice job. Inserts use `INSERT … ON CONFLICT DO NOTHING RETURNING` and a conflict means "continue from the existing row": a duplicate text update sends no second reply and no second event; a duplicate voice update resumes from the stored job state. Extraction runs before the insert, so a duplicate costs one extra model call and never a second set of tasks. `RegisterOrGetUser` is one `INSERT … ON CONFLICT (telegram_user_id) DO UPDATE … RETURNING`, so two first messages in one burst cannot lose one.

**VoiceJob state machine** (domain entity with guarded transitions, unit-tested in S2):

```
queued ──claim──► processing ──succeed──► done ──notify──► done+notified_at
   ▲                  │  │
   │   retry (attempts < MAX)  └──fail(kind)──► failed ──notify──► failed+notified_at
   └──────────────────┘
```

`done` and `failed` are absorbing. **An attempt owns the row for a lease** equal to the hard time limit (450 s, 3.6). `claim` is one atomic statement: `UPDATE … SET status='processing', attempts=attempts+1, claimed_at=now() WHERE id=? AND attempts < 5 AND (status='queued' OR (status='processing' AND claimed_at < now() − lease)) RETURNING attempts`; the returned number is the attempt the worker is running. A claim that returns no row means one of three things, decided by one follow-up read: the row is terminal (the delivery is acknowledged and dropped); the lease is live (another attempt is running: the delivery re-enqueues itself with `countdown` = the remaining lease and returns); or `attempts` has reached the ceiling (the delivery runs `exhaust`: `UPDATE … SET status='failed', failure_kind='stuck' WHERE id=? AND status IN ('queued','processing') AND attempts >= 5`, then enqueues the notification). So a job whose child dies five times in a row ends `failed(stuck)` on its sixth delivery and the user is told, whatever killed the child. Every completion write is guarded by the attempt it belongs to — `WHERE id=? AND status='processing' AND attempts=<claimed attempt>` — so a stale attempt that outlived its lease cannot overwrite a newer one. The queue is disposable: nothing about a job's fate lives only in Redis.

### 3.6 Flows fixed by the umbrella

Amended by section 13 (X3, X5, X8, X9): a retry first writes `requeue`, the sweep re-enqueues an expired `processing` row only when `last_enqueued_at` is older than 460 s, and the notification flow counts at its start and honours `notify_not_before`.

**Text task.** Update → validate → `sendChatAction typing` → `CreateTasksFromMessage`: `TaskExtractor.extract(text)` (bot path deadline 8 s, then heuristic) → one unit of work: `RegisterOrGetUser`, insert `inbound_messages` (conflict ⇒ duplicate, stop), insert the tasks → commit → publish one `TaskCreated` per task → reply listing the created titles (one line per task); when `degraded` is set the reply adds one line that the model was unavailable and the text was saved as one task. Target: reply within 3 s with the model, under 1 s with the heuristic. Accepted crash window: a crash between the commit and the reply loses the reply (Telegram's redelivery meets the unique key and sends nothing); the tasks exist and the dashboard shows them — the README states it, S4 tests it. Messages the bot refuses at once (empty text, oversize voice) get a reply and no row.

**Voice task and its feedback timeline.**

```
t0   voice update arrives at the bot (duration is always present; file_size may be absent)
     reject at once, with a reason, when duration > 120 s or file_size > 10 MB
     insert voice_job(queued, attempts=0); commit   (conflict ⇒ duplicate update: resume — send the ack
     send "Transcribing your voice message…"  ← target: under 1 s after t0        if ack_message_id is NULL, else stop)
     store ack_message_id; commit
     enqueue transcription job {voice_job_id}, set last_enqueued_at; on enqueue failure the sweep (below) repairs it
t1   worker-transcribe: claim (attempts := n) → download → size check on real bytes → Transcriber
     (openai: one call, 30 s timeout; whisper_server: one /inference request, 300 s timeout)
t2   worker-transcribe: TaskExtractor.extract(transcript) (deadline 30 s, then heuristic)
     one unit of work: insert inbound_messages(kind=voice, text, extractor), tasks, voice_job → done; commit
     publish TaskCreated per task; enqueue notification job {voice_job_id, outcome=done}
t3   worker-notify: edit the ack message to "Task created: <title>" or "N tasks created: …"
     (plus one line when degraded); voice_job.notified_at := now, notification_outcome := edited | replied
```

Time budget of one transcription attempt: download ≤ 30 s, provider call ≤ 300 s, extraction ≤ 30 s, writes; the transcription task is declared with `soft_time_limit=420`, `time_limit=450`; notification and sweep tasks with `soft_time_limit=60`, `time_limit=90`. `SoftTimeLimitExceeded` inside the transcription task is mapped to `transcription_timeout`.

Failure taxonomy (R12). `failure_kind` values, each with its user message and action, decided by the job function from the typed error and the row's `attempts`:

| `failure_kind` | Raised by | Retry? | User sees |
|---|---|---|---|
| `too_large` | the real byte size after download exceeds the limit (the duration and the declared size are refused by the bot at t0 with a reply and no row, so `too_long` never reaches the worker) | no | the reason and what to do (shorter message) |
| `unsupported_audio` | provider or server 4xx about the file (400/413/415, not 401/403/408/429), including whisper-server's conversion failure | no | "could not read this audio, please resend" |
| `empty_transcript` | the provider or server returns no text | no | "nothing recognisable, please speak closer or type it" |
| `transcription_timeout` | the provider request hit its timeout, or the task's soft time limit fired | no | "the recording took too long to process, please send a shorter one" |
| `provider_unavailable` | `openai`: 401/403, quota or billing 429, `TRANSCRIPTION_PROVIDER=openai` with an empty key at run time | no | "Transcription is unavailable right now; your message was not lost, please type it" |
| `transient_exhausted` | `openai` rate-limit 429, 5xx, timeout or connection errors; whisper-server connection refused or 5xx (the server is not started or is loading); Telegram download errors; Redis or database connection errors — after the last allowed attempt | retries first | first transient failure edits the ack once to "Taking longer than usual, retrying…"; after the last attempt the final edit says it failed and asks to resend |
| `internal` | any unmapped exception | no | the generic failure message |
| `stuck` | the recovery sweep gave up | no | the generic failure message |

Extraction never fails a job: a model error, timeout or refusal degrades to the heuristic and `inbound_messages.extractor` records it.

Retry mechanics: the job function reads `attempts` from the claim; on a transient error with `attempts < 5` the use case returns `RetryLater(delay = 2 · 2^(attempts−1) s)` (2, 4, 8, 16 s) and the entry point enqueues the same job again through `JobQueue` with that delay and returns normally, so the current message is acknowledged; with `attempts = 5` it marks `failed(transient_exhausted)` and enqueues the notification. The Celery task never raises for a known error and Celery's own retry counter is not used (`self.retry` is never called); an unknown exception is caught, recorded as `internal` and acknowledged too, and only a crash of the child leaves a message unacknowledged (`task_reject_on_worker_lost` returns it to the queue at once). That redelivery meets the lease of 3.5: while the dead attempt's lease is live the delivery waits it out through `countdown`, then claims again as attempt n+1; after the fifth crash the claim refuses and `exhaust` ends the job — crash redelivery and known errors share one ceiling. The OpenAI client is built with `max_retries=0` and a 30 s timeout so one attempt is one call and the attempt count stays in the database. The provider request runs under its own timeout; the task time limits above are the backstop: a hard kill leaves the row `processing` and the sweep repairs it. `docker compose restart worker-transcribe` burns at most one attempt: a warm shutdown (SIGTERM) finishes the running job within the service's `stop_grace_period` (480 s, set in S8), and an unacknowledged message is redelivered after the visibility timeout with an idempotent claim. Redis clients are built with socket and connect timeouts and `health_check_interval`, so a stalled connection is detected rather than blocking a worker forever.

**Recovery sweep** (`RecoverStuckVoiceJobs`, a beat task routed to the `notification` queue every 60 s, also run once from `worker-notify`'s `worker_ready` hook) repairs three states with three different clocks: a `processing` row whose lease has expired (`claimed_at` older than 460 s — the attempt is dead, not slow) → re-enqueue; a `queued` row whose `last_enqueued_at` is older than 460 s → re-enqueue (its message may be lost, or it may simply be waiting behind the single slot — the sweep cannot tell, so the policy is **bounded duplication**: at most one extra copy per row per 460 s of residence, stamped on `last_enqueued_at`, and under the claim rule of 3.5 a copy that arrives after the row finished is dropped, while one that arrives during a live lease waits the lease out and is then dropped too); a `done` or `failed` row with `notified_at IS NULL`, `notify_attempts < 5` and `last_enqueued_at` older than 120 s → enqueue the notification again. Exhaustion is the claim's, not the sweep's: the sweep never changes `attempts`. The sweep is idempotent because every transition is guarded by the row's state.

**Notification edge cases** (`NotifyUser`, 3.4 `UserNotifier`): every attempt increments `notify_attempts`; "message is not modified" counts as success (`edited`); `retry_after` from Telegram becomes the retry delay; 403 (user blocked the bot) is a quiet success (`blocked`); "message to edit not found" or a null `ack_message_id` falls back to a new reply to the original message (`replied`); an unmapped error retries through the schedule, and the fifth failure sets `notified_at` with `given_up` and a logged reason, so no row is swept forever; the notification payload carries the outcome, so a late duplicate cannot overwrite a failure message with a success one.

**Dashboard access.** `/dashboard` works only in a private chat (elsewhere a one-line refusal). It runs `IssueDashboardToken` (new random token, SHA-256 hash stored, `token_version` incremented, old token invalid) → publishes `AuthRevoked` so open SSE streams of that user close at once → replies with `<WEB_BASE_URL>/#token=<token>`. The dashboard reads the fragment once, removes it from the address bar (`history.replaceState`), keeps the token in local storage and sends `Authorization: Bearer <token>` on every request. A missing or wrong token is `401`; the dashboard then clears the token and shows "Send /dashboard to the bot for a new link". A task of another user is `404`. The README states that a new `/dashboard` signs out other devices.

**Real-time.** `POST /api/realtime/tickets` (bearer) → `IssueRealtimeTicket` → one-time ticket, 30 s, whose stored value carries `user_id` **and the `token_version` the bearer was validated against** → `GET /api/events?ticket=…` → `ConsumeRealtimeTicket` (`GETDEL`) → the API reads the user's current `token_version` from the database and answers `401` when it differs (a ticket issued before a `/dashboard` rotation cannot open a stream after it) → subscribes to `events:user:<user_id>` **before** sending headers, sends `: connected`, then events, then a comment heartbeat every 20 s; on every heartbeat it re-reads `token_version` and closes the stream when it changed, so a revocation whose `AuthRevoked` event was missed (a reconnecting Redis subscriber) still ends the stream within one heartbeat. The event is the fast path, the database is the authority. The dashboard owns the connection loop: on `error` it closes the `EventSource`, requests a new ticket, opens a new stream, refetches the task list after `open`, with exponential back-off and jitter capped at 30 s; a `401` on the ticket request stops the loop (token invalid). nginx's `/api/events` location sets `proxy_buffering off`, `proxy_http_version 1.1`, `proxy_set_header Connection ""` and a read timeout above the heartbeat interval; the API also sends `X-Accel-Buffering: no`.

**Event vocabulary** (one, used by Python and TypeScript): `task.created {task}`, `task.updated {task}`, `auth.revoked {}`, where `task` = `{id, inbound_message_id, position, title, description, status, created_at, updated_at}` — the same shape `GET /api/tasks` returns. The envelope and the `task` payload are Pydantic models that appear in the OpenAPI document (as the response model of `GET /api/events` plus `openapi_extra`), and the dashboard's TypeScript types **and** Zod schemas are generated from that document (section 4); nothing on the wire is written by hand twice.

**Demo modes are visible as such.** The fake transcriber returns `[demo transcript] …` and the ack edit says "(demo mode)"; `/start` names the transcription provider and the extractor in use; each worker logs them at start; `voice_jobs.provider` and `inbound_messages.extractor` record what actually ran. The Compose stack ships with `TRANSCRIPTION_PROVIDER=openai`: a reviewer puts an OpenAI key in `.env` and gets real transcription; a reviewer without a key sets two lines instead (`TRANSCRIPTION_PROVIDER=whisper_server`, `COMPOSE_PROFILES=whisper`) and gets a local whisper.cpp container with a small model on the CPU; with neither, the stack still starts, text tasks and the dashboard work, and a voice message gets the `provider_unavailable` reply. The README's first screen names both switches. The owner develops with `whisper_server` pointing at the Homebrew server on the Mac (`yarn dev:whisper` starts it, `yarn dev:worker transcribe` uses it); no model runtime is ever built into the backend image.

### 3.7 Configuration

One settings package with a base model and one sub-model per entry point (`BotSettings`, `WorkerSettings` shared by both worker roles, `ApiSettings`), so the API never requires the bot token and the bot never requires provider credentials; the extractor fields live in a shared mixin (S1) because the bot and the transcription worker both use them. Each entry point validates its own model at start and fails fast naming a variable that is malformed or structurally missing; the worker validates per role (3.1). Credentials are different: `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are optional in the model, and an empty one degrades behaviour instead of stopping a process — the transcriber factory builds an adapter whose every call ends `provider_unavailable` when the provider is `openai` and the key is empty, and the extractor factory builds the heuristic (marked `degraded`) when `claude` is requested without a key. Both cases are logged once at start and named by `/start`.

| Variable | Dev value (`yarn dev:*`) | Compose value | Required by |
|---|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | not variables: literal `taskbot` values in the shared development stack (S1 gate G1) | from `.env`, with placeholders in `.env.example` (section 12) | postgres, migrate |
| `DATABASE_URL` | `postgresql+asyncpg://taskbot:taskbot@localhost:5432/taskbot` | overridden in Compose to the `postgres` host | migrate, api, bot, workers |
| `REDIS_URL` | `redis://localhost:6379/0` (broker uses db 1: `CELERY_BROKER_URL`, derived when unset) | overridden to the `redis` host | api, bot, workers |
| `TELEGRAM_BOT_TOKEN` | the owner's test bot token | same | bot, worker-transcribe (file download), worker-notify (message edit) |
| `WORKER_ROLE` | `transcribe` / `notify` (set by `yarn dev:worker <role>`) | per service | both workers |
| `TRANSCRIPTION_PROVIDER` | `whisper_server` (`fake` in tests) | `openai` (or `whisper_server` with the profile) | worker-transcribe; the bot reads the name for `/start` |
| `OPENAI_API_KEY`, `OPENAI_TRANSCRIPTION_MODEL` | empty / `gpt-4o-mini-transcribe` (the current guide also names `gpt-transcribe`; S5 confirms the default against the live check) | the reviewer's key / same | worker-transcribe (only with `TRANSCRIPTION_PROVIDER=openai`) |
| `WHISPER_SERVER_URL` | `http://127.0.0.1:8178` (the Homebrew server started by `yarn dev:whisper`) | `http://whisper:8080` | worker-transcribe (only with `whisper_server`) |
| `WHISPER_DEV_MODEL_PATH` | the owner's ggml file (`ggml-large-v3-turbo-q5_0.bin` in the local models directory) | unused | `yarn dev:whisper` only |
| `WHISPER_MODEL`, `COMPOSE_PROFILES` | unused | `base` (downloaded once into the `whisper-models` volume by the image's `download-ggml-model.sh`), `whisper` to enable the service | the `whisper` service |
| `TASK_EXTRACTOR` | `heuristic` | `heuristic` | bot, worker-transcribe |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | empty / `claude-haiku-5-5` | same | bot, worker-transcribe (only with `TASK_EXTRACTOR=claude`) |
| `WEB_BASE_URL` | `http://localhost:5173` | `http://localhost:8080` | bot |
| `API_PORT`, `WEB_PORT` | `8000`, `5173` | `8000` (internal), `8080` | api, web |
| `LOG_LEVEL` | `INFO` | `INFO` | all |

Code constants, not variables: voice limits (120 s, 10 MB), retry schedule and attempt limits (5 transcription, 5 notification), the OpenAI call timeout (30 s), the whisper-server request timeout (300 s) and the per-task Celery time limits, extractor deadlines (8 s in the bot, 30 s in the worker), task caps (10 per message, 120-character titles), ticket TTL (30 s), heartbeat (20 s), sweep thresholds (460 s, 120 s), visibility timeout (3600 s). `.env.example` carries the dev values; `docker-compose.yml` overrides the host names. Published host ports for `postgres` and `redis` bind to `127.0.0.1`. `ffmpeg` is needed only beside a whisper-server started with `--convert` (present on the owner's Mac; the `whisper` service checks for it in S8); the backend image carries no ffmpeg and no model runtime. A reviewer without a Telegram token can run everything except the `bot` service; the README says so.

## 4. Shared constraints as checkable rules

| Rule | Tool / command | Where it runs |
|---|---|---|
| Layer direction (3.3) | `lint-imports`; `eslint-plugin-boundaries` | `yarn verify` |
| No SDK import in `domain` and `application` | import-linter `forbidden` contract | `yarn verify` |
| Strict types | `mypy --strict`; `tsc -b` with `strict` over the referenced projects | `yarn verify` |
| Lint, format, complexity | `ruff check` (rules incl. `C901`, `PLR0912`, `PLR0915`), `ruff format --check`; `eslint`, `prettier --check` | `yarn verify` |
| File size ≤ 700 lines (source files; tests, generated files and Alembic versions exempt) | a line-count check script in `yarn verify` | `yarn verify` |
| One schema across the language boundary | `backend` exports `backend/openapi.json` (`yarn api:schema`); `@hey-api/openapi-ts` generates `frontend/src/api/generated/{types.gen.ts, zod.gen.ts, index.ts}` from it; `yarn verify` fails when a generated file is stale | `yarn verify` |
| Tests | `pytest` (unit always; integration when infrastructure is up), `vitest` | `yarn verify` |
| Integration tests hit real PostgreSQL and Redis | `yarn infra:up` then `pytest -m integration`; the suite checks reachability first and fails with a message, it never skips silently; each session isolates itself on the shared stack (R-ISO, section 12) | `yarn verify` |
| Every boundary validated; early returns; names; one responsibility | review rule, with a test per boundary | code review |

`yarn verify` is the single verification command: the root `package.json` runs the backend checks through `uv run` and the frontend checks through the workspace, in that order, and stops at the first failure. It requires `yarn infra:up` first and says so when PostgreSQL or Redis is unreachable. Other root commands: `yarn infra:stop` (stops the shared stack, removes nothing), `yarn db:migrate` (`uv run alembic upgrade head`), `yarn api:schema`, `yarn dev:bot`, `yarn dev:api` (uvicorn with reload), `yarn dev:web` (Vite with a proxy to the API), `yarn dev:whisper` (`whisper-server -m $WHISPER_DEV_MODEL_PATH --host 127.0.0.1 --port 8178 --convert -l auto`; fails with a clear message when `whisper-server` or `ffmpeg` is not on PATH), `yarn dev:worker <role>` (sets `WORKER_ROLE` and runs the launcher, so `notify` includes `-B`), `yarn dev:seed` (creates a user and tasks for a Telegram user id through the standalone `CreateTasksFromMessage`, synthesising message ids, issues a dashboard token and prints it; from S3 on it also publishes the events, for dashboard work without the bot). The Compose stack is run once, on the final commit of S8.

Pinned toolchain (as amended by S1, section 12): Node 26 (`.nvmrc`, `engines.node >=26`), TypeScript 6, ESLint 10, Python 3.14, `postgres:18-alpine` and `redis:8` images, `python:3.14-slim` and `node:26-alpine` build images. Python pins that follow from the libraries: `celery>=5.6.3,<5.7` (5.7.0rc1 is the fallback named in section 9), `redis>=5,<6.5` (kombu's own cap), `openai>=3.37,<4` (the 2.x line ended at 2.54.0; S5 writes the adapter against 3.x; depends on `httpx2` like `anthropic`), `anthropic>=2.3,<3`, `httpx` (already a FastAPI test dependency; the `whisper_server` adapter uses it). No model runtime in the Python dependencies.

## 5. Stages

Each stage is one child workflow, sized for one designer session. Acceptance is observable on the dev servers; the Compose stack is verified only in S8. "Open questions" are decided by the stage designer with the owner after exploring that stage; they are not decided here. Stages S4, S5, S6 and S9 run in parallel (two at a time given machine load): each owns its own settings sub-model, its own package and its own module guide, and none edits `docker-compose.yml` or the Dockerfiles.

### S1 — Skeleton, tooling and local infrastructure

- Scope: `backend/` uv project (Python 3.14 pinned), ruff with the complexity rules, mypy strict, the two import-linter contracts of 3.3, pytest (`asyncio_mode = auto`, `integration` marker with the reachability check); `frontend/` Vite + React + TypeScript 5.x with eslint 9 + boundaries, prettier, vitest; root `package.json` with every command of section 4 (the `dev:*` and `api:schema` entries point at modules that later stages fill); `docker-compose.yml` with `postgres` and `redis` (health checks, `127.0.0.1` port binds); `docker/backend.Dockerfile` with a smoke `docker build` (multi-stage, `uv sync --frozen`, non-root, `python` as PID 1, an import check of the installed packages inside the image; no Compose service yet); the settings package of 3.7 with the base model and the extractor mixin; `.env.example`; the 700-line check; README setup section.
- Acceptance: a fresh clone runs `yarn install` (no engine or peer errors), `uv sync` (celery, openai, anthropic resolve on 3.14 with the pins of section 4), `yarn infra:up`, `yarn verify` green with placeholder tests; a deliberate `domain → infrastructure` import and a deliberate `openai` import in `domain` each fail `lint-imports` (shown in the stage diary); `docker build -f docker/backend.Dockerfile` succeeds; a bare `celery --version` and `python -c "import openai, anthropic"` succeed on 3.14 in the venv; `yarn dev:whisper` starts the owner's whisper-server and `curl -F file=@fixture.ogg -F response_format=json :8178/inference` returns text (recorded in the stage diary with the model and whether the server log reports Metal).
- Open questions: the Zod generator (orval, hey-api, or openapi-zod-client); Tailwind version; frontend test layout.

### S2 — Domain core and PostgreSQL persistence (after S1)

- Scope: entities, value objects and the `VoiceJob` state machine of 3.5 with the lease, the bounded claim, `exhaust` and the attempt-guarded completion; `token_version` on users; `ExtractedTasks` value object with its caps and the `extractor`/`degraded` fields; domain events; every port marked S2 in 3.4 plus the port definitions (Protocols) of all other ports; the `heuristic` extractor and the recording `fake`; use cases `RegisterOrGetUser`, `CreateTasksFromMessage` (standalone and `within(uow)` forms), `ChangeTaskStatus`, `ListTasks`, `IssueDashboardToken`, `AuthenticateToken`; SQLAlchemy models, Alembic initial migration (with the advisory lock), repositories with the `ON CONFLICT` rules; `yarn db:migrate`; `yarn dev:seed` (user, synthetic message ids, tasks, printed token).
- Acceptance: unit tests for every use case and every state-machine transition (allowed and refused); heuristic tests (one sentence, several sentences, a title longer than the cap, empty text refused); integration tests for every repository against real PostgreSQL; the invariant test inserts the same Telegram message twice for a text message and for a voice job and observes one row set and "continue from existing"; a concurrency test runs two first-contact `RegisterOrGetUser` calls with `asyncio.gather` and observes one user and two results; the extractor is called before the unit of work opens (recording fakes assert the order); a composition test runs `CreateTasksFromMessage.within` and a `VoiceJob` `done` transition in one unit of work and observes all-or-nothing on a forced failure; claim tests against real PostgreSQL: a sixth claim on a row with `attempts = 5` returns nothing and `exhaust` ends it `failed(stuck)`; a claim on a `processing` row with a live lease returns nothing, with an expired lease returns attempt n+1; a completion write from attempt n after attempt n+1 claimed the row changes nothing; `IssueDashboardToken` increments `token_version`; events are returned by the unit of work on commit, not published inside it.
- Open questions: sentence splitting rules for Russian and English; ordering of the task list; bigint column choices.

### S3 — Celery and Redis adapters: queue, events, tickets (after S2; board title "Redis adapters: queue, events, tickets")

- Scope: the Celery `app` in `infrastructure/celery/` with the settings of 3.1 (late ack, reject on worker lost, prefetch 1, no result backend, JSON, visibility timeout, routes for the two queues); `JobQueue` on `send_task` with `countdown`; the `worker` entry point package: role launcher (`WORKER_ROLE` → `-Q`, `-n`, `-B`), `worker_init` pre-flight hook (settings only; S5 adds the provider checks), `worker_process_init` building the per-child event loop and role-specific composition root, the `run_async` bridge used by every task, a test-only task module for the integration tests (S5 adds the real tasks in their own module); `EventPublisher` on pub/sub; `TicketStore` (value: `user_id` + `token_version`); `IssueRealtimeTicket`, `ConsumeRealtimeTicket`; `yarn dev:seed` publishes events; Redis client timeouts; `yarn dev:worker <role>`; integration tests on real Redis.
- Acceptance: a task handled without acknowledgement (child killed with SIGKILL mid-task) is redelivered and handled again; a delayed task is delivered no earlier than its countdown and is redelivered after a warm restart of the worker (with the Redis transport a countdown task waits in worker memory, unacknowledged in Redis; after a SIGKILL it returns only at the visibility timeout, which is why the sweep exists); an event published for user A is not received by a subscriber of user B; a ticket is consumed exactly once, expires, and returns the `token_version` it was issued with; `yarn dev:worker transcribe` and `yarn dev:worker notify` start side by side on Python 3.14 with distinct node names (no `NameError` from annotations, no `__wrapped__` lookup — the 3.14 risk of section 9 is checked here), each answers `inspect ping -d <role>@<host>`, and each stops cleanly on SIGTERM while a test task runs; two tasks sent to different queues are consumed by the right worker only.
- Open questions: `broker_connection_retry_on_startup`; per-task time-limit declaration style.

### S4 — Telegram bot (after S3)

- Scope: aiogram 3 application with long polling and the polling lock; `/start` (names the provider and extractor), `/dashboard` (private chats only); text → `sendChatAction typing` + `CreateTasksFromMessage` with the bot deadline; voice → `AcceptVoiceMessage` with the pre-download checks, the ack message, `ack_message_id` storage and the enqueue, in the order of 3.6; typed replies for refusals; the `UserNotifier` and `VoiceFileSource` adapters (used by S5 through the ports); publish-after-commit in the bot entry point; `yarn dev:bot`.
- Acceptance: handler tests with fake ports and recorded replies for text (one task, several tasks, degraded fallback line), voice, oversize voice, missing `file_size`, duplicate text update (no second reply, no second row set), duplicate voice update with and without a stored `ack_message_id` (resume vs stop), a crash after the text commit and before the reply (redelivery sends nothing, the tasks exist), `/dashboard` in a private chat and in a group; the recorded call order is insert → ack → store id → enqueue; an enqueue failure after the ack leaves a `queued` row and no crash; the `UserNotifier` adapter maps "not modified", `retry_after`, 403 and "not found" as 3.6 says (unit tests with a fake Telegram client); a live check with the owner's test bot is optional and recorded in the README as live or not.
- Open questions: message wording; a per-user rate limit; what `/start` shows beyond the provider and extractor names; whether a very short text skips the model.

### S5 — Worker: transcription and notification (after S3; uses S4's adapters and S9's extractor through the ports, with fakes until they land)

- Scope: `TranscribeVoiceJob`, `NotifyUser`, `RecoverStuckVoiceJobs`, built on the repository statements S2 ships (`stamp_*`, `begin_notification`, `defer_notification`, `finish_notification`; section 13 X8); `Transcriber` adapters `openai` (one call per attempt, typed error mapping), `whisper_server` (one multipart request per attempt, 300 s timeout, status-code mapping) and `fake`; the transcriber factory and the provider checks in the `worker_init` pre-flight; the failure taxonomy of 3.6 with typed errors; retry decisions from `attempts`; notification attempts and outcomes; the beat schedule for the sweep (routed to `notification`, first run from `worker_ready`); per-task time limits; task registrations, one module per queue; `provider` recorded on the row; both workers run from `yarn dev:worker <role>`; a measurement of transcription time for a 30 s and a 120 s Russian voice note against the owner's whisper-server, recorded in the stage diary.
- Acceptance: a unit test per row of the failure table (OpenAI errors through a stubbed client, whisper-server statuses through a stubbed transport) and per attempt boundary (attempt 4 retries, attempt 5 fails with `transient_exhausted`); an integration test runs the real worker against real Redis and PostgreSQL with the fake transcriber and the recording notifier and ends with task rows, a `done` job with `notified_at`, and the edited message; the same flow on `whisper_server` against the owner's running server with a committed 5 s OGG fixture asserts a non-empty transcript and the recorded `provider`; **one** live OpenAI check on the owner's key (the fixture transcribed as `voice.ogg`, recorded in the diary and the README as live) — everything else runs on the local server or the fake (owner's word); crash-point tests: a job left `queued` without a message, a job left `processing` past its lease, a `done` job without `notified_at` — each repaired by one sweep, and a `queued` row younger than the threshold is left alone; a crash loop (the child killed on every delivery) ends `failed(stuck)` with the user notified after the fifth attempt and never a sixth; the two-job timeline (job A runs 400 s, job B claimed at t=400, the sweep at t=480 leaves B alone because its lease is live; a third job still queued gets at most one extra copy, which is a no-op when it arrives); a notification failing five times ends `given_up` and is not swept again; a `processing` job redelivered to a second worker during a live lease waits it out and does not create a second task set; a server 4xx yields `unsupported_audio`; a request timeout and a simulated soft time limit each yield `transcription_timeout`; a stopped server yields retries and then `transient_exhausted`; the no-key path end to end on the dev servers: `TRANSCRIPTION_PROVIDER=openai` with an empty key, the worker starts, a voice job ends `failed(provider_unavailable)` and the recording notifier receives the "unavailable, please type it" edit.
- Open questions: the OpenAI model default (`gpt-4o-mini-transcribe` vs `gpt-transcribe`); whether the whisper-server adapter sends `temperature_inc` and a prompt; a second ack edit "Transcribed, extracting tasks…"; language hint vs auto-detect; whether failed voice jobs appear in the dashboard (decided here, exposed by S6 if yes). This is the largest stage; its designer may split it into two child workflows (use cases and adapters; sweep and integration) if the context requires.

### S6 — API: REST, authentication, SSE (after S3)

- Scope: the routes added to the FastAPI application factory that S1 ships with `GET /health` and the schema exporter; bearer dependency on `AuthenticateToken`; `GET /api/tasks`, `PATCH /api/tasks/{id}` (status) with publish-after-commit, `POST /api/realtime/tickets`, `GET /api/events` (subscribe before headers, `: connected`, heartbeat, close on `auth.revoked`), `GET /health`; the event models, which S2 defines in `domain` (`TaskView` and the three events; section 13 X2), exposed in the OpenAPI document; `yarn api:schema` and the committed `openapi.json`; `yarn dev:api`.
- Acceptance: httpx tests for 401 without a token, 404 for another user's task, status change persisted and published after commit (recording fake asserts the order), a ticket consumed once, an event published on Redis right after the response headers is delivered, the stream closes on `auth.revoked`; issue a ticket → rotate the token → connect with that ticket is `401`; a rotation whose event is suppressed in the test still closes an open stream at the next heartbeat; `/health` answers without a token; the exported schema contains the event models and the task `title`/`description` fields.
- Open questions: pagination; exposing voice job state (if S5 decided yes).

### S7 — Dashboard (after S6; tasks come from `yarn dev:seed` or from the bot when S4 is available)

- Scope: React application: token capture from the fragment and its removal, one view (list or Kanban) showing title and description (grouping by source message is the designer's choice), status controls, the SSE connection loop of 3.6, loading, empty, error and signed-out states, responsive layout; generated API types and Zod schemas; component tests.
- Acceptance: component tests for rendering, status change, an incoming `task.created`, and the reconnect loop (a killed stream leads to one new ticket and one refetch); `yarn dev:web` against `yarn dev:api` shows a seeded or bot-created task appear without reload; layout checked at 360 px and 1280 px (screenshots in the stage diary); no hand-written wire types or schemas.
- Open questions: list vs Kanban; drag-and-drop or buttons; copy; dark mode; whether the root guide's "const arrow functions" and "native interactive elements" rules get lint rules here (`func-style`, `eslint-plugin-jsx-a11y`) or the guides mark them as review-only (section 12).

### S9 — Task extraction with Claude Haiku (after S2; parallel with S4–S6)

- Scope: the `claude` adapter of 3.4, returning the port's `Extraction` with `ExtractedTasks` as the model-facing schema (section 13 X1), with the cached system prompt and splitting rules the owner assigned to it (S2-D5, X13), on the Anthropic Python SDK (`AsyncAnthropic(max_retries=0, timeout=deadline)`, `messages.parse` with `output_format=ExtractedTasks`, `claude-haiku-5-5`, effort `low`, system prompt that asks for tasks in the message's language with the title and description rules, a `max_tokens` cap); fallback to `heuristic`, marked `degraded`, on `APIConnectionError`, `APITimeoutError`, `RateLimitError`, any `APIStatusError`, a `refusal` or `max_tokens` stop reason, a `None` parsed output or a schema/cap validation failure; the extractor factory of 3.7; an opt-in live test (`ANTHROPIC_API_KEY` set) that sends two fixtures and asserts the shape; a prompt note in the module guide.
- Acceptance: unit tests with a stubbed client for each fallback trigger and for a valid parse, including a stub that sleeps past the deadline; the live test passes once on the owner's key and is recorded in the diary; the adapter never raises; the output is validated against the schema and the caps even when the SDK already parsed it; cost per message estimated from `usage` and written in the README (Haiku pricing, a few hundred tokens per message).
- Open questions: prompt wording and few-shot examples; whether the model may merge duplicate items; whether the dashboard shows the source text.

### S8 — Delivery: containers, Compose, end-to-end, README (after S4, S5, S6, S7, S9)

- Scope: `web.Dockerfile` (built on `node:26-alpine`) with nginx and the `/api` and `/api/events` locations of 3.6; the database credentials of the delivered stack read from `.env`, with placeholders in `.env.example`, so no password literal remains in the files a reviewer runs (section 12); full `docker-compose.yml` with all eight services plus the profile-gated `whisper` service (image, model download into the `whisper-models` volume on first start, `--convert`, health check on `/inference` readiness), health checks, `stop_grace_period` for `worker-transcribe` and the `migrate` ordering; the final binding of every real adapter in the bot and worker composition roots (S4, S5 and S9 land in any order with the port fakes where a sibling has not landed; S8 closes the wiring); one end-to-end test (fake Telegram update → bot handlers → worker with the fake transcriber and heuristic extractor → API → SSE event); README (design summary, assumptions of section 8, setup, the `.env` switch between `openai` and `whisper_server` (and the `whisper` profile) and what each needs, verification commands, which integrations were verified live, demo-mode notice, known limits); final `yarn verify`; one Compose run on the final commit.
- Acceptance: `docker compose up --build` from a fresh clone, run as an isolated stack (`TASKBOT_STACK=taskbot-e2e` with its own ports, so the shared development stack is untouched), with `.env` copied from `.env.example` and a real test bot token ends with seven services `healthy` and `migrate` `exited (0)` (`docker compose ps` recorded in the diary); a text message creates tasks; a voice message without an OpenAI key gets the `provider_unavailable` reply, and with the owner's key (on the owner's word) a real transcript; a second run with `TRANSCRIPTION_PROVIDER=whisper_server` and `COMPOSE_PROFILES=whisper` transcribes the fixture voice note through the container (time recorded; this is the one Compose whisper run); the end-to-end test passes; the README's commands are the ones that were run; a second `docker compose up` is harmless.
- Open questions: image size budget; whether a live Telegram, OpenAI and Claude smoke run on the Compose stack is part of the hand-in (on the owner's word only).

Dependencies on the board: S2 after S1; S3 after S2; S4, S5, S6 after S3; S9 after S2; S7 after S6; S8 after S4, S5, S6, S7, S9.

## 6. Release

- Integration branch for the umbrella: `umbrella/assessment`. Children integrate there; the umbrella reaches `main` once through its own review.
- Verification command pinned on the umbrella: `yarn verify` (with `yarn infra:up` as its documented prerequisite).
- Both are declared on the board by the lead (`stage … umbrella --integration-branch umbrella/assessment --verify "yarn verify"`) before the accepted gate stamp; the first gate round asked for it.
- The repository guide's second iteration (written by the lead after this gate) must carry: the command table of section 4, the layer rules and the two tool configurations of 3.3, the port/adapter table of 3.4 with its owners, the `VoiceJob` state machine and the "database owns attempts" rule, the Celery rules of 3.1 (sync tasks, per-child loop and composition root, no `self.retry`, countdown below the visibility timeout), the provider switch (`openai` in Compose, `whisper_server` in development, `heuristic` without a key) and the visible markers, the publish-after-commit and extract-before-transaction rules, and the rule that the Compose stack runs once on the final commit. Each stage keeps its module guide current in the same commit.

## 7. Change profile

New behaviour, greenfield repository. Touches: a new process per stage, two stores (PostgreSQL, Redis) and three external services (Telegram, the Anthropic API, the browser) plus one local model runtime (whisper.cpp). Earns: the architecture-documentation check (this document is that map until a stage adds something it does not show); authorization (personal tokens, tickets, rotation): who may read and change a task, the token hash as the only stored secret, and the revocation of open streams; concurrency (duplicate Telegram updates, concurrent first contact, worker redelivery and retries, a crash between any two steps of 3.6): the uniqueness invariant, the guarded state machine, the idempotent claim and the sweep cover them, and S2, S4 and S5 each carry a test per window; untrusted model output (S9): validated by schema and caps, never executed or trusted as an instruction. No money, no on-chain writes, no migration of existing data.

## 8. Assumptions recorded for the reader (not requirements of the assignment)

- No password login; the personal token from the bot is the only credential, and the board is per user. A new `/dashboard` link signs out other devices.
- Task fields are title, description, status and timestamps, grouped by the message they came from; no editing, priorities, due dates or deletion.
- One message may yield several tasks (the owner's addition); without an API key, exactly one.
- One dashboard view (decided in S7).
- Long polling, so the stack runs without a public URL; one bot instance per token; one replica per service.
- Voice limits: 120 s and 10 MB; longer messages are refused with an explanation. Telegram voice is OGG/Opus; it is sent as `voice.ogg` to OpenAI or to whisper-server, which converts it with ffmpeg (`--convert`).
- Transcription in the Docker stack uses OpenAI with a key, or the optional whisper.cpp container with a small model on the CPU; the owner's own whisper-server on Apple Silicon (Metal) is the development provider and is not part of the image. Language is auto-detected.
- The token lives in the browser's local storage without expiry (an assessment trade-off, stated in the README).

## 9. Risks carried into the stages

- Celery 5.6.3 (2026-03-26) declares Python 3.9–3.13; the 3.14 annotation fix (PEP 649) is in 5.6.3, but one 3.14 fix (task signature inspection through `__wrapped__`) is only in the 5.7 prereleases (5.7.0a1 onwards; 5.7.0rc1 of 2026-10-18 carries the 3.14 classifier). The design never wraps task functions with `functools.wraps`; S3 checks a worker start, a task run and `inspect ping` on 3.14, and `celery==5.7.0rc1` is the fallback if anything else surfaces — together with its kombu, which moves the redis-py pin to `>=6.4,<9`. kombu 5.6 caps `redis<6.5`, so the application's own redis-py pin follows whichever Celery is in use.
- OpenAI's documents disagree on OGG input: the API reference lists `ogg` among the accepted formats, the speech-to-text guide lists only mp3, mp4, mpeg, mpga, m4a, wav and webm. The single live check in S5 settles it; if OGG is refused, the adapter converts with ffmpeg (then the image needs ffmpeg) as a follow-up inside S5. The guide's current example model is `gpt-transcribe` while the SDK types still name `gpt-4o-mini-transcribe`; the default is confirmed by the same check.
- whisper-server reads OGG/Opus only through `--convert`, which needs ffmpeg beside the server: present on the owner's Mac; whether the `ghcr.io/ggml-org/whisper.cpp:main` image ships ffmpeg is checked in S8 (fallback: the `whisper` service builds a thin image on top of it with ffmpeg, the backend image stays untouched). The server must be started in development (`yarn dev:whisper`); a stopped server shows up as retries and a clear final failure, never a crash.
- whisper.cpp in Docker on a Mac runs on the CPU without Metal, so the profile uses a small model (`base`) and is a reviewer's fallback, not the owner's path; the `/inference` endpoint and the `--convert` flag are verified against the server's documentation, its readiness probe is settled in S8.
- A reviewer with neither a key nor the profile sees voice messages answered with the `provider_unavailable` text; the README says this in the first screen so it is read as a configuration choice, not a defect.
- The OpenAI and Anthropic SDKs depend on `httpx2`, a separate package from `httpx` (used by FastAPI's test client); both install side by side, and no HTTP mocking library is used for the SDKs (the adapters are tested with stubbed clients and recording fakes).
- The model is a degradable dependency: refusal, rate limit, outage or an invalid parse all fall back to the heuristic, and the row says so. Cost on Haiku is cents per thousand messages.
- SSE through nginx and Docker networking: covered by the location block and the heartbeat; verified in S8.
- Machine load during the stages is high; integration suites and whisper measurements run one at a time.

## 10. Owner acceptance (D6)

What a person gets, stage by stage, on their own machine:

| After | The person can | Estimated effort (agent wall-clock, with gate and review) |
|---|---|---|
| S1 | clone, install, run `yarn verify`; nothing to use yet | 4–6 h |
| S2 | nothing visible; the data model, state machine, heuristic extractor and use cases exist with tests; `yarn dev:seed` creates tasks | 5–8 h |
| S3 | nothing visible; Celery queues, events, tickets and the worker runtime work against Redis with tests on Python 3.14 | 4–6 h |
| S4 | send a text to the bot and see the created tasks (one or several); send a voice and get "Transcribing…" (no result yet); get a dashboard link | 5–8 h |
| S5 | send a voice and get it transcribed by the Mac's whisper-server (dev) or by OpenAI (one live check) back in the same message as tasks, or a clear failure; measured transcription times | 7–10 h |
| S9 | the Claude Haiku adapter exists with its live test; `yarn dev:seed` with an Anthropic key splits a text into tasks with titles and descriptions; the bot and the worker use it once S8 binds the real adapters | 3–5 h |
| S6 | call the API with the token from the bot (S4) or a seeded token and see the tasks; change a status; receive SSE events | 5–7 h |
| S7 | open the dashboard, see seeded or bot-created tasks appear live, move them between statuses, on a phone and a desktop | 5–8 h |
| S8 | run `docker compose up --build` and use everything from one command (an OpenAI key in `.env` for voice, or the `whisper` profile without a key); read the README | 6–9 h |

S4, S5, S6 and S9 can run as parallel children (two at a time given machine load), S7 after S6. Total: about 44–67 hours of agent work; on a calendar with two children in parallel and the owner's answers within the hour, roughly seven to nine working days to a hand-in-ready repository. The owner's time: one grill per stage (ten to twenty questions) and one acceptance table per stage.

Owner's acceptance of this statement, 2026-10-23: «Да, принимаю».

## 11. Pre-handoff review record

Three lenses ran on round 1 on 2026-10-23 (coherence, premise verification against library sources and documentation, adversarial). Folded in: the voice job state machine and the database-owned `attempts` (HIGH); the recovery sweep over `queued`, `processing` and un-notified rows (HIGH); the ack-before-enqueue order and the notifier fallback (HIGH); the dashboard-owned SSE reconnect loop (HIGH); arq's maintenance-only status, re-decided by the owner (HIGH); the `UnitOfWork` port and publish-after-commit; per-entry-point settings and the environment table; health checks for every service; Compose ownership moved to S1 and S8 and the Dockerfile smoke build to S1; `yarn db:migrate` and `yarn dev:seed`; `UserNotifier`, `VoiceFileSource`, `AuthenticateToken`, ticket use cases assigned to stages; the single event vocabulary with generated types and Zod; the exact import-linter contracts; the `failure_kind` enumeration; Telegram notification edge cases; duplicate-update semantics; token rotation closing streams and `/dashboard` in private chats; the visible demo marker; JSON payloads and `127.0.0.1` port binds; the 700-line check and ruff complexity rules; `file_size` optional in Telegram updates; TypeScript and Node pins; the D-03 wording; the bot polling lock; the Alembic advisory lock; the nginx SSE location.

Round 2 chose taskiq; the owner's own follow-up check of the library (deprecation of one-worker-several-streams, no delayed delivery for streams, receive-latency issue) led to round 3: Celery (D-02), two transcription adapters — OpenAI with one live check, and an HTTP adapter to a whisper.cpp server: the owner's Homebrew install on the Mac for development and every other transcription test, an optional Compose profile for reviewers (D-09) — and the task-extraction step with its heuristic fallback (D-10, stage S9, `inbound_messages` table, `title`/`description`/`position` on tasks). Two lenses (premise, coherence) ran on the Celery and local-whisper draft of round 3 before the owner moved the local adapter to whisper-server; their findings that survive the move are folded in: the transaction ownership of composed use cases (`within(uow)`), the worker token for the file download, the role variable and explicit node names, the main-process pre-flight, per-task time limits with a summed budget and the soft-limit mapping, `last_enqueued_at` and the sweep thresholds, notification attempts and outcomes, `pydantic.ValidationError` and `max_tokens` as extractor fallbacks with a per-root deadline, the recording fake extractor in S2, NOT NULL message columns, the seed's token and events, the `web` probe, `stop_grace_period`, the countdown-in-memory wording of the S3 acceptance, the beat schedule file, the text-path crash window, the final adapter binding in S8, and the Celery 5.7 prerelease wording. Findings about pywhispercpp (child start-up timeout, partial transcripts on abort, null model context, unchecked downloads) fell away with the adapter and are kept in the review artifacts. Library facts for round 3 were verified against the libraries' documentation and release pages before writing: Celery 5.6.3 release notes and the 3.14 fixes, the Redis broker caveats (visibility timeout, late ack, prefetch), whisper.cpp's server (`/inference` multipart contract, `--convert`, `-l auto`, the published image and its model download script), the OpenAI Python SDK 2.37.0 transcription call, retry and timeout options and its 3.14 support, the Anthropic Python SDK 2.2.0 structured-output API and its 3.14 support.

Design gate round 1 (Codex) returned the design with two HIGH and three MED findings, all folded in: a missing credential degrades instead of stopping a worker (G1: 3.1, 3.7, S5 no-key test through worker and notifier); the claim is bounded by the attempt ceiling and an attempt lease, with `exhaust` for crash redelivery and attempt-guarded completion writes (G2: 3.5, 3.6, S2 and S5 tests); the sweep uses three clocks and states its bounded-duplication policy (G3: 3.6, S5 two-job timeline); tickets carry `token_version` and the stream re-checks it at connect and on every heartbeat (G4: 3.5, 3.6, S2, S3, S6); the lead's board declarations (G5: section 6).

Rejected: the browser's six-connections-per-origin limit for many open tabs (out of scope at assessment scale); Redis Streams event replay for the dashboard (D-08 keeps refetch-on-reconnect); Celery's `self.retry` counter as the owner of attempts (the database row owns them, as decided in round 2).

## 12. Amendment 1 (2026-10-24): what S1 changed, and two owner decisions

S1 (`docs/plans/2026-10-24-s1-skeleton.md`, integrated at `9ec8031`) departs from this text in the points its section 4 lists; S1's gate accepted them and the owner decided the version choices (S1-D1, S1-D2). This amendment carries them into the umbrella so later stages read one current text. The S1 entry in section 5 is kept as written: it is the scope S1 was given.

| Was in this document | Is now | Source |
|---|---|---|
| Node 24, `node:24-alpine` | Node 26 (`.nvmrc` `26`, `engines.node >=26`), `node:26-alpine` for the S8 web image | S1-D1 |
| TypeScript 5.x, eslint 9 | TypeScript 6, ESLint 10 | S1-D2 |
| `redis:7`; PostgreSQL image not named | `redis:8`; `postgres:18-alpine` with data at `/var/lib/postgresql` | S1-D2 |
| `openai>=2.37,<3`, `anthropic>=2.2,<3` | `openai>=3.37,<4` (S5 writes the adapter against 3.x), `anthropic>=2.3,<3` | S1-D2 |
| openapi-typescript and a Zod generator; `src/api/schema.d.ts`, `src/api/zod.ts` | `@hey-api/openapi-ts` for both; `frontend/src/api/generated/{types.gen.ts, zod.gen.ts, index.ts}`; operation ids come from route names, which must be unique | S1-D4, S1 review |
| FastAPI application factory in S6 | the factory with `GET /health` and the schema exporter ships in S1; S6 adds the routes | S1-D3 |
| `yarn dev:worker <queue>`, `yarn dev:worker transcription` | `yarn dev:worker <role>`, role `transcribe` or `notify` | S1 |
| `yarn infra:down`; `yarn infra:up` as a direct Compose call | `yarn infra:stop` (stops, removes nothing); `yarn infra:up` runs `scripts/infra.mjs` with the shared-stack guard | S1-D7, S1 gate G1 |
| `tsc --noEmit` | `tsc -b` over the referenced projects | S1 |
| File-size rule exempts tests | also exempts generated files and Alembic versions | S1 |
| `POSTGRES_*` as variables | literal development values in the shared stack; `DATABASE_URL` carries the credentials | S1 gate G1 |
| The S8 Compose run from a fresh clone | runs as an isolated stack (`TASKBOT_STACK=taskbot-e2e`, its own ports) | S1-D7, S1 gate G1 |

Root commands beyond section 4 that S1 added: `verify:backend`, `verify:frontend`, `check:scripts`, `check:size`, `api:check`, `test:unit`. A `dev:*` command whose module belongs to a later stage is a stub that exits 1 naming that stage; the stage that builds the module replaces the stub.

**R-ISO: integration tests isolate themselves.** Every stage worktree shares one PostgreSQL and one Redis (project `taskbot`, S1-D7). An integration-test session creates its own database `taskbot_test_<unix-time>_<random>`, migrates it and drops it at the end; no test writes to the `taskbot` database. On Redis a session prefixes every key, channel, lock and Celery queue it names, and deletes its keys at the end. No test assumes it is alone on the stack. S2 builds the PostgreSQL fixture and S3 the Redis one (S1 section 6); every later stage with integration tests uses them.

Cleanup of what a killed session left behind may use age only to pick candidates. Before it drops a database or deletes keys it did not create, a session proves that no live session owns them; an old database of a paused but live session is never dropped. S2 chooses the mechanism for PostgreSQL and S3 for Redis, and each tests it with a live owner that is older than the age filter. This supersedes the age-only drop in S1 section 6, which no S1 code implements.

**Owner decision, 2026-10-24: the delivered stack carries no password literal.** A secret scanner flagged `POSTGRES_PASSWORD: taskbot` in `docker-compose.yml`. It is a development value on a stack bound to `127.0.0.1` and is kept there, because S1's gate G1 needs the shared stack's definition to be independent of any `.env`. The owner chose to leave it until S8 («1»): S8 reads the database credentials of the delivered stack from `.env`, with placeholders in `.env.example`, so the files a reviewer runs hold no password literal (S8 scope in section 5).

**Open question for S7** (from the S1 review): the root guide's "const arrow functions" and "native interactive elements" rules have no lint rule yet. S7 decides with the owner whether it adds `func-style` and `eslint-plugin-jsx-a11y`, or the guides mark these rules as review-only.

## 13. Amendment 2 (2026-10-24): what S2 changed

S2 (`docs/plans/2026-10-24-s2-domain-core.md`, integrated at `40ba17e`) departs from this text in the thirteen points of its section 9; S2's gate accepted them, and X13 is the owner's decision (S2-D5, S2-D6). The S2 design's section 9 is the full statement with the reasons; the table below names each change so later stages know where to read. Sections 3.2, 3.4, 3.5, 3.6 and the S5, S6 and S9 scopes above carry the changes in place.

| # | What changed | Who it binds |
|---|---|---|
| X1 | `TaskExtractor.extract` returns `Extraction(tasks, extractor, degraded)`; `ExtractedTasks(tasks)` is only the model-facing schema | S9, S4, S5 |
| X2 | `TaskView` and the three events are defined in `domain` by S2; S6 only exposes them in the OpenAPI document | S3, S6, S7 |
| X3 | a retry first writes `requeue` (`processing → queued`, attempt-guarded) in the use case's unit of work | S5 |
| X4 | `exhaust` also requires `status = 'queued' OR claimed_at < now − lease`; `last_enqueued_at` is `NOT NULL`, set at insert and by every terminal write | S5 |
| X5 | the sweep re-enqueues an expired `processing` row only when `last_enqueued_at` is older than 460 s, stamped in the same statement | S5 |
| X6 | `claim` returns a `ClaimOutcome` with the whole row; the refusal is classified by the pure `classify_claim_refusal` | S5 |
| X7 | every compared time is the `Clock` port's aware UTC value bound as a parameter, never `now()` | S3, S5 |
| X8 | S2 ships the repository statements `stamp_*`, `begin_notification`, `defer_notification`, `finish_notification`; S5 builds `RecoverStuckVoiceJobs` and `NotifyUser` on them | S5 |
| X9 | `begin_notification` counts at the start and stamps `last_enqueued_at`; the sweep has no `notify_attempts` filter; `NotificationCeilingReached` ends the row `given_up`; the new column `voice_jobs.notify_not_before`, written on `RetryAfterError`, holds the sweep and `begin_notification` until it passes | S4, S5 |
| X10 | the recording extractor fake lives in `backend/tests/fakes/`; the transcriber `fake` stays a production adapter | S5, S9 |
| X11 | R-ISO in PostgreSQL: a plain `DROP DATABASE`, which PostgreSQL refuses while a connection exists, is the liveness proof; `FORCE` only for a session's own database | every stage with integration tests |
| X12 | a fourth entry point, `entrypoints/seed/`, holds the `yarn dev:seed` composition root | S3 (seed publishes events), S8 |
| X13 | the fallback has no language rules: the text on one line, cut at a word to 120 characters, is the title and the whole text is the description; splitting is the model's job only, with a cached system prompt and rules | S9, S4, S5 |

The S1 design's STATUS line uses the canonical word again (ACTIVE) and keeps the supersession S2 wrote into it.
