# S2 — Domain core and PostgreSQL persistence

> **STATUS 2026-10-24: ACTIVE — stage design, round 4 (round 2: pre-handoff lens findings; round 3: the owner's extractor decisions S2-D5 and S2-D6; round 4: design gate round 1, G1 notification deferral and G2 strict task ordering; section 10), for the design gate.** Superseded in part by `docs/plans/2026-10-26-s9-claude-extraction.md` (Y1, Y2, Y4, Y9): one task per message, `Extraction.task`, `open_extractor`, descriptions up to 4096 characters.

Stage S2 of the umbrella design `docs/plans/2026-10-23-umbrella-assessment.md` (umbrella section 5, S2), read with its amendment 1 (umbrella section 12) and the S1 design `docs/plans/2026-10-24-s1-skeleton.md`. The umbrella fixes the scope; this document fixes how S2 delivers it, records the owner's decisions for this stage, and lists where S2 departs from the umbrella's text (section 9, rows X1–X13). "Umbrella x.y" names a section of the umbrella design; a bare number names a section of this document. The implementation steps are called S2.1 to S2.5.

## 1. Problem

S1 left the four Python layers empty. Every later stage needs the same core: the entities and rules of tasks, users, inbound messages and voice jobs; the ports the adapters implement; the use cases the bot, the worker and the API call; and a PostgreSQL schema with repositories that keep the uniqueness, concurrency and attempt rules of umbrella 3.5 under real concurrent writes. S2 builds that core, the fallback task extractor used when the model is absent or fails, the migration command, a seed command for work without the bot, and the isolated integration-test database every later stage uses. S2 builds no process that talks to Telegram, Redis, a transcription provider or a browser.

## 2. Decisions

Taken with the repository owner on 2026-10-24. The owner's words are given in English; the Russian originals are in the designer's diary.

| # | Decision | Owner's answer |
|---|---|---|
| S2-D1 | The task list is ordered by source message, newest first; the tasks of one message stay together in the order of the text (`inbound_message_id DESC, position ASC`). A status change does not move a task. | "1" |
| S2-D2 | A task's status can change from any of the three statuses to any other, including back from `completed`. Setting the current status again changes nothing: no write, no event. | "1" |
| S2-D3 | A message is accepted only when its text contains at least one letter or digit in any script (`str.isalnum`, which also counts numeric signs such as `²` or `½`). A message of only emoji, punctuation or invisible characters is refused like an empty one: no row, and the bot (S4) replies with one line. | "1" |
| S2-D4 | Persistence style: the domain holds frozen dataclasses and `Protocol` ports; the infrastructure describes tables with typed SQLAlchemy declarative classes (the schema for Alembic and mypy) and writes every query with SQLAlchemy Core (`insert … on_conflict … returning`, `update … where … returning`), mapping each returned row to a domain entity with one function. The ORM session's object tracking is not used. | "1", after the explanation that no SQL strings are written and that the claim, the upsert and the guarded completion must each be one atomic statement |
| S2-D5 | Splitting a message into tasks is the language model's job only: Claude Haiku (umbrella S9) with a carefully written system prompt, rules and examples, and with prompt caching of the system prompt and the rules. No language rules (sentences, abbreviations, list markers) are written in code. | "I propose not to write deterministic code, but to write a good prompt and rules and ask Haiku to split the task (do not forget the cache of the system prompt and the rules)" |
| S2-D6 | When the model is not configured or fails, a fallback without language rules keeps the message: one task whose title is the text on one line, cut at a word to 120 characters, and whose description is the whole text; the result is marked `degraded`, and the bot says so in one line. | "1" |

Rejected for S2-D4: ORM objects for plain inserts with Core for the atomic transitions (two styles in one layer; the identity map goes stale after a direct `UPDATE`); imperative mapping onto the domain classes (the domain classes become mutable and ORM-managed); Core `Table` objects without declarative classes (columns are `Any` under mypy strict).

Designer decisions inside those answers, settled from the code, the umbrella and executed probes (diary, "D2 context map" and "pre-handoff review"): status-like columns are `VARCHAR(32)` with a named `CHECK`, not native PostgreSQL enums (3.4); every time a query compares or writes is a bound parameter from the `Clock` port, never the database `now()`, which is frozen at transaction start; clocks are timezone-aware UTC; internal ids are `int` identity columns and Telegram ids `bigint`; `position` is 0-based; the fallback rule of 3.6; tokens are `secrets.token_urlsafe(32)` hashed with SHA-256; Alembic lives in `backend/alembic/`, as umbrella 3.2 places it.

## 3. Design

### 3.1 Files

New files:

```
backend/
  alembic.ini                     script_location = %(here)s/alembic; no URL (env.py reads settings)
  alembic/
    env.py                        async engine, advisory lock inside the transaction (3.7)
    script.py.mako                template whose output passes ruff and mypy
    versions/0001_initial.py      the four tables of 3.4
  src/taskbot/
    domain/
      limits.py                   the code constants of umbrella 3.7 that S2 uses (3.2)
      text.py                     clean_text, clean_inline, MessageText (S2-D3)
      users.py  messages.py  tasks.py   entities, value objects, enums
      voice_jobs.py               VoiceJob, its enums, transitions, ClaimOutcome, NotificationStart, classify_*_refusal
      extraction.py               TaskDraft, ExtractedTasks, Extraction, ExtractorName
      events.py                   TaskView, TaskCreated, TaskUpdated, AuthRevoked, DomainEvent
      jobs.py                     TranscriptionJob, NotificationJob, TicketClaims
      errors.py                   domain and port error types
      ports/
        persistence.py            the four repositories, UnitOfWork, UnitOfWorkFactory (3.5)
        services.py               Clock, TokenGenerator, TokenHasher, TaskExtractor
        messaging.py              JobQueue, EventPublisher, TicketStore
        telegram.py               UserNotifier, VoiceFileSource
        transcription.py          Transcriber, Transcript
    application/
      register_user.py            RegisterOrGetUser
      create_tasks.py             CreateTasksFromMessage
      change_task_status.py       ChangeTaskStatus
      list_tasks.py               ListTasks
      dashboard_token.py          IssueDashboardToken, AuthenticateToken
      publishing.py               publish_events (log and continue)
    infrastructure/
      settings/database.py        DatabaseSettingsMixin
      settings/migration.py       MigrationSettings
      settings/seed.py            SeedSettings
      clock.py                    SystemClock
      tokens.py                   SecretsTokenGenerator, Sha256TokenHasher
      extraction/heuristic.py     HeuristicExtractor: the fallback of S2-D6 (no language rules)
      extraction/factory.py       build_extractor (S9 adds the claude branch)
      sqlalchemy/
        tables.py                 declarative table classes, MetaData naming convention
        engine.py                 create_engine_and_sessions(database_url, *, pool_size)
        mapping.py                Row → entity functions
        users.py  messages.py  tasks.py  voice_jobs.py   one repository per file
        unit_of_work.py           SqlAlchemyUnitOfWork
    entrypoints/
      seed/__init__.py  seed/__main__.py   yarn dev:seed (3.8)
  tests/
    conftest.py                   moved up from tests/integration: marking, reachability, database fixtures
    databases.py                  R-ISO helpers (3.7)
    fakes/                        clock.py, tokens.py, calls.py, extraction.py, persistence.py (3.9)
    contract/                     repository contract suite, fake and PostgreSQL legs (3.9)
    unit/                         entities, state machine, text, heuristic, use cases, events
    integration/                  unit of work, concurrency, migration, R-ISO, seed
    acceptance/replay_s2.py       read-only replay of the D6 table (section 7)
```

Changed files: `backend/pyproject.toml` (3.7, 3.9), `backend/tests/integration/conftest.py` (removed; its content moves to `tests/conftest.py`), `backend/tests/unit/test_env_example.py` and `backend/tests/unit/test_integration_collection.py` (3.7), root `package.json` (`db:migrate`, `dev:seed`), `docker/backend.Dockerfile` (3.7), `backend/AGENTS.md` (each step), `README.md` (command rows), and the status header of `docs/plans/2026-10-24-s1-skeleton.md` (section 9, X11). No file comes near 700 lines; the largest is the voice-job repository, about 300 lines.

### 3.2 Domain model

**Constants** (`domain/limits.py`): `MAX_TASKS_PER_MESSAGE = 10`, `TITLE_MAX = 120`, `DESCRIPTION_MAX = 2000`, `MAX_TRANSCRIPTION_ATTEMPTS = 5`, `MAX_NOTIFY_ATTEMPTS = 5`, `LEASE_SECONDS = 450`, `SWEEP_STALE_SECONDS = 460`, `SWEEP_UNNOTIFIED_SECONDS = 120`, `MAX_TOKEN_LENGTH = 128`, and `retry_delay_seconds(attempt) = 2 * 2 ** (attempt - 1)`. Later stages add their constants here.

**Text cleaning** (`domain/text.py`), one implementation used by every text that reaches the database:
- `clean_text(raw)`: line breaks `\r\n`, `\r`, `\v`, `\f`, `\x85`, ` `, ` ` become `\n`; then control characters (Unicode category `Cc`, except `\n` and `\t`) and lone surrogates (`Cs`) are removed, because PostgreSQL rejects NUL and asyncpg rejects surrogates; then whitespace is stripped at both ends.
- `clean_inline(raw)`: `clean_text`, then every run of whitespace, line breaks included, becomes one space. It is used for `TelegramIdentity.display_name` and for titles.
- `MessageText`: a frozen value object built only by `MessageText.parse(raw)`, which applies `clean_text` and raises `EmptyMessageError` unless one character satisfies `str.isalnum` (S2-D3). The bot's text and the worker's transcript both pass through it; S5 maps an `EmptyMessageError` on a transcript to `empty_transcript`.

**Enums** (`StrEnum`; the value is the stored string): `TaskStatus` (`pending`, `in_progress`, `completed`), `MessageKind` (`text`, `voice`), `ExtractorName` (`heuristic`, `claude`), `VoiceJobStatus` (`queued`, `processing`, `done`, `failed`), `FailureKind` (the eight values of umbrella 3.6), `NotificationOutcome` (`edited`, `replied`, `blocked`, `given_up`), `TranscriptionProvider` (`fake`, `openai`, `whisper_server`).

**Value objects**: `TelegramIdentity(telegram_user_id: int, display_name: str)` (`display_name` cleaned with `clean_inline`, empty allowed); `MessageRef(chat_id: int, message_id: int)`, Telegram's ids.

**Extraction types** (`domain/extraction.py`, Pydantic, frozen, because S9 needs their JSON schema):
- `TaskDraft(title, description)`: a validator cleans `title` with `clean_inline` and `description` with `clean_text`, then requires a non-empty title of at most 120 characters and a description of at most 2000.
- `ExtractedTasks(tasks: list[TaskDraft])`, 1–10 drafts: the model-facing schema that S9 passes as the structured output format.
- `Extraction(tasks: tuple[TaskDraft, ...], extractor: ExtractorName, degraded: bool)`: what the `TaskExtractor` port returns (X1).

**Entities** (frozen dataclasses with `slots`). A transition method returns the new entity, or `None` when its guard refuses, exactly as the repository statement returns a row or nothing; the in-memory fakes call these methods, so the fake and the SQL state one rule each.
- `User(id, telegram_user_id, display_name, token_version, created_at, updated_at)`. The token hash stays inside the repository.
- `InboundMessage(id, user_id, ref, kind, text, extractor, degraded, created_at)`.
- `Task(id, user_id, inbound_message_id, position, title, description, status, created_at, updated_at)`; `change_status(new, now) -> Task | None` returns `None` when the status is unchanged (S2-D2) and otherwise sets `updated_at = max(now, self.updated_at + 1 µs)`, so each real change of one task carries a strictly greater `updated_at` than the one before, even when the clock repeats or goes back (`timestamptz` and `datetime` both keep microseconds); `to_view() -> TaskView`.
- `VoiceJob`: every column of 3.4; the transitions of 3.3.

**Events** (`domain/events.py`, Pydantic, frozen). `TaskView` is the wire shape of umbrella 3.6: `id, inbound_message_id, position, title, description, status, created_at, updated_at`. `TaskCreated(type="task.created", task)`, `TaskUpdated(type="task.updated", task)`, `AuthRevoked(type="auth.revoked")`; each carries `user_id` for routing to `events:user:<user_id>`, excluded from serialisation (`Field(exclude=True)`). `DomainEvent` is their union discriminated by `type`. S3 serialises with `model_dump_json()`; S6 exposes the same classes in the OpenAPI document (X2).

**Job payloads and ticket claims** (`domain/jobs.py`, Pydantic, frozen, `extra="forbid"`): `TranscriptionJob(voice_job_id)`, queue `transcription`, job name `transcribe_voice_job`; `NotificationJob(voice_job_id, status: Literal["done", "failed"])`, queue `notification`, job name `notify_user`; the queue and job name are class variables, so S3 routes by class. `TicketClaims(user_id, token_version)` for S3's `TicketStore`.

**Errors** (`domain/errors.py`). Every exception name ends in `Error` (ruff N818); all subclass one `DomainError`. Raised by S2's code: `EmptyMessageError` (`MessageText.parse`), `TaskNotFoundError` (`ChangeTaskStatus`, also for another user's task, so the API answers 404 for both), `InvalidTokenError` (`AuthenticateToken`). Raised by later stages' code, defined here so the application can catch them without importing adapters: `StaleAttemptError` (S5's voice unit of work, when `lock_attempt` returns `None`), `EnqueueError`, `TranscriptionError(kind)`, `VoiceFileTooLargeError`, `VoiceFileUnavailableError`, `RetryAfterError(seconds)`, `RecipientBlockedError`, `MessageGoneError`, `NotificationError`.

