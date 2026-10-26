# Backend

One Python 3.14 package serves the bot, workers and FastAPI API.
Use `uv` from the root or `uv --directory backend`; never bare pip.

## Layers

Voice acceptance lives in application/accept_voice.py. It commits the job before
acknowledging, stores the acknowledgement in a second unit of work, then enqueues
and stamps it in a third. No transaction spans Telegram or the broker. Metadata
failures after acceptance are logged safely; the recovery sweep owns failed sends.
Bot and notification replies share application/replies.py. The seed and bot use
dashboard_link for the fragment URL. Telegram fakes reject calls inside a unit of
work using a task-local context variable.

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
The heuristic extractor keeps one task without language rules. ClaudeExtractor returns
one validated TaskDraft or the heuristic marked degraded on a provider/parse failure.
Storage positions remain fixed by migration 0001;
each new message creates its task at position 0.

Open extractors through the async open_extractor context manager in the composition
root; it owns and closes the Claude client on that root's event loop. Heuristic and
keyless branches never import the SDK. Roots use code constants for deadlines:
8 seconds in S4's bot, 30 in S5's worker and 30 in the seed. S5 must pass
interrupts=(SoftTimeLimitExceeded,) so worker interruption propagates even when
the SDK wraps it. Task cancellation always propagates. Requests disable SDK retries
and pin the Anthropic base URL. Logs contain safe reason codes, usage and latency;
keep anthropic and httpx2 loggers at WARNING after SDK import to protect chat text.
The owned HTTP client refuses redirects, so a response cannot forward source text
to another host. Suppress Pydantic serializer UserWarnings process-wide after SDK
import; those warnings can include response content outside the SDK loggers.
Interrupt propagation excludes exceptions already handled by the caller before
extract starts, while preserving exception chains created inside the current call.

The English system rules and four synthetic examples live in extraction/prompt.py.
Keep the prompt at least 2600 characters for the cache prefix. Check prompt changes
with the owner-controlled live replay; examples are not the acceptance messages.
Run it from root with yarn live:extraction --thinking disabled|adaptive, one mode
per process. It makes eight paid calls, checks fixed A1-A8 facts and cache hits,
and prints usage/latency. Gist and added facts need manual review. Normal verify
only runs its offline checker/configuration tests and never calls the provider.
Fact checks examine title and description separately, retain accepted word-stem
suffixes and reject numeric supersets with the replay's token-boundary rules.

Test service doubles live in tests/fakes. The read-only owner-row replay is
`uv run --locked --directory backend python tests/acceptance/replay_s2.py` from root.

Application use cases receive ports. Extract before opening a unit of work.
Composable within methods record events but never commit or return them; standalone
execute results carry the events returned by commit. Publish only after commit with
publish_events; publication failures are logged without undoing persisted work.
CreateTasksFromMessage.within returns CreatedMessage or DuplicateMessage; standalone
execute returns TasksCreated (which adds events) or DuplicateMessage. S5 composes the
within result with voice-job completion in one unit of work.
TaskView.from_task(task, source_text) in domain/events.py builds the shared wire
shape. Task events include the stored source message text; entities do not import
wire models. TaskRepository.page_for_user joins that text and orders by descending
message id, then ascending position. ListTasks fetches one extra row and returns
a cursor only when another page exists. Status changes return a TaskChange.view
even when the requested status is unchanged.
DeleteTask locks the owner-filtered task, deletes it and erases source text only
when no task of that message remains, in one transaction. The retained message
row prevents duplicate deliveries from recreating deleted tasks. Publish
task.deleted only after commit. A task with NULL source text is an invariant
failure in status changes and listing, not an empty tooltip.
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
Task descriptions now allow 4096 characters (revision 0002); apply migrations
before any process writes longer descriptions. Downgrading to 0001 requires no
stored description over 2000 characters. Tests migrate only owned databases;
they never advance the development head.
Revision 0003 permits NULL message text after deletion. Its downgrade fills NULL
with an empty string before restoring NOT NULL, preserving the duplicate guard.
Migrate through 0003 before running the deletion API.

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
SeedSettings, SQL units of work, clock, extractor and token adapters. The CLI owns
the extractor, Redis client and engine; register cleanup immediately after construction.
AsyncExitStack closes the extractor, Redis and engine in that order and attempts all
cleanup even if one fails. build_seed_ports receives the opened extractor explicitly;
integration tests inject HeuristicExtractor so shell configuration cannot call Claude.
The CLI constructs RedisEventPublisher and passes it as build_seed_ports's explicit
publisher keyword; SeedPorts contains no owned client. The reusable seed function
invokes standalone task creation and token issue, then publish_events once after
both commits. Duplicate messages still rotate the token and publish auth.revoked.
Publication failure preserves the tasks, token and link and logs one failed batch.

