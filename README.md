# AI Task Bot

A task-management application. Users create tasks by sending text or voice messages to a Telegram bot. A background worker transcribes voice messages asynchronously through Redis queues. PostgreSQL stores tasks and users. A responsive web dashboard shows the tasks in real time and lets users move each task between Pending, In Progress and Completed. Docker Compose runs the web application, PostgreSQL, Redis and the worker.

The domain core and PostgreSQL persistence are available, with migrations and a
command to create development tasks. The API serves authenticated task pages,
status changes and real-time events. The dashboard displays a live task board with
desktop columns and phone tabs. The Telegram bot accepts text and queues voice
messages. The worker runtime is available; transcription handlers arrive later.

## Local setup

You need Git, Node 26, Yarn 1.22.22, uv 0.13 or newer, Docker and Compose v2.
`nvm install` reads `.nvmrc`. Node 26 does not bundle Corepack; install Yarn
separately, for example with `npm install -g yarn@1.22.22`.
uv installs Python 3.14 if it is missing. Keep ports 5432, 6379, 8000 and 5173 free,
or use the isolated infrastructure settings below.

```text
clone -> install -> start infrastructure -> verify -> run dev servers
```

From the repository root:

```sh
cp .env.example .env
yarn install --frozen-lockfile
uv sync --locked --directory backend
yarn infra:up
yarn verify
```

`yarn verify` needs PostgreSQL and Redis. With either stopped, it fails with the
service address and `run yarn infra:up`; it never silently skips integration tests.
No API key or Telegram token is needed for the foundation checks.

## Commands

| Command | Purpose |
|---|---|
| `yarn verify` | File size, script tests, Python lint/format/types/layers/tests, schema drift, frontend checks |
| `yarn test:unit` | Python unit tests and frontend tests without infrastructure |
| `yarn verify:backend` / `yarn verify:frontend` | Check one module |
| `yarn check:scripts` / `yarn check:size` | Check the Node tools or source-file length |
| `yarn api:schema` | Export OpenAPI and regenerate TypeScript and Zod together |
| `yarn api:check` | Fail if the schema or generated files are stale |
| `yarn infra:up` / `yarn infra:stop` | Start or stop guarded local PostgreSQL and Redis |
| `yarn dev:api` | Task API with reload on `http://127.0.0.1:8000`; public `GET /health` |
| `yarn dev:web` | Frontend on `http://127.0.0.1:5173`; `/api` proxy preserves the prefix |
| `yarn dev:whisper` | Local whisper.cpp server on `http://127.0.0.1:8178` |
| `yarn db:migrate` | Upgrade the database schema to the latest Alembic revision |
| `yarn --silent dev:seed <telegram_user_id> [text …]` | Create tasks, rotate the dashboard token and publish events |
| `yarn --silent dev:seed --keep-token <telegram_user_id> [text …]` | Create tasks while keeping an existing dashboard link valid |
| `yarn --silent live:extraction --thinking disabled\|adaptive` | Paid, opt-in replay of eight synthetic messages with Claude |
| `yarn dev:worker transcribe` / `yarn dev:worker notify` | Start the prefork worker roles; notify also runs beat |
| `yarn dev:bot` | Telegram bot; requires `TELEGRAM_BOT_TOKEN`; one instance per token |

Never hand-edit `backend/openapi.json` or `frontend/src/api/generated`.
Change the Pydantic models and run `yarn api:schema`. TypeScript types and runtime
Zod schemas have the same source.

## Telegram bot

Apply database migrations, put a separate test bot token in the untracked `.env`,
then run `yarn dev:bot`. The bot validates settings before starting. It checks
PostgreSQL and acquires a Redis polling lock before contacting Telegram. A second
instance exits with the remaining lock lifetime. The bot removes an existing
webhook without discarding pending updates.

Private text creates one task and replies with its title. Task titles retain the
message's language; bot replies are English. `/start` shows the selected providers.
`/dashboard` issues a personal link and invalidates older links. Keep the link
private. Two concurrent requests can deliver the older link last; request another
link if needed. A failed reply after rotation also needs a new `/dashboard`.

A voice message receives an acknowledgement, then enters the transcription queue.
The limits are 120 seconds and 10,000,000 bytes; exact limits pass. Missing size
metadata is checked during download. Voice processing completes when the worker
handlers are available. Failed enqueue attempts leave a recoverable database row.

```text
text  -> extract -> commit task -> reply -> publish event
voice -> commit job -> reply -> store reply id -> enqueue -> stamp
```