### 3.3 The VoiceJob state machine

```
             add_or_get (attempts 0, last_enqueued_at = now)
                        │
                        ▼
   ┌──────────────► queued ──claim (attempts+1, claimed_at)──► processing
   │                   ▲                                         │  │  │
   │                   └──requeue (same attempt, retry)──────────┘  │  │
   │                                                                │  │
   │   exhaust (attempts ≥ 5, queued or lease expired)              │  │
   │        └────────────────────────► failed(stuck) ◄──fail(kind)──┘  │
   │                                      │                            │
   │                                      │            done ◄─complete─┘
   │                                      ▼              ▼
   └── (the sweep re-enqueues)    begin_notification → finish_notification(outcome)
                                     └─ RetryAfter → defer_notification (notify_not_before)
```

`done` and `failed` are absorbing: every transition except `set_ack_message_id` has the status in its `WHERE`, and no transition leads out of a terminal status. Every transition also writes `updated_at = now`. Each is one guarded statement in the repository and the same guard in the entity; a refused guard returns `None` (or `False` where nothing is returned), never raises:

| Transition | Allowed from | Guard | Writes | Returns |
|---|---|---|---|---|
| `add_or_get(new, now)` | — | unique (`telegram_chat_id`, `telegram_message_id`) | `queued`, `attempts = 0`, `last_enqueued_at = now` | `AddedVoiceJob(job, created)`; a conflict returns the stored row with `created = False` |
| `set_ack_message_id(id, ack, now)` | any | `ack_message_id IS NULL` | `ack_message_id` | `bool` (False: another resume stored one first) |
| `mark_enqueued(id, now)` | `queued`, `processing`, or terminal with `notified_at IS NULL` | — | `last_enqueued_at = now` | `bool` |
| `claim(id, now)` | `queued`, or `processing` with `claimed_at < now − LEASE` | `attempts < 5` | `processing`, `attempts + 1`, `claimed_at = now` | `ClaimOutcome` |
| `lock_attempt(id, attempt)` | `processing` | `attempts = attempt` | `SELECT … FOR UPDATE` | `VoiceJob \| None` (None: stale) |
| `requeue(id, attempt, now)` | `processing` | `attempts = attempt AND attempt < 5` | `queued`, `last_enqueued_at = now` | `VoiceJob \| None` |
| `complete(id, attempt, inbound_message_id, provider, now)` | `processing` | `attempts = attempt` | `done`, `inbound_message_id`, `provider`, `last_enqueued_at = now` | `VoiceJob \| None` |
| `fail(id, attempt, kind, provider, now)` | `processing` | `attempts = attempt` | `failed`, `failure_kind`, `provider`, `last_enqueued_at = now` | `VoiceJob \| None` |
| `exhaust(id, now)` | `queued`, or `processing` with `claimed_at < now − LEASE` | `attempts >= 5` | `failed`, `failure_kind = stuck`, `last_enqueued_at = now` | `VoiceJob \| None` |
| `begin_notification(id, now)` | `done`, `failed` | `notified_at IS NULL AND notify_attempts < 5 AND (notify_not_before IS NULL OR notify_not_before <= now)` | `notify_attempts + 1`, `last_enqueued_at = now` | `NotificationStart` |
| `defer_notification(id, until, now)` | `done`, `failed` | `notified_at IS NULL`; the domain refuses `until < now` | `notify_not_before = GREATEST(COALESCE(notify_not_before, until), until)`, `last_enqueued_at = now` | `bool` |
| `finish_notification(id, outcome, now)` | `done`, `failed` | `notified_at IS NULL` | `notified_at = now`, `notification_outcome` | `VoiceJob \| None` |

