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

The separate seed_keep_token path registers the user, then reads get_token_version
through a unit of work before task creation. A zero version refuses with DomainError;
the upsert may create the user row, but no task or event is created. An existing
link permits standalone task creation and publication of its committed events only.
A duplicate publishes nothing. KeptTokenSeedResult has no token or link fields.
The CLI accepts --keep-token before or after the Telegram user id and uses a separate
run_keep_token runner. Both runners share resource ownership through open_seed_ports.
The kept-token output contains titles, the channel and a token-preservation notice.
It never prints a token or dashboard link, and a refusal exits with status 2.

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
The delivered Compose project defaults to taskbot-app and publishes only web.
The infrastructure launcher selects taskbot explicitly and adds docker/dev-infra.ports.yml.
For that shared project it uses scripts/empty.env and removes shell POSTGRES_* variables,
so a copied or customized dotenv file cannot change the shared database credentials.
An isolated taskbot-* project keeps its dotenv credentials and requires distinct ports.
The launcher refuses taskbot-app because that name belongs to the delivered application.
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

docker/backend.Dockerfile packages every Python entry point in one non-root image.
Python and uv use version tags and immutable index digests. Its default API command
sets --timeout-graceful-shutdown 2; Compose overrides the command for other roles.
Every backend container uses init: true, so Celery's recorded main PID exceeds 1
and a fatal pool-child initialization can stop it through the existing safe guard.
The transcription default is openai; a missing key refuses voice without an SDK call.
Local whisper development explicitly selects whisper_server. The delivered optional
whisper image selects a native architecture base by index digest and verifies its
base model download before renaming the partial file. It runs as uid 10001 with
conversion files under /tmp and the model under /models.

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
Call the queue's synchronous close() before child shutdown closes loop-owned clients.
It waits for running sends and cancels executor work that has not started.
close() then logs "Producer threads drained and closed". Cancellation
of an enqueue awaiter does not stop an already-running broker operation. Broker socket
timeouts and bounded retries limit those operations; queued sends add executor batches.

## Bot runtime

The private /tasks command registers the sender through RegisterOrGetUser and
calls ListCurrentTasks, which composes the canonical ListTasks twice. Show at
most 15 titles: In progress first, then Pending, newest message first per group.
Completed tasks are omitted. All three units of work close before the reply.
The 120-code-point title bound keeps the worst-case reply at 3726 UTF-16 units.
Keep the copy and formatter in application/replies.py; listing never rotates
the dashboard token or publishes an event. The two reads are separate snapshots.
Register BOT_COMMANDS once after getMe/deleteWebhook with the all-private-chats
scope and request_timeout=10. Menu failures log exception classes and preserve
polling; cancellation propagates. tests/acceptance/replay_s10.py pins T1-T10;
test_bot_runtime.py covers menu startup (T11) against isolated PostgreSQL/Redis.

Voice acceptance lives in application/accept_voice.py. It commits the job before
acknowledging, stores the acknowledgement in a second unit of work, then enqueues
and stamps it in a third. No transaction spans Telegram or the broker. Metadata
failures after acceptance are logged safely; the recovery sweep owns failed sends.
Bot and notification replies share application/replies.py. The seed and bot use
dashboard_link for the fragment URL. Telegram fakes reject calls inside a unit of
work using a task-local context variable.

BotSettings requires a validated Telegram token and reuses web and transcription
settings mixins. PollingLock uses owner tokens, namespaced keys and bounded calls.
Start LockKeeper immediately after acquisition, including slow startup; renew on
fixed monotonic slots and fail closed before the lease expires. Register token-guarded
release before acquisition, so a cancelled SET reply cannot strand the key.
HeartbeatTask has its own task and writes only after successful polling. Its Lua guards prevent an
old owner from writing or deleting a successor's heartbeat. Shutdown stops both
tasks before shared-timeout heartbeat cleanup and token-checked lock release.

Telegram adapters live in infrastructure/telegram. The notifier maps all aiogram
and other ordinary failures to safe domain errors. Each adapter attaches no
exception context of its own; a caller's active exception may remain as context.
Cancellation always propagates; replies explicitly disable inherited parse modes.
Only an edit's not-modified response is success; a gone edit target maps to
MessageGoneError. The voice source caps declared and streamed bytes under one
30-second deadline. No Telegram adapter logs raw exceptions or response bodies.