Tasks come only from private chats. Commands in groups receive a private-chat
reminder; commands addressed to another bot and other group content are ignored.
Keep Telegram privacy mode enabled to avoid replies to unrelated group commands. Unknown commands receive usage text.
Unsupported attachments receive a text-and-voice reminder. Captions and edits do
not change tasks; forwarded messages belong to the person who forwarded them.

The bot handles at most ten updates concurrently. It has no per-user rate limit,
which assumes a small installation. Ctrl-C or SIGTERM stops polling and allows
up to 15 seconds for active handlers. A second signal cancels the remaining work.
SIGKILL, a drain timeout or an unexpected handler failure can lose a confirmed
update. A crash after a text commit can leave a task without a reply or live
event; refreshing the dashboard recovers its state. A duplicate delivery does not
create another task. Resending as a new message creates a new task.

`TRANSCRIPTION_PROVIDER` selects `whisper_server`, `openai` or `fake`. It never
automatically falls back to another provider. Missing OpenAI credentials are shown
by `/start`; voice messages are still acknowledged. A missing Anthropic key uses
the degraded heuristic and adds a fallback notice to text replies.

The bot renews its polling lock during startup and while draining. It publishes
`heartbeat:bot` only after successful polling, under `REDIS_NAMESPACE` when set.
Loss of ownership or sustained failed polling requests stops the process. Time
spent waiting for handler capacity counts as healthy polling. If all ten handlers
wait indefinitely, for example on database locks, the bot keeps its heartbeat
but consumes no further updates. This version has no timeout for a complete update;
its heartbeat does not detect permanently stuck handlers. Allow at least 30 seconds
for shutdown in the delivered stack; verify the producer drain under the deployment's
broker conditions.

Telegram: verified live on 2026-10-27 with a separate test bot against the local
stack. Private `/start`, two texts, two voice messages, `/dashboard` and a sticker
were each handled in 319-883 ms, from receiving the update to sending the reply; the
voice messages stopped at the acknowledgement, because transcription is a later stage.
SIGINT stopped the bot in about 3 s and released its polling lock. Offline handler,
adapter and lifecycle checks use a fake Telegram transport; integration checks use
real isolated PostgreSQL and Redis.
Run the synthetic acceptance replay without credentials:

```sh
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s4.py
```

## Development tasks

Apply migrations before creating tasks. Both commands read `DATABASE_URL` from
the environment or the root `.env`; it must use `postgresql+asyncpg://`.

```sh
yarn db:migrate
yarn --silent dev:seed 1001 "Call home and ask about the weekend."
```

Without text, the seed uses a built-in English sample. The current fallback keeps
one task per message, cuts its title to 120 characters and keeps longer text in
the description.
The seed stores tasks and prints the token and `<WEB_BASE_URL>/#token=…` link.
Every default run rotates the token, including a duplicate message, so older links stop
working. After both database commits, it publishes one `task.created` per task,
then `auth.revoked`. The printed `Events channel:` uses the stored user's internal
id, which differs from the Telegram id on the command line. A test namespace also
prefixes this channel. Open the printed link in the dashboard. To add tasks while
that dashboard stays signed in, use `--keep-token` after the first default run:

```sh
yarn --silent dev:seed --keep-token 1001 "Prepare the weekly report."
```

This mode publishes only task events and prints no token or link. It refuses a
user who has never had a dashboard link; run the default seed first.

To observe development events, subscribe in another terminal before seeding:

```sh
redis-cli -u redis://localhost:6379/0 PSUBSCRIBE 'events:user:*'
```

Use the configured `REDIS_URL` if it differs from this default. With Redis
unreachable, the seed waits up to about one second, logs one failed publication
batch and still prints the committed token and link. It skips the remaining
events in that batch. The CLI closes its extractor, Redis client and SQL engine on exit.
The seed publisher and root command were verified against real local stores with
synthetic tasks, an owned test database and a private Redis namespace.

## Task dashboard

Run `yarn dev:api` and `yarn dev:web`, then open a seeded dashboard link. The page
stores its token locally and removes the URL fragment. A new link replaces the
session in other tabs. An invalid link asks for a new `/dashboard` link from the bot.

At desktop widths the board shows Pending, In Progress and Completed columns.
Below 768 pixels, tabs show one column at a time. Arrow buttons move a task to a
neighboring status; desktop cards also support dragging between columns. Status
changes appear immediately and are serialized per card. Deletion requires
confirmation and waits for the server; there is no undo.

Long descriptions expand with Show more. Source message opens the original text
in a disclosure block. Each column loads more tasks on request; a `+` after its
count means more tasks remain. Live, Connecting and Reconnecting describe the
stream state. Every reconnect reloads all tasks down to the oldest shown card.
The dev proxy propagates an interrupted API stream so reconnection also works
after an API restart.