`exhaust`'s `queued` branch is defensive: no normal path leaves a `queued` row with `attempts >= 5` (`claim` always moves to `processing`, `requeue` requires `attempt < 5`); its test builds such a row directly.

`claim` returns a `ClaimOutcome` (`domain/voice_jobs.py`):

```python
type ClaimOutcome = Claimed | AlreadyFinished | LeaseLive | CeilingReached | JobMissing
# Claimed(job)                       job.attempts is the attempt the worker runs
# AlreadyFinished(job)               done or failed: acknowledge and drop
# LeaseLive(remaining_seconds: int)  another attempt runs: re-enqueue with this countdown (rounded up, ≥ 0)
# CeilingReached(job)                attempts = 5 and no live lease: run exhaust, then notify
# JobMissing()                       no such row: acknowledge and drop
```

`begin_notification` returns a `NotificationStart` the same way:

```python
type NotificationStart = NotificationStarted | NotificationNotDue | NotificationCeilingReached | NothingToNotify
# NotificationStarted(job)                    job.notify_attempts is the attempt S5 runs
# NotificationNotDue(remaining_seconds: int)  notify_not_before is later than now (rounded up, ≥ 0)
# NotificationCeilingReached(job)             notify_attempts = 5: S5 calls finish_notification(given_up)
# NothingToNotify()                           no such row, not terminal, or already notified: acknowledge and drop
```

When the claim's `UPDATE` returns no row, the repository reads the row once and passes it to the pure domain function `classify_claim_refusal(job: VoiceJob | None, now) -> ClaimOutcome`, which both the fake and the PostgreSQL repository use. It checks, in this order: missing, terminal, live lease, ceiling. If none applies (the row became claimable between the two statements), it returns `LeaseLive(0)`, so the delivery re-enqueues itself at once instead of looping. The function is unit-tested on constructed rows, which is how the race case is reached. A refused `begin_notification` goes through `classify_notification_refusal(job: VoiceJob | None, now) -> NotificationStart` in the same way, in this order: nothing to notify, ceiling, not due; the race case returns `NotificationNotDue(0)`.

**Sweep statements** (S2 builds them, S5 the use case and the beat task). Each selects and stamps in one statement (`UPDATE … WHERE id IN (SELECT id … FOR UPDATE SKIP LOCKED LIMIT 100) RETURNING`), so two concurrent sweeps never return the same row:
- `stamp_stale_processing(now) -> list[VoiceJobId]`: `processing`, `claimed_at < now − 460 s` and `last_enqueued_at < now − 460 s`.
- `stamp_stale_queued(now) -> list[VoiceJobId]`: `queued` and `last_enqueued_at < now − 460 s`.
- `stamp_unnotified(now) -> list[UnnotifiedJob]` (`UnnotifiedJob(id, status)`): `done` or `failed`, `notified_at IS NULL`, `last_enqueued_at < now − 120 s`, and `notify_not_before IS NULL OR notify_not_before <= now`. It has no `notify_attempts` filter (X9).
Each sets `last_enqueued_at = now` on the rows it returns.

**Notification rule.** `begin_notification` counts the attempt before the Telegram call, so a crash during it is counted, and it stamps `last_enqueued_at`, so the sweep waits 120 s after each attempt instead of sending a parallel copy while the call runs. When Telegram answers `RetryAfterError(seconds)` and `notify_attempts < 5`, S5's `NotifyUser` does not wait in the worker: it calls `defer_notification(id, now + seconds + 1 s, now)` and ends the delivery. Until that time `stamp_unnotified` skips the row and `begin_notification` returns `NotificationNotDue` without counting an attempt, so a cooldown of any length costs exactly one attempt. S5 drops a `NotificationNotDue` delivery or re-enqueues it with that countdown; the sweep returns the row once it is due either way. `GREATEST` keeps a later deferral from being shortened by an earlier one. On `NotificationCeilingReached`, and on a `RetryAfterError` during the fifth attempt, `NotifyUser` calls `finish_notification(given_up)`. Because the sweep keeps returning an un-notified, due row until `notified_at` is set, a crash during the fifth attempt still leads to a sixth delivery, whose begin returns `NotificationCeilingReached` and ends the row.

**Retry and back-off.** `requeue` stores no "not before" time: a stray copy of the job (a sweep copy or an earlier `LeaseLive` re-enqueue) that arrives before the 2–16 s delay claims the row at once as the next attempt. All attempts still count against the ceiling of 5; this design accepts that a short outage can use attempts faster than the schedule.

**The voice unit of work** (S5 runs it; S2 tests it): `lock_attempt` first (S5 raises `StaleAttemptError` on `None`), then `CreateTasksFromMessage.within`, then `complete`. A stale attempt stops at the first statement and writes nothing; a failure at any later point rolls the message, the tasks and the job state back together (umbrella 3.4). If `within` returns `DuplicateMessage(message_id)` on this path, the caller completes the job with that stored message id.

`retry_delay_seconds` and the failure kinds are in the domain; which error maps to which kind and when to retry is S5's (umbrella 3.6).

### 3.4 Schema (`alembic/versions/0001_initial.py`)

All four tables of umbrella 3.5 in one migration, with every column later stages read, so S3–S9 need no migration for the fields the umbrella fixed. `created_at`, `updated_at` and `last_enqueued_at` are `timestamptz NOT NULL`; `claimed_at`, `notified_at` and `notify_not_before` are nullable `timestamptz`. The application writes every timestamp from the `Clock` port; there are no database defaults or triggers for time. Status-like columns are `Enum(…, native_enum=False, create_constraint=True, length=32, name="<column>", values_callable=…)`: `VARCHAR(32)` with a `CHECK` named `ck_<table>_<column>` that lists the lowercase values. A `MetaData` naming convention fixes every index and constraint name.

| Table | Columns (beyond umbrella 3.5) | Constraints and indexes |
|---|---|---|
| `users` | `id int identity`, `telegram_user_id bigint`, `display_name text NOT NULL`, `dashboard_token_hash text NULL`, `token_version int NOT NULL DEFAULT 0` | unique `telegram_user_id`; unique `dashboard_token_hash` |
| `inbound_messages` | `telegram_chat_id bigint`, `telegram_message_id bigint`, `kind`, `text text NOT NULL`, `extractor NOT NULL`, `degraded bool NOT NULL` | unique (`telegram_chat_id`, `telegram_message_id`); FK `user_id` with index |
| `tasks` | `position int` (0-based), `title text`, `description text NOT NULL` | unique (`inbound_message_id`, `position`); index (`user_id`, `inbound_message_id DESC`, `position`) for S2-D1; checks `char_length(title) BETWEEN 1 AND 120`, `char_length(description) <= 2000`, `position BETWEEN 0 AND 9` |
| `voice_jobs` | as umbrella 3.5; `last_enqueued_at timestamptz NOT NULL` (X4); `notify_not_before timestamptz NULL` (X9); `ack_message_id bigint` | unique (`telegram_chat_id`, `telegram_message_id`); checks `attempts BETWEEN 0 AND 5`, `notify_attempts BETWEEN 0 AND 5`, `status <> 'done' OR inbound_message_id IS NOT NULL`, `status <> 'failed' OR failure_kind IS NOT NULL`, `notified_at IS NULL OR status IN ('done', 'failed')`, `notify_not_before IS NULL OR status IN ('done', 'failed')`; partial index on `last_enqueued_at` where `status IN ('queued', 'processing') OR notified_at IS NULL` |

Columns written for diagnosis and read by no use case: `users.display_name`, `voice_jobs.file_unique_id`, `duration_seconds`, `file_size`, `provider`, `notification_outcome`, and `inbound_messages.text`, `kind`, `extractor`. They stay because umbrella 3.5 fixed them and they let a reviewer see what ran.

### 3.5 Persistence adapter

**Repository ports** (`domain/ports/persistence.py`). Every method takes the values it needs; `now` is always explicit. "None" in the last column is the refusal or absence result; no method raises for a guard or a conflict.