Root yarn dev:bot runs entrypoints/bot. The process owns one engine, one named
Redis client, one extractor with an eight-second deadline, one Celery producer
and one Telegram session. Register every resource immediately with AsyncExitStack.
No client is created per message. Check PostgreSQL before any Telegram request.
Renew the polling lock from acquisition; a startup hook also renews it before
the first poll, so a lost key cannot enter polling between startup steps.

Register sender and private-chat guards before command/text/voice handlers.
Use separate F.text and Command filter arguments; their bitwise conjunction is
not a valid aiogram filter. Ignore edited updates and captions. All replies use
UserNotifier; the typing action alone calls aiogram directly with a two-second
timeout. The error hook logs class names and update ids only and never raises.

InFlightUpdates tracks each handler until its task completes, including the
error hook. Stop polling before draining, keep the Telegram session open during
the drain, then cancel and await unfinished handlers after 15 seconds or the
second signal. The process owns SIGINT/SIGTERM instead of aiogram. Supervise the
keeper and heartbeat through startup and polling. BotStartupError is the single
startup failure path; phases are an Enum. Only the owned startup task is cancelled
by a startup stop, so an external cancellation always propagates. PollingWatch
measures failed or overdue getUpdates requests; semaphore back-pressure stays healthy.
External cancellation stops aiogram polling before cancelling the owned task.
TokenRedactionFilter belongs on every root logging handler.

The offline replay is tests/acceptance/replay_s4.py with TASKBOT_ENV_FILE=/dev/null.
Its frozen B1-B19 expectations are s4_expected.json; B19b and B19c separately check
text and voice with B19's keyless settings. tests/bot.py owns the shared
synthetic settings, update builder and handler/voice fixtures.
Use task_created_text for degraded success replies and worker notification edits.
Cold bot CLI/collection/replay subprocess checks allow 120 seconds for SDK imports.
The held-lock subprocess fixture renews its lease during that startup window.
tests/bot_runtime.py provides root spies over real PostgreSQL, Redis and the
producer, with a fake Telegram transport and extractor. Root integration tests
use per-session databases and namespaces, never development rows. A live check
uses separate test credentials typed into this worktree's untracked dotenv file;
record the result in the README.

tests/integration/test_end_to_end.py joins that bot harness to real Celery workers,
PostgreSQL, Redis and the HTTP/SSE API. Only Telegram and provider calls use doubles.
All processes share the bot harness's namespace. It checks both text and voice
creation, notification, task events and authenticated task reads. Runtime effect
waits use tests/processes.py's cold-start budget; ETA restart tests observe scheduled
work before stopping the worker. docs/development.md lists load-sensitive tests;
the list explains failures and never removes them from the final verification.

## Worker runtime

TranscribeWorkerSettings declares provider and extractor settings separately from
notify-role WorkerSettings. Both reuse the bot's Telegram token shape validator.
Transcriber.transcribe takes audio bytes; each adapter owns its timeout. The bot
checks Telegram's declared duration. The worker caps downloaded bytes and sends
them to the provider without measuring duration or inspecting audio framing
(S5-D9). Provider responses classify unreadable audio. MessageText.bounded caps
stored transcripts at 4096 characters. Worker notification copy lives in
application/replies.py. S5 adds no migration; the head stays 0003. The shared SQL
engine hides bound parameters.

Open transcription clients through infrastructure/transcription/factory.py's
open_transcriber. The factory owns and closes the selected client; fake and
keyless OpenAI branches do not import the SDK. Adapters classify final versus
transient errors, own their wall-clock deadlines, disable retries and redirects,
and propagate cancellation/soft interruption. Whisper non-speech markers alone
are empty; ordinary fluent hallucinations are not filtered. Provider clients and
loggers must never expose transcript text.

TranscribeVoiceJob composes claim, slow preparation and guarded atomic completion
as separate steps. Pass AttemptProgress for the synchronous soft-limit backstop.
NotifyUser reads terminal state and text inputs before its Telegram call; the
stored row controls the result text. RecoverStuckVoiceJobs commits all sweep
stamps before enqueueing. Database failures escape transcription mapping; queue
failures are logged safely and recovered by sweep. Extend existing service
fakes instead of duplicating them; their unit-of-work guards enforce the boundary.
NotificationJob.from_voice_job is the canonical terminal-state mapping for full
jobs and the sweep's UnnotifiedJob projection. Nonterminal inputs are refused.