SeedResult.user_id comes from the token issue result's authoritative internal user
id. Print its namespaced user_channel, never the Telegram id. CLI message ids are
negative microsecond timestamps to avoid real Telegram ids. The CLI validates user
ids as positive signed int64 values before it loads settings. Use load_settings to
report configuration errors without echoing secret inputs. Print tokens only in
this explicit development output; result representations hide token/link.

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
prefixes for keys, channels, locks and Celery queues and removes its own keys.
The redis_session fixture owns its namespace and returns a fresh child string per
writing test through redis_session.child(). Cleanup scans only <session-id>:* in
both application and broker databases. A same-host owner record and a process-held
file lock prove abandoned ownership. Missing records, corrupt records, other hosts
and missing/noncanonical lock paths retain keys; they require manual owner cleanup.
Use SCAN MATCH taskbot_test_* to inspect retained records; age alone never permits
deletion. No test assumes exclusive use. S1's tests only read.

## API schema and image

The API factory registers routes without reading environment variables. Production
lifespan validates ApiSettings and owns its SQL engine and Redis client. Injected
ApiServices attach immediately, so ASGITransport tests need no lifespan runner.
Every task/ticket route authenticates a bearer token; /health is public and
/api/events authenticates a single-use ticket. Subscribe and recheck the database
before stream headers. The request-scoped dependency owns subscription cleanup.
SSE data carries the domain envelope with no event field; comments keep it alive.
Task pages use opaque cursors and an optional status filter. Publish status events
only after the use case commits. Keep the dev server's graceful shutdown timeout
so open streams cannot block reload or Ctrl-C.
DELETE returns 204 with no body or content type. The read-only acceptance replay
checks W1-W8 with the heuristic and in-memory ports. It needs no database, Redis,
Telegram or extraction credentials:

```sh
uv run --locked --directory backend python tests/acceptance/replay_s6.py
```

Change Pydantic response models, then run root `yarn api:schema`; generated
TypeScript and Zod files are never edited by hand. `yarn api:check` detects drift.
Route names become OpenAPI operation ids; choose unique names.
A stage that adds dependencies or a top-level backend directory, such as
`alembic/`, reruns the backend smoke build and records its result.

## Redis adapters

Telegram adapters live in infrastructure/telegram. The notifier maps all aiogram
and transport failures to the domain notifier errors without exception chaining.
Only an edit's not-modified response is success; a gone edit target maps to
MessageGoneError. The voice source caps declared and streamed bytes under one
30-second deadline. No Telegram adapter logs raw exceptions or response bodies.

Build asynchronous clients through infrastructure/redis/client.py. Names use namespaced
or user_channel; event routing uses internal users.id. RedisEventPublisher bounds a
publish to one second. publish_events stops a batch after its first publication failure.
RedisTicketStore issues 30-second, single-use tickets with SET NX EX and GETDEL;
ConsumeRealtimeTicket also checks the current database token version without a commit.

subscribe_user_events waits for Redis's subscription confirmation before yielding.
UserEventSubscription.next returns raw event JSON or None after a healthy idle check.
EventStreamGapError identifies a reconnect; Redis/timeout failures in adapter operations
become EventStreamError. Consumer-body exceptions retain their identity. Exit cleanup
survives repeated cancellation within its operation timeouts, then propagates cancellation.
A consumer closes its stream and refetches state after either stream error.
Events received during the idle PING remain deliverable. EventStreamError and
EventStreamGapError live in domain/errors.py and remain re-exported by the adapter.
RedisEventSubscriber implements the domain subscription port. StreamUserEvents
checks token_version after subscription confirmation and every 20 monotonic
seconds, including under traffic. Each database check has a five-second timeout;
post-start failures close the stream, and cancellation propagates after cleanup.
The API frames the resulting items as SSE.