The deterministic dashboard replay uses the eight synthetic extraction rows and
checks the implemented board, text, moves, deletion, live arrival and sign-out:

```sh
uv run --locked --directory backend python tests/acceptance/generate_s7_fixture.py
yarn workspace frontend vitest run src/app/replay.test.tsx
```

The fixture comes from the backend's canonical task API; do not edit it by hand.

## Claude extraction

The default `TASK_EXTRACTOR=heuristic` needs no provider key. To use Claude, set
`TASK_EXTRACTOR=claude` and `ANTHROPIC_API_KEY` in the untracked root `.env`.
`ANTHROPIC_MODEL` defaults to `claude-haiku-5-5`. Keep real keys out of git.
Apply `yarn db:migrate` before starting code that writes descriptions longer than
2000 characters; the current limit is 4096, with a 120-character title.

Claude returns one task per message, including lists and greetings. It writes a
short title and a clean description in the source language, preserving facts and
removing filler. The original message stays stored. Provider failures use the
heuristic marked `degraded`; missing or blank keys do the same. Each call has one
attempt and a whole-call deadline. The seed uses 30 seconds; the bot and worker
stages use 8 and 30 seconds respectively. Usage and latency logs exclude chat text.

The owner-controlled replay makes eight paid API calls per command. It reads the
fixed synthetic A1-A8 rows in `backend/tests/acceptance/s9_rows.json`, writes no
database data and is outside `yarn verify`. Run each mode in its own process:

```sh
yarn --silent live:extraction --thinking disabled
yarn --silent live:extraction --thinking adaptive
```

The command refuses missing `TASK_EXTRACTOR=claude` or `ANTHROPIC_API_KEY`. It prints
one JSON line per row with the task, extractor, degraded flag, failure reason,
latency and four usage counts. Usage is `null` when no completed response was
logged; a timeout may still incur provider charges. It checks preserved facts,
filler removal, the injected-title refusal and cache hits after the first row.
Failures name the row and check on stderr and produce a nonzero exit status.
An adaptive-mode deadline fallback is recorded as `degraded: true` with reason
`timeout` and counts as evidence against that mode. Read each result to judge its
gist and confirm that the model added no facts. Prompt changes need this replay.

Live measurements on 26 October 2026 used `claude-haiku-5-5`, the eight synthetic
rows and the bot's 8-second deadline. The first three runs had one cold request
and seven prompt-cache reads; the final run reused the preceding probe's cache
for all eight requests. No run used the fallback. Latency measures the extraction call,
excluding process and SDK startup.

| Prompt / thinking | Mean / median latency | Total cost, 8 calls | Cost per 1,000 at this sample mix | Quality |
|---|---|---|---|---|
| Initial / disabled | 2.350 / 1.809 s | $0.000986890 | $0.123361 | A1–A7 pass; A8 changes uncertainty and an approximate date |
| Initial / adaptive | 2.660 / 1.842 s | $0.001067085 | $0.133386 | A1–A7 pass; A8 has the same defects |
| First correction / disabled | 1.810 / 1.511 s | $0.001031065 | $0.128883 | A1–A7 pass; A8 fails both strengthened checks (exit 1) |
| Final / disabled, warm cache | 1.518 / 1.150 s | $0.000940540 | $0.117568 | A1–A8 pass the strengthened checks and manual reading |

Token counts below are totals across eight messages per run. Divide each total by
eight for mean tokens per message. The input column excludes cache writes and reads.

| Prompt / thinking | Input tokens | Output tokens | Cache-write tokens | Cache-read tokens |
|---|---|---|---|---|
| Initial / disabled | 1,355 | 1,039 | 1,702 | 11,914 |
| Initial / adaptive | 1,355 | 1,199 | 1,703 | 11,921 |
| First correction / disabled | 1,355 | 1,063 | 1,867 | 13,069 |
| Final / disabled, warm cache | 1,355 | 1,212 | 0 | 19,904 |

The initial comparison supports keeping thinking `disabled`: adaptive used more
output tokens and had higher mean latency without fixing A8. These small samples
do not establish general performance. Three further A8-only tuning probes
preceded the final passing replay. One separate A8 diagnostic call also failed
filler removal. The final copyediting prompt preserves uncertainty, approximate
dates and context; manual review confirmed all eight final rows. A speaker's
own correction can remain as a clarification when its meaning is preserved.
Adaptive has not been tested with this final prompt. The 36 recorded calls,
including failed attempts and probes, cost $0.007439830 in total.