| Port | Method | Returns |
|---|---|---|
| `UserRepository` | `upsert(identity, now)` | `UpsertedUser(user, created)` |
| | `get(user_id)` | `User \| None` |
| | `find_by_token_hash(token_hash)` | `AuthenticatedUser(user_id, token_version) \| None` |
| | `rotate_token(user_id, token_hash, now)` | `int` (the new `token_version`) `\| None` (no such user) |
| | `get_token_version(user_id)` | `int \| None` (no such user) |
| `InboundMessageRepository` | `add(new: NewInboundMessage, now)` | `InboundMessage \| None` (None: duplicate) |
| | `get_by_ref(ref)` | `InboundMessage \| None` |
| | `get(message_id)` | `InboundMessage \| None` |
| `TaskRepository` | `add_many(user_id, inbound_message_id, drafts, now)` | `tuple[Task, ...]` in position order |
| | `get_for_update(user_id, task_id)` | `Task \| None` (None also for another user's task) |
| | `save_status(task)` | `Task \| None` (owner and id in the `WHERE`) |
| | `list_for_user(user_id)` | `tuple[Task, ...]` ordered as S2-D1 |
| | `list_for_message(inbound_message_id)` | `tuple[Task, ...]` in position order |
| `VoiceJobRepository` | the transitions (including `defer_notification`) and sweep statements of 3.3, plus `get(job_id) -> VoiceJob \| None` | as 3.3 |

`NewInboundMessage(user_id, ref, kind, text: MessageText, extractor, degraded)` and `NewVoiceJob(user_id, ref, file_id, file_unique_id, duration_seconds, file_size)` are the insert values.

**Unit of work port**: `UnitOfWork` is a `Protocol` with the attributes `users`, `messages`, `tasks`, `voice_jobs`, the method `record(*events: DomainEvent) -> None`, `async commit() -> tuple[DomainEvent, ...]` (commits and returns the recorded events), and the async context manager methods; leaving the context without `commit()` rolls back. `type UnitOfWorkFactory = Callable[[], UnitOfWork]`. Nothing can publish inside a unit of work: no use case holds a publisher.

**Implementation** (`infrastructure/sqlalchemy/`):
- `tables.py`: `class Base(DeclarativeBase)` with the naming convention; one `Mapped[...]` class per table; `__table_args__` are tuples.
- Every statement is Core and its `RETURNING` names the table's columns (`.returning(*UserRow.__table__.c)`), so results are plain `Row`s and the session's identity map stays empty; `mapping.py` turns a `Row` into an entity. `rowcount` is not used (it is untyped under mypy strict).
- `upsert` is `INSERT … ON CONFLICT (telegram_user_id) DO UPDATE SET display_name = excluded.display_name, updated_at = excluded.updated_at RETURNING <columns>, literal_column("(xmax = 0)", Boolean).label("inserted")`, read by tuple position (attribute access on the row is `Any` under mypy strict). The update is unconditional, because a `DO UPDATE … WHERE` that is false returns no row. It never touches `dashboard_token_hash` or `token_version`.
- `InboundMessageRepository.add` and `VoiceJobRepository.add_or_get` use `ON CONFLICT DO NOTHING RETURNING`; no row means duplicate, and `add_or_get` then reads the stored row in the same transaction. No savepoint is needed: `DO NOTHING` does not abort the transaction. Identity values burned by conflicts leave gaps; no test asserts contiguous ids.
- Time: lease and sweep cutoffs are computed in Python (`now − timedelta(seconds=…)`) and bound. `SystemClock.now()` returns `datetime.now(UTC)`; every repository method raises `ValueError` on a naive `now`, because asyncpg silently shifts a naive value by the host time zone.
- `SqlAlchemyUnitOfWork(sessions)` opens an `AsyncSession` on `__aenter__`, binds the four repositories to it, collects events, commits in `commit()`, and rolls back and closes in `__aexit__`.
- `create_engine_and_sessions(database_url: str, *, pool_size: int = 5) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]` with `expire_on_commit=False`. Composition roots call it with the URL from their settings; tests call it with the test database URL and `pool_size=2`.

### 3.6 Use cases and the heuristic extractor

Every use case receives its ports in the constructor. The transaction belongs to the outermost caller (umbrella 3.4): a composable use case has `within(uow, …)`, which writes and records events but never extracts and never commits, and a standalone `execute(…)`, which opens its own unit of work. Results are frozen dataclasses. Only a standalone result carries `events` (the tuple `commit()` returned); a `within` result never does, so a caller cannot publish twice.

- **`RegisterOrGetUser`**: `within(uow, identity) -> UpsertedUser(user, created)`; `execute(identity) -> UpsertedUser`.
- **`CreateTasksFromMessage`** (ports: extractor, unit-of-work factory, `RegisterOrGetUser`, clock):
  - `execute(CreateTasksCommand(identity, ref, kind, text: str)) -> TasksCreated | DuplicateMessage`. Order: `MessageText.parse` (raises `EmptyMessageError`; no extraction, no row) → `extractor.extract` → open the unit of work → `RegisterOrGetUser.within` → `within(…)` → commit.
  - `within(uow, *, user_id, ref, kind, text: MessageText, extraction) -> TasksCreated | DuplicateMessage`: insert the message; a conflict returns `DuplicateMessage(message_id)` with the stored message's id, writes nothing else and records no event; otherwise insert the tasks at positions 0..n−1 and record one `TaskCreated` per task in position order.
  - `TasksCreated(user_id, message, tasks, events)` carries `message.degraded` and the titles, which S4 needs for the reply and S5 for the ack edit. On a duplicate S4 sends no reply.
- **`ChangeTaskStatus`**: `execute(user_id, task_id, status) -> TaskChange(task, changed, events)`; raises `TaskNotFoundError`. One unit of work: `tasks.get_for_update(user_id, task_id)` → read the clock (after the row lock, so a later commit never carries an earlier time) → `task.change_status(status, now)` → when changed, `save_status` and record `TaskUpdated`. Same status: `changed = False`, no write, no event (S2-D2). Two concurrent changes serialise on the row lock; the last commit wins, and its `updated_at` is strictly greater than the previous one (3.2), so no two committed changes of one task share a value. S7 keeps, for each task, the event with the greatest `updated_at` and drops an event whose `updated_at` is not greater than the one it holds; publication order does not matter.
- **`ListTasks`**: `execute(user_id) -> tuple[Task, ...]`, ordered as S2-D1. No filter and no pagination (umbrella D-08: the dashboard refetches the whole list; S6 owns the pagination question). A voice message's tasks are inserted when its transcript is ready, so they sort by that moment, not by when the voice was sent (section 7, R10).
- **`IssueDashboardToken`**: `execute(identity) -> IssuedToken(user_id, token, token_version, events)`. It takes the identity, because `/dashboard` can be a user's first message. One unit of work: `RegisterOrGetUser.within` → generate the token → hash it → `users.rotate_token` (`UPDATE … SET dashboard_token_hash = :h, token_version = token_version + 1 … RETURNING token_version`) → record `AuthRevoked`. `IssuedToken.token` is excluded from `repr`. Two concurrent calls for one user serialise on the user row; exactly one of the two tokens stays valid, the one committed last.
- **`AuthenticateToken`**: `execute(token) -> AuthenticatedUser(user_id, token_version)`; raises `InvalidTokenError`. It refuses a token that is not ASCII or longer than `MAX_TOKEN_LENGTH` before hashing, then calls `users.find_by_token_hash`, which returns the id and `token_version` from one query (S3 stores that exact version in the ticket, umbrella 3.6). `users.get_token_version` serves S3's ticket check and S6's heartbeat.
- **Tokens**: `SecretsTokenGenerator` returns `secrets.token_urlsafe(32)` (43 URL-safe characters, 256 bits); `Sha256TokenHasher` returns the lowercase hex SHA-256 of the ASCII bytes. A 256-bit random secret needs no salt; equality lookup on the unique hash column works.
- **`publish_events(publisher, events)`** (`application/publishing.py`): publishes each event and logs a failure without raising (umbrella 3.4). S3 binds the publisher; S2 tests it with a failing fake.

**Fallback extractor** (`infrastructure/extraction/heuristic.py`; S2-D6). The stored extractor name stays `heuristic` (umbrella 3.5, S1's `TASK_EXTRACTOR` values). `HeuristicExtractor(degraded: bool = False).extract(text: MessageText) -> Extraction` has no language rules; splitting a message into tasks is the model's job (S2-D5, S9). **Contract: for every `MessageText` it returns exactly one valid `TaskDraft` and never raises.**

1. `line` = the text cleaned with `clean_inline` (one line, single spaces).
2. If `line` has at most 120 characters, the title is `line`; otherwise it is cut at the last space before character 120 and gets `…`, or, when there is no space in the first 119 characters, cut hard at 119 and gets `…`.
3. The description is empty when the title is the whole text unchanged (one line, not cut); otherwise it is the whole text cleaned with `clean_text`, newlines kept, cut to 1999 characters plus `…` when longer than 2000 (the full text always stays in `inbound_messages.text`).
4. The result has `extractor = heuristic` and the constructor's `degraded` flag.

A seeded fuzz test (fixed seed, a few thousand random strings over Latin, Cyrillic, CJK, digits, punctuation, emoji, zero-width and control characters and line breaks) checks the contract on every string `MessageText.parse` accepts.

**What S9 carries from S2-D5.** The `claude` adapter's system prompt holds the splitting rules and examples (one task per actionable item, titles and descriptions in the message's language, a list becomes several tasks, no invented tasks, the caps of 3.2) and is sent as a stable prefix with `cache_control` before the message text, so repeated calls read it from the cache (Claude Haiku 5.5 caches a prefix of at least 512 tokens; a cache read costs about a tenth of the input price, and it shortens the time to the first token, which the bot's 8 s deadline needs). S9's design writes the prompt, chooses the cache lifetime (5 minutes or 1 hour) and measures `cache_read_input_tokens` in its live test. Whether `TASK_EXTRACTOR` defaults to `claude` in `.env.example` and Compose (then a missing key yields the degraded fallback with its one-line notice) is S9's question with the owner.

**Extractor factory** (`infrastructure/extraction/factory.py`): `build_extractor(settings: ExtractorSettingsMixin) -> TaskExtractor` returns `HeuristicExtractor()` for `heuristic`, and `HeuristicExtractor(degraded=True)` for `claude`, logging once that the Claude adapter is not built yet. S9 replaces the `claude` branch.

**Port signatures for later stages** (`domain/ports/`, Protocols only, no adapter in S2): `JobQueue.enqueue(job, *, delay_seconds=0)` (raises `EnqueueError`), `EventPublisher.publish(event)`, `TicketStore.issue(claims) -> str` and `consume(ticket) -> TicketClaims | None`, `UserNotifier.send_reply(chat_id, text, *, reply_to_message_id=None) -> int` and `edit_message(chat_id, message_id, text)` (typed errors of 3.2; "message is not modified" is a normal return), `VoiceFileSource.fetch(file_id, *, max_bytes) -> bytes`, `Transcriber.transcribe(audio, *, timeout_seconds) -> Transcript` (raises `TranscriptionError(kind)`; attribute `provider`). The stage that builds an adapter may refine its port's signature in its own design, with the reason; S2 fixes the names and the types the domain already has.

### 3.7 Settings, migrations and the isolated test database

**Settings.** `DatabaseSettingsMixin(BaseModel)` with a required `database_url: PostgresDsn` whose validator requires the scheme `postgresql+asyncpg` (a plain `postgresql://` validates as a DSN and then fails when the async engine is created). Entry-point models: `MigrationSettings(BaseAppSettings, DatabaseSettingsMixin)` and `SeedSettings(BaseAppSettings, DatabaseSettingsMixin, ExtractorSettingsMixin)` with `web_base_url` (default `http://localhost:5173`). Tests read `InfraTestSettings` (S1) for the server address only; they connect to their own test databases. `tests/unit/test_env_example.py` adds both models and keeps its explicit list of compared defaults (`DATABASE_URL` has no default now and leaves the list; `WEB_BASE_URL` joins it); secrets stay empty.

**Alembic** (`backend/alembic.ini`, `backend/alembic/`). `script_location = %(here)s/alembic`, so the configuration works from any working directory. `env.py` takes the URL from `config.attributes["database_url"]` when a caller passes one and from `MigrationSettings` otherwise, builds an async engine with `NullPool`, and runs the migrations through `connection.run_sync`. Inside `with context.begin_transaction():` its first statement is `SELECT pg_advisory_xact_lock(<constant key>)`, so concurrent upgrades run one after the other and later ones find nothing to do. Measured in the diary's D2 entry: the lock placed before `begin_transaction()` makes Alembic treat the connection as an outer transaction and never commit, and four concurrent upgrades then exit 0 with no tables; A5 therefore checks the tables, not the exit code. The whole upgrade is one transaction (Alembic's default); a later migration that needs `transaction_per_migration` must revisit the lock. `env.py` does not call `logging.config.fileConfig`, which disables the application's loggers in-process. `script.py.mako` produces revisions that pass ruff and mypy; `alembic/` joins mypy's `files`. `yarn db:migrate` is `uv run --locked --directory backend alembic upgrade head`.

**Ruff.** `backend/pyproject.toml` sets `[tool.ruff] src = ["src"]` and `[tool.ruff.lint.isort] known-first-party = ["taskbot", "tests"]`: without them the new `backend/alembic/` directory makes ruff classify the `alembic` package as first-party and the import order fails (measured). `per-file-ignores` turn off `RUF001`, `RUF002` and `RUF003` (Cyrillic letters that look like Latin ones) for `tests/**`, with a comment: the tests hold Russian text on purpose. Planned code avoids `ASYNC220` (no `subprocess.Popen` in async tests), `PT011` (`pytest.raises` with `match=`) and `RUF012` (tuple `__table_args__`).

**Backend image.** `docker/backend.Dockerfile` copies `backend/alembic.ini` and `backend/alembic/` into the runtime stage's `/app`, so umbrella S8's `migrate` service can run `alembic upgrade head` there. The smoke build is re-run and recorded (backend/AGENTS.md rule), and `docker run --rm taskbot-backend:s2 alembic heads` prints `0001`.

**R-ISO PostgreSQL databases** (umbrella section 12; S1 section 6). The helpers live in `tests/databases.py` and talk to the server through raw asyncpg on the `postgres` maintenance database (asyncpg runs `CREATE` and `DROP DATABASE` outside a transaction, and its exceptions carry the SQLSTATE). The URL helpers derive the maintenance and test URLs from `InfraTestSettings().database_url` (swap the database name; strip `+asyncpg` for raw asyncpg) and have unit tests.

1. **`test_database(name_time)`**: an async context manager that creates `taskbot_test_<unix-time>_<8 hex>`, opens one **anchor connection** to it and keeps it open, and yields its URL; in `finally` it closes the anchor and drops the database with `DROP DATABASE IF EXISTS … WITH (FORCE)`. Everything after `CREATE DATABASE` runs inside that `try`, so a failing migration leaves nothing behind. Identifiers are quoted by one helper, never formatted into SQL by hand.
2. **Session fixture** (`tests/conftest.py`, session scope): runs the stale sweep (step 4), opens `test_database`, runs `alembic upgrade head` on it through `asyncio.to_thread(command.upgrade, config, "head")` with a `Config` built from the absolute path of `alembic.ini` by `make_alembic_config(url)`, which always sets `config.attributes["database_url"]` and refuses a URL whose database name is not a test name; yields the URL.
3. **Per-test cleanup** (function fixture requested by persistence tests): inside one transaction, `SET LOCAL lock_timeout = '5s'`, a check that `current_database()` is the session's test database, then `TRUNCATE` of every table `RESTART IDENTITY CASCADE`; a leaked transaction then fails the test instead of hanging the suite. Concurrency tests (two units of work at once, under `asyncio.TaskGroup`) commit for real, which a rolled-back outer transaction would not allow.
4. **Stale sweep with a liveness proof**, `drop_abandoned_databases(now, *, names=None)`. Candidates are databases whose name satisfies `re.fullmatch(r"taskbot_test_([0-9]+)_[0-9a-f]{8}", name)`, whose time part is older than six hours before `now`, and which are not the session's own; the optional `names` limits the candidates (for the A6 test). Candidates with a connection in `pg_stat_activity` are skipped at once (an optimisation: a refused `DROP` waits about 5 s on the server). For each remaining candidate the sweep issues a plain `DROP DATABASE IF EXISTS "<name>"` without `FORCE`. PostgreSQL refuses it atomically while any session is connected (SQLSTATE `55006`): the sweep leaves that database. SQLSTATE `3D000` (another session's sweep dropped it first) is also a normal outcome. Any other error is raised with the database name. The sweep logs which databases it left. A live session always holds its anchor connection, also while its process is paused (a stopped process keeps its socket); a killed session's connections end with its process, so its database becomes droppable.
5. **Migration tests** use their own databases from `test_database`, never the session database: A5's empty database, its downgrade and its two-process race. The race test passes `DATABASE_URL` explicitly to each `alembic upgrade head` subprocess (`asyncio.create_subprocess_exec`) and checks afterwards that the development database's table list and `alembic_version` are unchanged.
6. `pyproject.toml` sets `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"`: the session fixture, the function-scoped cleanup fixture and the tests then share one event loop (measured: with only the test scope set, the function-scoped cleanup fixture fails with "attached to a different loop").
7. Test engines use `pool_size=2`; the shared server allows 100 connections and several worktrees run tests at once.
8. `tests/integration/conftest.py` moves to `tests/conftest.py` with its two hooks unchanged in behaviour (mark everything under `tests/integration/`, probe reachability when an integration item is selected), so the contract suite in `tests/contract/` can mark only its PostgreSQL leg. `tests/unit/test_integration_collection.py` stops asserting the literal `"2/"` and compares the reported count with a separate collection of the `integration` marker.