RedisSettingsMixin supplies application/broker URLs and a validated test namespace.
SeedSettings includes this mixin. The read-only owner replay is
`uv run --locked --directory backend python tests/acceptance/replay_s3.py` from root.
It verifies optional tokens, refused worker roles and the empty-namespace channel
rule. It starts no worker and connects to no store; live seed tests prove publication.
TASKBOT_ENV_FILE overrides the root dotenv file at settings construction; explicit
_env_file=None still disables dotenv loading for in-process tests.

Derived broker URLs use Redis database 1 and keep only ssl_* query options,
preserving TLS certificate policy; redis-py client options and fragments are dropped.
An explicit CELERY_BROKER_URL is used unchanged.

## Celery producer

create_celery_app builds connection-free shared configuration and routes from the
domain job classes. CeleryJobQueue serializes those models as JSON and publishes on
its dedicated four-thread executor. It resolves producer_pool before threaded use.
Negative countdowns are refused; countdowns above 600 seconds are clamped and logged
at debug. OperationalError becomes EnqueueError with its cause preserved.
Call the queue's synchronous close() before child shutdown closes loop-owned clients;
it waits for running sends and cancels executor work that has not started. Cancellation
of an enqueue awaiter does not stop an already-running broker operation. Broker socket
timeouts and bounded retries limit those operations; queued sends add executor batches.

## Worker runtime

Root yarn dev:worker transcribe / notify runs entrypoints/worker through the role
launcher. It execs Celery; notify also owns beat. Role selection reads the first
argument or process WORKER_ROLE, never dotenv. BrokerSettings is connection-free;
WorkerSettings requires role and DATABASE_URL, with no provider settings and an
optional Telegram token. Startup validation prints variable names without input
values. On macOS the launcher adds FORKED_BY_MULTIPROCESSING=1; direct prefork
without it is refused.

Notify stores its beat schedule in taskbot-<uid> under the system temporary
directory. Before exec, the launcher creates it with mode 0700 or checks that
it is an owned directory with no group or other access; symlinks are refused.
Beat unpickles this schedule, so only the worker's OS user may write it.
The application keeps the directory across restarts. Transcribe creates none.
Startup logs mask broker passwords and omit the URL query and fragment.

The main process records its actual PID and connects no clients. Each pool child
owns one event loop and WorkerContainer. Register each resource in that owner as
soon as it is built; a builder failure must leave partial resources reachable for
cleanup. start_child catches BaseException, closes resources independently,
signals only its still-recorded actual parent, then always os._exit(70). Tasks use
runtime.run_async; interrupted coroutines are cancelled and drained before reuse.
Shutdown closes the job queue synchronously before Redis and the SQL engine.

Use tests/workers.py for real-worker tests. Every worker needs its own test child
namespace, /dev/null dotenv, no Telegram token and a closed-port fake database URL.
Test-only hooks/tasks stay in tests/worker_tasks.py, included explicitly. Worker
output goes to owned files. Teardown sends SIGTERM only to the main PID, allows
warm shutdown, records leftover members before cleanup, and rejects any forced stop.
It kills residual owned groups in a finalizer. Beat runs in a private temporary directory;
verify its namespaced schedule in the nested per-user directory while it runs,
then remove the harness's entire private temporary root after all children exit.
Never run an unfinished worker against the
unnamespaced shared development queues; without S5 handlers it discards real jobs.
S8 owns Linux prefork, Compose delivery and restart: unless-stopped verification.

Celery 5.6 embedded Beat protects sys.__stdin__ when closing inherited descriptors,
but Billiard first replaces sys.stdin. The worker-only compat fixup registers a
public after-fork callback that aligns those wrappers in Beat before descriptor
closure. Celery serializes the fixup for spawned apps. Pool children and producer
apps do not change their streams. Keep completed notify TERM/Ctrl-C logs free of
bad-descriptor finalization errors; remove this compatibility fix when the pinned
SDKs preserve the live wrapper themselves.

## HTTP boundary details

Runtime documentation routes are disabled; export the committed schema with
yarn api:schema. FastAPI parses malformed JSON before bearer dependencies, so
that combination returns 422 even without a token. Valid requests authenticate
before application work. ApiError documents the common detail-string responses.
Under an unthrottled event flood, FastAPI's keep-alive task can log
BrokenResourceError during client abort; the subscription still closes. Ordinary
traffic does not reproduce this dependency log noise.