Root yarn dev:worker transcribe / notify runs entrypoints/worker through the role
launcher. It execs Celery; notify also owns beat. Role selection reads the first
argument or process WORKER_ROLE, never dotenv. BrokerSettings is connection-free;
WorkerSettings requires role and DATABASE_URL with an optional Telegram token;
TranscribeWorkerSettings adds the provider and extractor settings. Notify ignores
these settings. Startup validation prints variable names without input
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
soon as it is built; async build_container enters the extractor and transcription
contexts on that loop. Notify builds neither. A builder failure leaves partial resources reachable for
cleanup. start_child catches BaseException, closes resources independently,
signals only its still-recorded actual parent, then always os._exit(70). Tasks use
runtime.run_async; interrupted coroutines are cancelled and drained before reuse.
Shutdown closes the job queue synchronously before Redis and the SQL engine.
Keep aiogram imports inside configured Telegram composition. The shared token
shape check matches the SDK without importing its model graph. Neither launcher
pass imports aiogram, even when a token is configured; the owned child builds Bot.

Transcription, notification and sweep tasks validate keyword payloads and call
the corresponding application use cases. Transcription has a 400-second attempt
budget, then Celery soft/hard limits of 420/450 seconds. Notification and sweep
limits are 60/90 seconds. OpenAI has a 30-second adapter deadline; whisper-server
has 300 seconds. The error taxonomy is in docs/plans/2026-10-27-s5-worker.md.
Task errors log class names only: PostgreSQL server errors can contain source text
even when bound parameters are hidden. Notify enqueues a sweep at startup and
beat repeats it every 60 seconds, with a 60-second message expiry.
Successful task bodies log result class and failure-kind/notification-outcome
codes at INFO, never result representations or source text.

Use tests/workers.py for real-worker tests. Every worker needs its own test child
namespace, /dev/null dotenv and no Telegram or inherited provider credentials.
The default database URL has a closed port; S5 tests pass only an owned test
database. The harness validates its name. Defaults explicitly select fake
transcription and heuristic extraction. Test-only hooks/tasks stay in
tests/worker_tasks.py and tests/worker_s5_hooks.py, included explicitly. The S5
hooks record receipts, return the synthetic fixture and inject child crashes;
database, Redis and queue clients remain real. Worker
output goes to owned files. Teardown sends SIGTERM only to the main PID, allows
warm shutdown, records leftover members before cleanup, and rejects any forced stop.
It kills residual owned groups in a finalizer. Beat runs in a private temporary directory;
verify its namespaced schedule in the nested per-user directory while it runs,
then remove the harness's entire private temporary root after all children exit.
Never run test workers against unnamespaced shared development queues.
Root scripts/stack-acceptance.mjs verifies Linux prefork in an owned Compose project,
including real child tasks, init layout, fatal-child automatic restart and warm shutdown.
It mounts tests read-only for one-off probes only. tests/stack_worker_probe.py records
the pool child's actual parent, recorded main PID, init name and uid without secrets.
Production images contain no test hooks. The normal health checks remain unchanged.

The offline voice replay is tests/acceptance/replay_s5.py with
TASKBOT_ENV_FILE=/dev/null. Its frozen V1-V12 expectations are s5_expected.json.
It uses real use cases with synthetic service doubles;
provider factories and socket connections are forbidden during replay.

The opt-in live probe is root yarn live:transcription --provider
whisper_server|openai [--file path]. It reports synthetic transcript and timing;
each OpenAI invocation is one paid call. TranscriberSettingsMixin owns provider
client settings shared by the worker and probe. Bot settings retain only provider
selection/key fields. The probe needs no database or Telegram configuration.
tests/live/test_whisper_worker.py exercises the real whisper worker with recording
Telegram adapters. pytest deselects live_whisper unless its marker is named in -m;
ordinary verify must not call a live provider. Owner-entered credentials stay in
this worktree's untracked .env. Record actual live results before closing S5.5.

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