### 3.8 `yarn dev:seed`

`yarn dev:seed <telegram_user_id> [text …]` runs `uv run --locked --directory backend python -m taskbot.entrypoints.seed`. The command builds its composition root from `SeedSettings` (engine, unit-of-work factory, `SystemClock`, token generator and hasher, `build_extractor`) and calls `seed(ports, telegram_user_id, text, message_id)`, a function tested against a test database. `seed` runs the standalone `CreateTasksFromMessage.execute` with identity `(telegram_user_id, "Seed user")`, `chat_id = telegram_user_id` and the given `message_id`, then `IssueDashboardToken.execute`, and returns the titles, the token and the link `<WEB_BASE_URL>/#token=<token>`, which the command prints. The command passes `message_id = −(time.time_ns() // 1000)`: negative, so it never meets a real Telegram id, and in microseconds, so quick reruns do not collide. On `DuplicateMessage` the output says the message was already seeded and still issues the token. Without text it uses one built-in English sample. The returned events go to one call site where S3 binds `publish_events`; in S2 that call site logs "events not published (no publisher until S3)". Each run rotates the token, and the output says that older dashboard links stop working. `yarn --silent dev:seed …` prints only the command's own output.

### 3.9 Tests

- **Fakes** (`tests/fakes/`): `clock.py` (settable aware clock), `tokens.py` (sequential tokens, readable hasher), `calls.py` (one call log shared by the recording fakes), `extraction.py` (recording extractor with a scripted result), `persistence.py` (in-memory repositories and unit of work that call the entity transitions, with a `fail_on` hook for forced failures). Later stages add their own modules beside these.
- **Repository contract suite** (`tests/contract/`): one test module per repository port, parametrised over two legs, `fake` and `postgres`; the `postgres` leg is a `pytest.param(…, marks=pytest.mark.integration)`, so `pytest -m "not integration"` runs the fake leg and `pytest -m integration` the PostgreSQL leg. Every method of every repository port of 3.5 has at least one case. The entity rules and the SQL rules therefore cannot drift.
- **Unit tests**: entity transitions; `classify_claim_refusal`; `clean_text`, `clean_inline`, `MessageText`; the heuristic table and fuzz test; each use case with fakes; event serialisation; `publish_events` with a failing publisher; the URL helpers of 3.7.
- **Integration tests** (PostgreSQL only; S2 uses no Redis): the unit of work, concurrency, the composed voice unit of work, schema introspection, the migration, R-ISO, the seed.