The prompt examples were refined after the A8 failures. They use different text
but share A8's uncertainty, approximate-reminder and self-correction patterns.
The final A8 pass therefore provides less evidence of generalisation than A1–A7.

Costs use the [Haiku 5.5 rates](https://platform.claude.com/docs/en/models/haiku-5-5/overview)
and [prompt-cache pricing](https://platform.claude.com/docs/en/about-claude/pricing)
checked on that date: USD per million tokens, $0.10 input, $0.50 output,
$0.125 for a five-minute cache write and $0.01 for a cache read. For each response,
cost is `(input × 0.10 + output × 0.50 + cache_write × 0.125 + cache_read × 0.01)
/ 1,000,000`. The per-1,000 column scales these eight-message samples; it is not a
monthly bill estimate.

## Workers

Run each role in its own terminal. Give the role as the first argument, or set
`WORKER_ROLE` in the process environment. A role in `.env` alone is not used.
Both roles require `DATABASE_URL`; Redis defaults to `redis://localhost:6379/0`
and the broker uses database 1 unless `CELERY_BROKER_URL` overrides it.

```sh
yarn dev:worker transcribe
yarn dev:worker notify
```

The launcher preserves Celery's main PID and sets the required multiprocessing
flag on macOS. On macOS, use this command instead of starting Celery directly.
The startup log shows the role, queue, node, broker with its password hidden,
and Redis namespace. A missing Telegram token produces a warning; no token is
required to start either role. Provider adapters and real job handlers arrive in S5.
Until then, a worker warns that jobs without handlers will be discarded. Use only
isolated test namespaces when testing this unfinished runtime on the shared broker.
Run at most one development worker per role on each machine, from the worktree
whose code should handle the jobs. Workers of one role also share a node name.
`REDIS_NAMESPACE` is for test sessions; leave it empty in normal configuration.

Press Ctrl-C once for warm shutdown. A running task finishes before the worker
stops; the prompt can return a few seconds before that task finishes. A second
Ctrl-C requests cold shutdown. SIGTERM to the main process also finishes running
tasks. Notify keeps its beat schedule in a private `taskbot-<uid>` directory under
the system temporary directory. The launcher creates it with mode `0700` or
refuses an existing directory owned by another user, a symbolic link, or group
or other access. The schedule survives restarts; test schedules use namespaced
names in an owned temporary root that the harness removes. Startup logs mask
broker passwords and omit URL queries and fragments. A broker URL derived from
`REDIS_URL` retains TLS options (`ssl_*`) and drops other client options; an
explicit `CELERY_BROKER_URL` is kept unchanged. Worker tests use real Redis and test-only
`s3test.*` tasks, without Telegram, transcription providers or database connections.
Linux prefork, the delivered Compose stack and `restart: unless-stopped` are
verified in S8.

## Shared infrastructure

The default `taskbot` stack belongs to the machine. All worktrees share it and
its development data. Leave it running when a stage finishes. `yarn infra:stop`
stops containers without removing data; coordinate a stop with other active work.
Do not run `docker compose down` or delete the shared volumes.

Use `yarn infra:up` and `yarn infra:stop`. Calling Compose directly bypasses the
guard. The guard fixes the project, input file and project directory, so native
Compose overrides cannot redirect these commands. Development credentials are
the public value `taskbot`; service ports bind only to `127.0.0.1`.

If those ports are occupied, uncomment `TASKBOT_STACK`, `TASKBOT_PG_PORT` and
`TASKBOT_REDIS_PORT` in `.env`. Choose a `taskbot-<name>` project and two distinct
ports other than 5432/6379. Update `DATABASE_URL` and `REDIS_URL` to match them.
The isolated project has its own containers and volumes. Integration tests create
and migrate a private database for each session, then drop it. Cleanup protects
live test sessions even if they are old. Tests never write to the development
database or assume exclusive use of the shared stack. Each writing Redis test uses
a private session and child namespace for keys, event channels, tickets and locks.
Celery's `global_keyprefix` isolates all broker names; queue names remain
`transcription` and `notification`. Tests set `TASKBOT_ENV_FILE=/dev/null` so they
cannot inherit developer credentials from `.env`.

Redis cleanup requires a valid same-host owner record and an available process
lock. Unknown ownership retains keys, including missing or corrupt records,
foreign hosts and missing lock files. This can leak test data, but cannot delete
another session's data. Inspect retained keys with `SCAN MATCH taskbot_test_*`
in both Redis databases; manual cleanup belongs to the owner.

The read-only S3 replay checks optional worker tokens, refused roles and the
internal-user channel rule. It starts no worker and connects to no store:

```sh
uv run --locked --directory backend python tests/acceptance/replay_s3.py
```

The read-only API replay checks the accepted task scenarios against synthetic
messages. It uses in-memory stores and needs no credentials:

```sh
uv run --locked --directory backend python tests/acceptance/replay_s6.py
```

## Local transcription

For `yarn dev:whisper` only, install whisper.cpp (`brew install whisper-cpp` on
macOS, or build the upstream server on Linux), ffmpeg and a ggml model.
Set `WHISPER_DEV_MODEL_PATH` in `.env` to the model's absolute path.
The launcher also expands a leading `~/`. Existing shell variables win over `.env`.
On macOS, a compatible whisper.cpp installation uses Metal; the server log says
which backend it selected. The development check uses large-v3-turbo.
The synthetic OGG round trip and clean shutdown were verified locally with that
model on Metal.

```sh
yarn dev:whisper
curl -F file=@backend/tests/fixtures/voice-en.ogg -F response_format=json http://127.0.0.1:8178/inference
```

Stop with Ctrl-C. The launcher forwards termination signals to the server and
releases port 8178. Press Ctrl-C again to force a server that does not stop.
The committed fixture is synthetic speech. No live Telegram or OpenAI integration
is claimed. Claude extraction was checked with the synthetic replay above.

## Backend image

The smoke image contains the installed Python package and runtime dependencies,
runs as a non-root user, and starts the health API by default. It includes Alembic
configuration and migration snapshots. It contains no model runtime, ffmpeg or
`.env` file. The migration smoke check requires no database connection.

```sh
docker build -f docker/backend.Dockerfile -t taskbot-backend:s2 .
docker run --rm taskbot-backend:s2 alembic heads
docker run --rm -p 127.0.0.1:18000:8000 taskbot-backend:s2
```

The full application Compose stack is delivered after the remaining modules.

## Task API

Run `yarn dev:api` after the shared stores are available and the development
database is migrated. `yarn dev:seed` prints a bearer token for local requests.
Every new token invalidates older links for that user. The dashboard reads
the token from its URL fragment; API calls send `Authorization: Bearer <token>`.
Missing or invalid credentials return `401` with instructions to request a new link.

| Method and path | Result |
| --- | --- |
| `GET /health` | Public health response |
| `GET /api/tasks?limit=50&status=pending` | Task items and an opaque `next_cursor`; optional status; maximum limit 200 |
| `PATCH /api/tasks/{task_id}` | Set `status` to `pending`, `in_progress` or `completed`; return the task |
| `DELETE /api/tasks/{task_id}` | Permanently delete the task; return `204` without a body |
| `POST /api/realtime/tickets` | Single-use ticket valid for 30 seconds |
| `GET /api/events?ticket=…` | SSE task events and revocation notifications |

Each task includes `source_text`, the stored message text for its Source message block. Follow
`next_cursor` to load another page. A foreign or missing task returns `404`.
Status publication follows the database commit; a Redis failure does not undo a
saved change. Refresh the task list after every stream reconnect.

Deletion also erases the stored source message text when its last task is gone.
The message record remains to prevent a duplicate delivery from recreating the
task. Other tabs receive `task.deleted` with the task id. Deletion has no undo and
does not remove the original message from the Telegram chat. Apply database
migrations through revision `0003` before using this operation.

The stream sends `: connected` after confirming its Redis subscription, then JSON
`data:` frames without an `event:` field. Parse their `type` discriminator with
the generated schema. Keep-alive comments arrive during idle periods. On errors,
close the stream, request a new ticket and reconnect with back-off; `401` on the
ticket request ends the loop. A token rotation forwards `auth.revoked` and closes
the stream. The database recheck catches missed publications, using elapsed time
every 20 seconds and a five-second check timeout.

There is no cap on simultaneous streams; each holds one Redis connection. Very
long voice transcripts increase task pages and event sizes because source text
is not truncated. The access log includes single-use ticket query strings;
these tickets expire after 30 seconds and cannot be reused after consumption.

The API routes and streaming behavior are verified with local PostgreSQL and
Redis; these checks need no Telegram or transcription credentials. The frontend
contract is generated with `yarn api:schema`; `yarn api:check` detects drift.

Runtime `/docs`, `/redoc` and `/openapi.json` routes are disabled. Use the
committed OpenAPI file or `yarn api:schema`. Expected API errors have a JSON
`detail` string, with generated types for `401`, `404` and `503`. Malformed JSON
returns `422` before bearer validation; otherwise protected requests require
a valid token.