## 4. Implementation steps

One commit each, in this order; each step runs every check that exists after it, leaves `git status --short` empty, and updates `backend/AGENTS.md` for what it adds.

| Step | Scope | Done when |
|---|---|---|
| S2.1 Domain and fallback extractor | `domain/` (limits, text, entities, VoiceJob transitions and `classify_claim_refusal`, extraction types, events, jobs, errors, ports); `infrastructure/extraction/` fallback and factory; ruff `src`, `known-first-party` and the Cyrillic per-file ignore for tests; `tests/fakes/` clock, tokens, calls, extraction; unit tests; `tests/acceptance/replay_s2.py` | ruff, format, mypy, lint-imports, `pytest -m "not integration"` green; A1, A2, A9 |
| S2.2 Use cases | `application/`; `tests/fakes/persistence.py`; use-case unit tests; the contract suite's fake leg | A3a, A4a, A13a |
| S2.3 Schema and test databases | settings mixin and the two models; `tables.py`; `alembic.ini`, `alembic/`, `0001_initial`; `tests/databases.py`; `tests/conftest.py` (moved hooks, fixtures); pyproject loop scopes and mypy `files`; `test_env_example.py` and `test_integration_collection.py`; `yarn db:migrate`; the S1 status note (X11) | A5, A6, A10; `yarn verify` green |
| S2.4 Repositories and unit of work | `sqlalchemy/` engine, mapping, four repositories, unit of work; `SystemClock`, token adapters; the contract suite's PostgreSQL leg; unit-of-work, concurrency, composition and token tests | A3b, A4b, A7, A8, A13b |
| S2.5 Seed, image and guides | `entrypoints/seed`; `yarn dev:seed`; Dockerfile COPY and smoke check; `backend/AGENTS.md` summary (layers, fakes, contract suite, R-ISO usage, migration rule); README command rows | A11, A12; `yarn verify` green on the final commit |

## 5. Acceptance criteria

Each is observable on this machine with the shared stack up (`yarn infra:up`); the implementation note records the command and its output. "a" items run with fakes, "b" items on PostgreSQL.

- **A1 Entities and state machine.** Unit tests cover every transition of 3.3, allowed and refused (wrong status, wrong attempt, ceiling, live lease, terminal rows), and `classify_claim_refusal` on constructed rows for every outcome, including the race that yields `LeaseLive(0)` and the rounding of `remaining_seconds`; `Task.change_status` over all nine status pairs (same status → `None`, other → a new task whose `updated_at` is strictly greater); with a clock that repeats one instant, two changes give `T + 1 µs` and `T + 2 µs` over a task updated at `T`, and with a clock five seconds behind, `previous + 1 µs`; `classify_notification_refusal` on constructed rows for every outcome, including `NotificationNotDue` with its rounding, the ceiling taking precedence over a pending deferral, and the race that yields `NotificationNotDue(0)`; `defer_notification` refuses `until < now`.
- **A2 Text and fallback extractor.** `clean_text` maps CR, CRLF, VT, FF, NEL and U+2028/2029 to newlines and removes NUL and lone surrogates; `MessageText.parse` refuses an empty string, whitespace, `👍`, `...`, a zero-width-only string and NUL-only text, and accepts `Купить 🥛` and `²`. The fallback returns exactly rows R1–R6 of section 7, plus: a 120-character line (not cut, empty description); a 121-character line (cut at a word); a 150-character URL and 130 CJK characters (hard cut to 119 + `…`); a 3000-character text (description cut to 1999 + `…`). The seeded fuzz test finds no input that raises or yields an invalid draft, and no output with more than one task.
- **A3 Use cases.**
  - **a.** With recording fakes on one call log, `extract` runs before the unit of work opens; `commit()` returns the recorded events and a `within` result carries none; a duplicate returns `DuplicateMessage(message_id)` with no events and no second row set; `ChangeTaskStatus` to another status returns `changed = True`, exactly one `TaskUpdated` and a new `updated_at`, to the same status `changed = False`, no event and an unchanged `updated_at`, and on another user's task raises `TaskNotFoundError`; with a fake clock that returns the same instant, two changes `todo → in_progress → done` give two `TaskUpdated` events with strictly increasing `updated_at`, and a reducer written in the test with S7's rule (3.6) ends at `done` when it receives them in reverse order; `IssueDashboardToken` increments `token_version` by one per call and the old token then fails `AuthenticateToken`; a non-ASCII or 129-character token is refused before the hasher is called.
  - **b.** On PostgreSQL: another user's task is absent from `ListTasks` and `ChangeTaskStatus` raises `TaskNotFoundError` for it; issue, re-issue and authenticate behave as in A3a; two concurrent `IssueDashboardToken` calls for one user leave exactly one of the two tokens valid; the two same-instant changes of A3a store `updated_at` values that differ by exactly 1 µs when read back.
- **A4 Invariant and concurrency.**
  - **a.** With fakes: the same Telegram message twice as text gives one row set and `DuplicateMessage`; twice as a voice job gives one job and `created = False`.
  - **b.** On PostgreSQL: the same two cases; two first-contact `RegisterOrGetUser.execute` calls under `asyncio.TaskGroup` give one users row, both results with the same user id, exactly one with `created = True`; ten concurrent claims of one queued job give exactly one `Claimed`.
- **A5 Migration.** On an empty test database `alembic upgrade head` creates the four tables; `alembic check` reports no difference for tables, columns, indexes and unique constraints; an introspection test reads every named `CHECK` (`pg_get_constraintdef`), the `VARCHAR(32)` lengths and the partial index predicate (`pg_indexes`) and compares them with an expected list; `downgrade base` then `upgrade head` succeeds. Two `alembic upgrade head` processes started together against one empty database both exit 0, the four tables exist afterwards, `alembic_version` is at head, and the development database is unchanged.
- **A6 R-ISO.** A test session creates, migrates and drops its own `taskbot_test_*` database; after the run no database of that session is left, and the development database's table list is unchanged. The liveness test, limited by `names` to its own databases and run with `now` shifted seven hours ahead: a database held by an open connection survives the sweep; after that connection closes, the next sweep drops it; two sweeps run together over one droppable database both finish without error (one drops it, the other gets `3D000`); names that do not match the pattern (`taskbot`, `taskbot_test_x`, a name with a trailing newline) are never candidates.
- **A7 Claim, lease and completion on PostgreSQL** (umbrella S2 acceptance, with X3–X9), through the contract suite: five claims give attempts 1 to 5 when each lease has expired; the sixth gives `CeilingReached` and `exhaust` ends the job `failed(stuck)`; a claim during a live lease gives `LeaseLive` with the remaining seconds, and after the lease attempt n+1; `exhaust` on a row with `attempts = 5` and a live lease changes nothing, and after the lease ends it; a completion write from attempt n after attempt n+1 claimed changes nothing; after `requeue` the next claim succeeds at once as attempt n+1; a new row has `last_enqueued_at` equal to its creation time, and `requeue`, `complete`, `fail`, `exhaust` and `begin_notification` stamp it; each sweep statement returns a row once and not again before its threshold passes, and two concurrent sweeps never return the same row; a row with `notify_attempts = 5` and `notified_at` NULL is returned by `stamp_unnotified` after 120 s, `begin_notification` then returns `NotificationCeilingReached`, `finish_notification(given_up)` ends it, and the sweep no longer returns it; a sweep right after `begin_notification` returns nothing. The deferral case (G1 of the design gate; the cooldown is longer than five recovery intervals): `begin_notification` at t = 1 s gives attempt 1, `defer_notification(until = 902 s)` follows; `stamp_unnotified` at 180, 360, 540, 720 and 900 s returns nothing, `begin_notification` at 500 s returns `NotificationNotDue(402)` and `notify_attempts` stays 1; at 902 s the sweep returns the row once and `begin_notification` gives attempt 2; a `defer_notification` with an earlier `until` leaves the later one in place.
- **A8 Composition.** `CreateTasksFromMessage.within` and the job's `complete` in one unit of work commit together; a forced failure after the tasks are inserted leaves no message, no task and the job still `processing`; a stale attempt stops at `lock_attempt` and writes nothing.
- **A9 Events.** Each event serialises to exactly the keys of umbrella 3.6 (`type` and `task` with the eight `TaskView` fields, or `type` alone) and never contains `user_id`.
- **A10 Settings.** `MigrationSettings(_env_file=None)` with `DATABASE_URL` removed from the environment fails naming `database_url`; a `postgresql://` URL without `+asyncpg` fails naming `database_url`; `.env.example` validates through both new models (extended `test_env_example.py`).
- **A11 Seed.** On a throwaway test database (`DATABASE_URL` set to it, dropped afterwards), `yarn db:migrate` then `yarn --silent dev:seed 1001 "Позвонить маме. Спросить про выходные."` prints the title `Позвонить маме. Спросить про выходные.`, the token and the `#token=` link; a second run prints a new token, and only the new one authenticates; the `seed` function's test shows the same, and two seeds with the same `message_id` give the "already seeded" branch.
- **A12 Image.** `docker build -f docker/backend.Dockerfile -t taskbot-backend:s2 .` succeeds; `docker run --rm taskbot-backend:s2 alembic heads` prints `0001 (head)`; `yarn verify` is green on the final commit.
- **A13 Ordering (S2-D1).**
  - **a.** With fakes, and **b.** on PostgreSQL: of two messages, the newer one's tasks come first; a message's tasks keep position order; a status change does not move a task.

## 6. Change profile

New behaviour in a greenfield core. Touches: a new store schema (four PostgreSQL tables and the first migration); concurrency (duplicate updates, concurrent first contact, the voice-job claim with its lease, ceiling and attempt guards, concurrent sweeps, concurrent notifications, concurrent token rotation, concurrent migrations); authorization data (the dashboard token: random generation, the SHA-256 hash as the only stored secret, `token_version` rotation, the owner filters that make another user's task invisible); the shared development stack (R-ISO and the liveness proof); one existing image (Dockerfile COPY). Earns: the concurrency checks (each window has a test on real PostgreSQL: A4b, A7, A8, A3b), authorization (A3a, A3b), the migration rollout order (A5: one migration, advisory lock, upgrade and downgrade on an empty database, a concurrent race; no data to migrate), the architecture check (no new process; the seed is a new entry point, X12), and the shared-stack safety of A5 and A6. No money, no on-chain writes, no existing data.

## 7. Owner acceptance (D6)

The census of stored rows found none: the development database `taskbot` has no tables of its own (`information_schema.tables` with `table_schema = 'public'`: 0 rows, 2026-10-24). The rows below are synthetic messages (no real chat content), chosen for the cases most likely to surprise. They show what S2 does once the bot (S4) and the dashboard (S7) call it; before S2 nothing happens to any of them.

| # | Message text | Result after S2 (title / description, or refusal) | Why |
|---|---|---|---|
| R1 | `Купить молоко` | `Купить молоко` / empty | the whole text fits in the title |
| R2 | `Позвонить маме. Спросить про выходные и купить билеты.` | `Позвонить маме. Спросить про выходные и купить билеты.` / empty | no language rules: the whole text is the title (54 characters) |
| R3 | a three-line list `- купить молоко` / `- позвонить маме` / `- оплатить счёт` | one task: `- купить молоко - позвонить маме - оплатить счёт` / the list as written | without the model there is exactly one task; Claude (S9) makes three |
| R4 | `Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по регионам, сравнением с прошлым годом и прогнозом на следующий квартал. Отправить Анне.` | title `Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по регионам, сравнением с прошлым годом…` / description: the whole text | the 120-character cap; nothing is lost |
| R5 | `👍` | refused, no row | S2-D3 |
| R6 | any text while the model is unavailable | the fallback result of R1–R4, marked `degraded`; the bot adds one line that the model was unavailable | S2-D6 |
| R7 | two text messages, the second newer | the second message's tasks are listed first | S2-D1 |
| R8 | a task moved `Completed → Pending`; then set to `Pending` again | the first change is saved and announced; the second changes nothing | S2-D2 |
| R9 | `yarn dev:seed 1001 "…"` twice | each run prints a new dashboard link; the earlier link stops working | the token rotates (umbrella D-05) |
| R10 | a voice message, then a text sent while the voice is still being transcribed | the text's tasks appear first; the voice's tasks appear above them when the transcript is ready | a message is stored when its text exists (umbrella 3.5); the list is ordered by stored message |

Owner's acceptance, 2026-10-24: "Yes" (Russian original in the designer's diary), given to this table after the first table was redirected by S2-D5 and S2-D6. `backend/tests/acceptance/replay_s2.py` is a read-only script that prints rows R1–R6 from the implemented `MessageText` and fallback extractor (no database, no network, no paid call); R7–R10 are covered by A13, A3, A11 and the ordering rule. With the `claude` extractor (S9) rows R1–R4 change: the model splits them; S9's own acceptance table shows that. The reviewer runs it on the reviewed commit and compares it with this table.

## 8. Risks

- Without the model every message is one task (R2, R3): this is the owner's chosen fallback (S2-D6); the splitting quality lives in S9's prompt.
- The liveness proof relies on PostgreSQL refusing a plain `DROP DATABASE` while a connection exists and on a paused process keeping its socket (both executed in the review). Its failure direction is a leak, never a wrong drop: an idle client left connected to a stale test database protects it until it disconnects; a client host that vanished without closing its socket protects it until TCP keepalive ends the backend (about two hours with the server's defaults). While a sweeper's refused `DROP` waits its 5 s, a new connection to that database waits too; test connect timeouts stay above 10 s.
- Shared development database: a later stage that adds a migration moves the shared `taskbot` database to a head that other worktrees do not know, and their `yarn db:migrate` then fails with "Can't locate revision". S2 puts every umbrella column in its one migration so this is rare; `backend/AGENTS.md` states that a stage adding a migration says so in its diary.
- One event loop for the whole test session: a test that leaves a task running can disturb the next one; concurrency tests use `asyncio.TaskGroup`, which cancels siblings on failure.
- `requeue` has no "not before" time (3.3): a stray copy can use the next attempt earlier than the back-off schedule.

## 9. Departures from the umbrella text

The umbrella is gate-stamped, so S2 does not edit it. These points supersede its text for S2 and later stages; the lead carries them into the next umbrella amendment.

| # | Umbrella text | Replaced by | Why |
|---|---|---|---|
| X1 | `TaskExtractor` returns `ExtractedTasks` carrying `extractor` and `degraded`, and S9 passes `ExtractedTasks` as the model's `output_format` (3.4) | the port returns `Extraction(tasks, extractor, degraded)`; `ExtractedTasks(tasks)` is only the model-facing schema | as one class, the model's schema would require the adapter's bookkeeping fields (executed in exploration) |
| X2 | the event models belong to S6 (5, S6 scope) | S2 defines `TaskView` and the three events in `domain`; S6 only exposes them in the OpenAPI document | S3's publisher (infrastructure) must serialise them and may not import application or entry points |
| X3 | retry: "the use case returns `RetryLater` and the entry point enqueues the same job again" (3.6) | the use case first writes `requeue` (`processing → queued`, attempt-guarded) in its unit of work | with the row left `processing`, the 2–16 s retry meets a live lease and waits about 450 s (executed); the 3.5 diagram already draws this edge |
| X4 | `exhaust`: `WHERE … status IN ('queued','processing') AND attempts >= 5` (3.5); `last_enqueued_at` nullable (3.5) | `exhaust` also requires `status = 'queued' OR claimed_at < now − lease`; `last_enqueued_at` is `NOT NULL`, set at insert and by every terminal write | the umbrella's `exhaust` ends a live fifth attempt and loses its success (executed); a NULL `last_enqueued_at` is never swept, and an unstamped terminal row is re-notified too early |
| X5 | the sweep re-enqueues a `processing` row whose lease expired (3.6) | also only when `last_enqueued_at` is older than 460 s, stamped in the same statement | otherwise every 60 s sweep adds a copy while the transcribe worker is down |
| X6 | `claim … RETURNING attempts` and an untyped "follow-up read" (3.5) | `claim` returns a `ClaimOutcome` with the whole row; the follow-up read is the pure `classify_claim_refusal` (3.3) | the worker needs the row's file id, chat and message ids; the outcome names the four refusal cases as types |
| X7 | `now()` in the claim and sweep SQL (3.5, 3.6) | every compared time is the `Clock` port's aware UTC value bound as a parameter | `now()` is frozen at transaction start, and lease tests would need real waiting |
| X8 | the sweep's queries and the notification counters belong to S5 by implication (5) | S2 builds the repository statements (`stamp_*`, `begin_notification`, `defer_notification`, `finish_notification`); S5 builds `RecoverStuckVoiceJobs` and `NotifyUser` | the `VoiceJobRepository` is an S2 port; S5, the largest stage, would otherwise edit an S2 adapter in parallel with other stages |
| X9 | the notification gives up on "the fifth failure"; the sweep re-notifies un-notified rows with `notify_attempts < 5` (3.6); `voice_jobs` has no notification due time (3.5) | `begin_notification` counts at the start, stamps `last_enqueued_at` and returns a `NotificationStart`; the sweep has no `notify_attempts` filter; `NotificationCeilingReached` ends the row `given_up`; a new column `notify_not_before`, written by `defer_notification` on `RetryAfterError`, holds both the sweep and `begin_notification` until it passes | a crash during the fifth attempt would otherwise leave a row that nothing ever ends (executed); without the stamp the sweep sends a parallel copy while the call runs; without the due time a 900 s `retry_after` spends all five attempts at 1, 180, 360, 540 and 720 s and ends `given_up` before the retry is allowed (executed by the design gate) |
| X10 | `extraction/ (claude, heuristic, fake)` under infrastructure (3.2) | the recording extractor fake lives in `backend/tests/fakes/`; S5's transcriber `fake` stays a production adapter | the extractor fake is test-only; it would otherwise ship in the image |
| X11 | S1 section 6 and the umbrella's R-ISO text before amendment 1: stale cleanup by age with `DROP … WITH (FORCE)` | age picks candidates; a plain `DROP DATABASE` that PostgreSQL refuses while a connection exists is the liveness proof (3.7); `FORCE` only for a session's own database. S2.3 adds a note to the S1 design's status header that its section 6 item 1 is superseded in part by this section | umbrella section 12 requires a liveness proof; `FORCE` would end a paused live session |
| X12 | entry points are `bot/`, `worker/`, `api/` (3.2) | plus `entrypoints/seed/` for `yarn dev:seed` | umbrella section 4 asks for the seed command; its composition root belongs in `entrypoints` (the only layer that may bind adapters) |
| X13 | D-10 and 3.4: the `heuristic` extractor splits the first sentence into the title and the rest into the description | the fallback has no language rules: the text on one line, cut to 120 characters, is the title, and the whole text is the description (S2-D6); splitting is only the model's, with a cached system prompt and rules (S2-D5, carried into S9) | the owner's decision, 2026-10-24 |

## 10. Pre-handoff review record

Five lenses ran on round 1 on 2026-10-24 on `claude-sonnet-5-5`: coherence; premise verification in three groups (SQL and libraries, the repository and its tools, the R-ISO mechanism), each executed against PostgreSQL 18.6 on throwaway `taskbot_test_*` databases that were dropped afterwards; adversarial. Artifacts: `.tasks/artifacts/2026-10-24-s2/{coherence,premise-sql,premise-repo,premise-riso,adversarial}.md` (local).

Folded in:
- The heuristic was not total (HIGH, adversarial, executed with a fuzz of 200,000 strings: titles over 120 characters when no space exists, empty titles for `. Hello` or a lone marker). → The contract "exactly one valid draft, never raises", the hard cut, the title fallback, the first line with a letter or digit, A2's new rows and a seeded fuzz test (3.6).
- Only the voice-job repository had a method table (HIGH, coherence). → The port table of 3.5 with every method, argument, return and refusal result, the unit-of-work attributes and `commit()`'s type.
- The un-notified sweep could never end a row left at five attempts (MED, two lenses, executed), and a notification attempt waiting out `retry_after` invited a parallel sweep copy (MED). → No `notify_attempts` filter; `begin_notification` stamps `last_enqueued_at`; two A7 cases (3.3, X9).
- The R-ISO sweep failed when two sessions raced for one stale database (MED, executed: `3D000`), had no other error rule, leaked the session's own database on a failing migration, cost 5 s per refused drop, and its A6 test raced with foreign sweeps. → `3D000` and `55006` handled, other errors raised, `try`/`finally` around creation, `pg_stat_activity` pre-skip, injectable `now` and `names`, `re.fullmatch`, identifier quoting, raw asyncpg on the maintenance database (3.7, A6).
- A5 needed databases other than the session one, `alembic check` cannot see `CHECK` constraints or partial-index predicates (executed), and a destructive helper could reach the development database. → `test_database` for migration tests, an introspection test, `make_alembic_config` refusing non-test names, the unchanged-development-database check, `%(here)s` in `script_location`.
- The contract suite had no home that runs both legs (MED, executed). → `tests/contract/` with a marked PostgreSQL leg; hooks moved to `tests/conftest.py`.
- Cyrillic text trips `RUF001`–`RUF003`, and `backend/alembic/` breaks the isort order (MED/LOW, executed; the fix re-measured by the designer). → Ruff `src`, `known-first-party`, per-file ignores (3.7).
- Acceptance gaps (MED): A4's "two equal results" was false (executed) → reworded; S2-D1 had no item → A13; A3 lacked the changed path and PostgreSQL authorization cases → A3a/A3b; "integration tests for every repository" was weakened → every port method has a contract case.
- `ChangeTaskStatus` could commit an earlier `updated_at` than the previous change (MED, executed). → Clock read after the row lock and `max` in the entity.
- Smaller items: `VARCHAR(32)` and constraint names need `length` and `name` (executed); `RETURNING` must name table columns to keep the identity map empty (executed); the typed `xmax` spelling (executed); the loop-scope reason (executed); text cleaning of CR and other breaks, `display_name` and drafts; `DuplicateMessage(message_id)` and `set_ack_message_id -> bool`; seed ids in microseconds and its duplicate branch; A11 on a throwaway database; A12's observation; the long row's literal input; the voice-ordering row shown to the owner; settings files, the `+asyncpg` scheme rule, the engine factory's signature; the defensive `exhaust` branch; renamed `NotificationJob.status`, `complete(inbound_message_id)`, `drop_abandoned_databases` and the departure ids (X instead of D); X11's supersession note and X12.

Round 3 (owner, after the first D6 table): the sentence and abbreviation rules of the heuristic were removed (S2-D5, S2-D6, X13); the fallback of 3.6 replaces them, and the D6 table was redone. The heuristic's A2 rows and the Cyrillic ignore for the extractor module left with them.

Round 4 (design gate round 1, Codex, blocked; probes in `.tasks/artifacts/2026-10-24-s2/gate-r1-probes.py`, local):
- G1 [HIGH|confirmed]: with a 900 s `retry_after`, the sweep started attempts at 180, 360, 540 and 720 s and the row ended `given_up` before the retry at 901 s. → `notify_not_before` with `defer_notification`; `begin_notification` and `stamp_unnotified` wait for it without counting an attempt; `NotificationStart` and `classify_notification_refusal` so S5 can tell "not due" from "ceiling"; crash recovery during the fifth attempt kept; A1 and A7 deferral cases; schema, port table and X9 (3.3, 3.4, 3.5).
- G2 [MED|confirmed]: `max(previous, now)` gave two changes the same `updated_at` when the clock repeated, so S7 could not order them. → `max(now, previous + 1 µs)` for a real change, the no-op unchanged; S7's rule written out; same-instant and regressing-clock cases in A1, A3a and A3b (3.2, 3.6).

Rejected: a "not before" column for `requeue` (accepted as a risk, section 8: the ceiling still bounds attempts); categories `L*` and `Nd` instead of `str.isalnum` (the owner's rule is "a letter or a digit", and numeric signs such as `²` are rare and harmless).
