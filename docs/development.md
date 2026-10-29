# Development

Use this guide to run and change the application from a source checkout.
The [README](../README.md) covers the packaged Docker application and its
verification record. The [deployment guide](deployment.md) covers server setup,
backups and image updates.

## Prepare a checkout

You need Git, Node 26, Yarn 1.22.22, uv 0.13 or newer, Docker and Compose v2.20
or newer. `nvm install` reads `.nvmrc`. Node 26 does not bundle Corepack; install
Yarn 1.22.22 separately. Use Yarn for
project dependencies and uv for Python dependencies. uv installs Python 3.14
if it is missing.

Reserve ports 5432 and 6379 for shared development infrastructure, 8000 for the
API, 5173 for Vite, and 8178 if you run local Whisper. Use an isolated
infrastructure project if the database ports are occupied.

```text
clone -> install -> start infrastructure -> verify -> migrate -> run dev servers
```

From the repository root:

```sh
cp .env.example .env
yarn install --frozen-lockfile
uv sync --locked --directory backend
yarn infra:up
yarn verify
yarn db:migrate
```

`yarn verify` requires PostgreSQL and Redis. If either service is stopped, the
checks fail with its address and `run yarn infra:up`. They do not skip integration
tests. No Telegram token or provider key is required for these checks. Migrate
the development database before running the API, bot, workers or seed command;
test migrations affect test databases only.

Keep real credentials in the untracked root `.env`. Use a separate test bot for
live development. Do not put tokens, keys or real chat content in committed files.

## Development settings

Python settings read the root `.env`; process environment variables take
precedence. `TASKBOT_ENV_FILE` selects another dotenv file. Tests use
`TASKBOT_ENV_FILE=/dev/null` to disable the root dotenv file. Python settings
ignore empty values; a required field still needs a value from another source.
Each process validates only the settings it reads.

The table follows the copied `.env.example`, which selects OpenAI voice and Claude Haiku task extraction.
Set `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in the untracked `.env` for full functionality.
The example supplies the local `DATABASE_URL`; application settings require that URL.
Without a value for `TASK_EXTRACTOR`, the code and Compose fallback is `heuristic`.

| Variable | Default or requirement | Use |
|---|---|---|
| `DATABASE_URL` | Required; example `postgresql+asyncpg://taskbot:taskbot@localhost:5432/taskbot` | API, bot, workers, seed and migrations; must use `postgresql+asyncpg://` |
| `REDIS_URL` | `redis://localhost:6379/0` | Application events, tickets, locks and worker configuration |
| `CELERY_BROKER_URL` | Unset | Derive from `REDIS_URL`, using database 1; an explicit value overrides it |
| `REDIS_NAMESPACE` | Empty | Test sessions only; leave empty for normal development |
| `TELEGRAM_BOT_TOKEN` | Required for the bot; optional for workers | Real voice processing needs the same test token in the bot and workers |
| `WORKER_ROLE` | Required process setting or launcher argument | `transcribe` or `notify`; a value in `.env` alone does not select a role |
| `TRANSCRIPTION_PROVIDER` | `openai` | `openai`, `whisper_server` or `fake`; no automatic provider fallback |
| `OPENAI_API_KEY` | Unset | Required for real OpenAI transcription; missing keys leave text usable |
| `OPENAI_TRANSCRIPTION_MODEL` | `gpt-transcribe` | OpenAI transcription model |
| `WHISPER_SERVER_URL` | `http://127.0.0.1:8178` | Local Whisper HTTP server |
| `TASK_EXTRACTOR` | `claude` in the copied example | `heuristic` or `claude`; missing keys use the degraded heuristic |
| `ANTHROPIC_API_KEY` | Unset | Claude extraction; requested Claude without a key uses the degraded heuristic |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Claude extraction model |
| `WEB_BASE_URL` | `http://localhost:5173` | Dashboard links from the development bot and seed; strips a trailing slash |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |

For local Whisper development, set these values in `.env`:

```dotenv
TRANSCRIPTION_PROVIDER=whisper_server
WHISPER_SERVER_URL=http://127.0.0.1:8178
WHISPER_DEV_MODEL_PATH=/absolute/path/to/ggml-large-v3-turbo-q5_0.bin
WEB_BASE_URL=http://localhost:5173
```

`WHISPER_DEV_MODEL_PATH` has no default and must name an existing model file.
The Node launcher expands a leading `~/`; shell variables take precedence over
`.env`. It checks that `whisper-server` and `ffmpeg` are on `PATH`.

Compose and the development infrastructure launcher also read these variables:

| Variable | Default | Scope |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `taskbot` each | Packaged Compose and isolated infrastructure; blank values use the default |
| `WEB_PORT` | `8080` | Packaged web host port; does not change Vite or the development API port |
| `WHISPER_MODEL` | `base` | Optional packaged Whisper image; the delivered downloader supports `base` |
| `WHISPER_MEM_LIMIT` | `1g` | Packaged Whisper container memory limit |
| `COMPOSE_PROFILES` | Empty | Packaged optional services; `whisper` enables the Whisper container |
| `TASKBOT_STACK` | `taskbot` | Development infrastructure project selected by the guard |
| `TASKBOT_PG_PORT` | `5432` | Development PostgreSQL host port |
| `TASKBOT_REDIS_PORT` | `6379` | Development Redis host port |

The packaged project is `taskbot-app`. It publishes web on
`127.0.0.1:8080` by default and keeps PostgreSQL, Redis and the API internal.
Compose sets container URLs itself: database host `postgres`, Redis host `redis`,
Whisper URL `http://whisper:8080`, and dashboard URL
`http://localhost:${WEB_PORT:-8080}`. It does not pass the local `.env` URLs
through to containers. Keep `WEB_BASE_URL=http://localhost:5173` for development.
There is no `API_PORT` setting; the local commands use ports 8000 and 5173.

## Shared infrastructure

The default `taskbot` stack belongs to the machine. All worktrees share it and
its development data. Leave it running when a stage finishes. `yarn infra:stop`
stops containers without removing data; coordinate a stop with other active work.
Do not run Compose `down` against `taskbot`, or delete its volumes.

Use `yarn infra:up` and `yarn infra:stop` for development infrastructure. The
guard fixes the project, input files and project directory, so native Compose
overrides cannot redirect these commands. It adds `docker/dev-infra.ports.yml`
to publish database ports on `127.0.0.1`. For the shared `taskbot` project it
passes `scripts/empty.env` and removes `POSTGRES_USER`, `POSTGRES_PASSWORD` and
`POSTGRES_DB` from the child environment. Those three settings in `.env` or the
shell therefore cannot change the shared credentials: user, password and
database remain `taskbot`. A plain `docker compose` command uses the separate
packaged `taskbot-app` project.

The first guarded start after the S8 infrastructure changes recreates the shared
containers once and keeps their volumes. Coordinate that start with other users
of the shared stack. Do not use another checkout with the older Compose
definition to recreate the shared containers during this transition.

If those ports are occupied, uncomment `TASKBOT_STACK`, `TASKBOT_PG_PORT` and
`TASKBOT_REDIS_PORT` in `.env`. Choose a `taskbot-<name>` project and two distinct
ports other than 5432/6379. Update `DATABASE_URL` and `REDIS_URL` to match them.
The isolated project has its own containers and volumes. If you change its
`POSTGRES_*` credentials, make `DATABASE_URL` match them.

R-ISO is the test isolation rule: integration tests create and migrate a private
`taskbot_test_<unix-time>_<8 hex>` database for each session, then drop it.
Cleanup protects live test sessions even if they are old. Tests never write to the development
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

Separate databases and Redis namespaces isolate data, not server capacity.
PostgreSQL has 100 connection slots. The end-to-end integration test can add
25 application connections across its bot, API and worker children. Avoid
concurrent full verification runs on the shared server.

## Command reference

| Command | Purpose |
|---|---|
| `yarn verify` | File size, script tests, Python lint/format/types/layers/tests, schema drift, frontend checks |
| `yarn test:unit` | Python unit tests and frontend tests without infrastructure |
| `yarn verify:backend` / `yarn verify:frontend` | Check one module |
| `yarn check:scripts` / `yarn check:size` | Check the Node tools or source-file length |
| `yarn api:schema` | Export OpenAPI and regenerate TypeScript and Zod together |
| `yarn api:check` | Fail if the schema or generated files are stale |
| `yarn infra:up` / `yarn infra:stop` | Start or stop guarded development PostgreSQL and Redis |
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

## Seed tasks and dashboard links

Apply migrations before creating tasks. Both commands read `DATABASE_URL` from
the environment or the root `.env`; it must use `postgresql+asyncpg://`.

```sh
yarn db:migrate
yarn --silent dev:seed 1001 "Call home and ask about the weekend."
```

Without text, the seed uses a built-in English sample. The heuristic extractor keeps
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

There is no cap on simultaneous streams; each holds one Redis connection.
Stored voice transcripts are limited to 4096 characters. Source text appears in
both task pages and events, so it contributes to their size. The development
API access log includes single-use ticket query strings; these tickets expire
after 30 seconds and cannot be reused after consumption. The packaged nginx
access log records the URI without its query string.

The API routes and streaming behavior are verified with local PostgreSQL and
Redis; these checks need no Telegram or transcription credentials. The frontend
contract is generated with `yarn api:schema`; `yarn api:check` detects drift.

Runtime `/docs`, `/redoc` and `/openapi.json` routes are disabled. Use the
committed OpenAPI file or `yarn api:schema`. Expected API errors have a JSON
`detail` string, with generated types for `401`, `404` and `503`. Malformed JSON
returns `422` before bearer validation; otherwise protected requests require
a valid token.

## Telegram bot

Apply database migrations, put a separate test bot token in the untracked `.env`,
then run `yarn dev:bot`. The bot validates settings before starting. It checks
PostgreSQL and acquires a Redis polling lock before contacting Telegram. A second
instance exits with the remaining lock lifetime. The bot removes an existing
webhook without discarding pending updates.

Stop any other bot that polls the same token, including a bot in `taskbot-app`.
Polling locks in separate Redis instances or namespaces cannot protect each
other. Telegram returns polling conflicts when both bots run.

Private text creates one task and replies with its title. Task titles retain the
message's language; bot replies are English. `/start` shows the selected providers.
`/tasks` lists up to 15 current tasks, with In progress first and Pending second.
Each group shows the newest messages first. Completed tasks stay on the dashboard.
When more current tasks exist, the reply points to `/dashboard`. Listing tasks
does not issue a new link or change a task. Changes and deletion stay on the dashboard.
`/dashboard` issues a personal link and invalidates older links. Keep the link
private. Two concurrent requests can deliver the older link last; request another
link if needed. A failed reply after rotation also needs a new `/dashboard`.

A voice message receives an acknowledgement, then enters the transcription queue.
The limits are 120 seconds and 10,000,000 bytes; exact limits pass. Missing size
metadata is checked during download. The bot checks Telegram's declared duration;
the worker sends downloaded bytes to the provider without inspecting their format
or measuring duration. Failed enqueue attempts leave a
recoverable database row. A successful job edits the acknowledgement to the task
title. A missing acknowledgement gets a new reply to the voice message.

```text
text  -> extract -> commit task -> reply -> publish event
voice -> commit job -> reply -> store reply id -> enqueue -> stamp
worker -> download -> transcribe -> extract -> commit -> notify
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
by `/start`; voice messages are still acknowledged. Requested Claude extraction
without an Anthropic key uses the degraded heuristic and adds a fallback notice
to text replies. With OpenAI selected and no key, voice jobs end with an
unavailable notice. Fake transcription creates a deterministic demo task and
marks its notification as demo mode. Stored
transcripts are limited to 4096 characters, cut at a word when possible.

The bot renews its polling lock during startup and while draining. It publishes
`heartbeat:bot` only after successful polling, under `REDIS_NAMESPACE` when set.
Loss of ownership or sustained failed polling requests stops the process. Time
spent waiting for handler capacity counts as healthy polling. If all ten handlers
wait indefinitely, for example on database locks, the bot keeps its heartbeat
but consumes no further updates. This version has no timeout for a complete update;
its heartbeat does not detect permanently stuck handlers. The packaged stack
gives the bot 40 seconds to stop. Its Docker verification record belongs to the
[README](../README.md); check producer drain under a new
deployment's broker conditions.

Telegram: verified live on 2026-10-27 with a separate test bot against the local
stack. Private `/start`, two texts, two voice messages, `/dashboard` and a sticker
were each handled in 319-883 ms, from receiving the update to sending the reply; the
voice messages stopped at the acknowledgement, because transcription is a later stage.
SIGINT stopped the bot in about 3 s and released its polling lock. Offline handler,
adapter and lifecycle checks use a fake Telegram transport; integration checks use
real isolated PostgreSQL and Redis.
The `/tasks` command has offline and isolated-store coverage and was checked in the owner's Docker run on 2026-10-29.
The private `/start`, `/tasks`, `/dashboard` menu has offline and isolated-store coverage;
it has not been separately checked live on Telegram.
The bot registers that menu once at startup, with a ten-second request timeout.
A registration failure logs a warning and polling continues.

The dated Telegram check above used local development processes.
The owner's packaged-Docker check of 2026-10-29 has a separate record in the [README](../README.md).

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
required to start either role. Processing real voice messages requires the same
Telegram token as the bot. Transcribe owns one download, extraction and
transcription client per child. Notify builds no extraction or transcription client.
Use isolated test namespaces when testing on the shared broker.
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
`s3test.*` tasks for lifecycle checks. The voice-flow tests also use owned PostgreSQL
databases, fake transcription and recording Telegram adapters. No ordinary test
contacts a paid provider. Notify recovers stale jobs at startup and every 60 seconds.
Transcription retries transient failures at most five times, with 2/4/8/16-second
delays. OpenAI's 30-second timeout is transient; whisper-server's 300-second timeout
is final. The full attempt has a 400-second budget and Celery limits of 420/450 seconds.
Notification retries are capped at five attempts. Retry-after responses preserve
their requested delay. Terminal failure texts stay in Telegram.
The packaged workers use Linux prefork. See the [README](../README.md) for
the packaged-stack verification record and [deployment](deployment.md) for
container restart and shutdown settings.

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
is claimed by this transcription check. The earlier Telegram entry-point check is
recorded in the bot section. Claude's synthetic replay is in the extraction section.

Use the opt-in probe for one recording. It prints provider, transcript and elapsed
transcription time. Each OpenAI invocation makes one
paid call. The provider model and URL use the same settings as the worker:

```sh
yarn --silent live:transcription --provider whisper_server
yarn --silent live:transcription --provider openai
yarn --silent live:transcription --provider whisper_server --file /path/to/synthetic.ogg
uv run --locked --directory backend pytest -m live_whisper tests/live
```

The last command exercises real PostgreSQL, Redis, Celery and whisper-server with
the synthetic fixture and a recording Telegram adapter. Start `yarn dev:whisper`
first. Normal `yarn verify` deselects these tests unless the marker is named
explicitly. Its offline probe tests forbid real provider calls.

S5 live verification on 2026-10-27: the real-worker Whisper flow passed with a
non-empty transcript, a stored task and a recorded notification edit. On Apple M3,
whisper-server large-v3-turbo-q5_0 with Metal transcribed synthetic Russian
recordings of 30.02 and 120.02 seconds in 4.036 and 10.983 seconds. These timings describe this
model and hardware with repeated synthetic speech; they do not measure accuracy.
The separate OpenAI check returned non-empty text from `gpt-transcribe` for the
3.82-second synthetic fixture in 2.526 seconds. The first key returned
`provider_unavailable`; the owner replaced it and authorized one successful repeat.
The first test-bot voice was rejected before Whisper and created no task. After
the owner chose to trust Telegram's declared duration (S5-D9), the packet parser
and worker-side duration refusal were removed. A new voice recorded in the
Telegram app then passed through the real test bot and both development workers:
Whisper returned text, one task was stored, and the acknowledgement was edited
to the result on the first transcription and notification attempts. The stand
used heuristic extraction with a private test database and Redis namespace;
both were removed after verification.

An additional owner-authorized run used `TRANSCRIPTION_PROVIDER=openai` and
`gpt-transcribe` with a new voice recorded in the Telegram app. The real worker
returned non-empty text, stored one task and edited the acknowledgement on the
first transcription and notification attempts. This run made one OpenAI call.
It used heuristic extraction and a private database/Redis namespace, then removed
the stand and its owned resources.

## Claude extraction

The copied example selects `TASK_EXTRACTOR=claude`. Set `ANTHROPIC_API_KEY` in the untracked root `.env`
to use Claude Haiku. For explicit keyless extraction, set `TASK_EXTRACTOR=heuristic`.
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

## Checks and acceptance replays

`yarn verify` checks source size, Node scripts, Python lockfile/lint/format/types,
import layers, Python unit and integration tests, API schema drift, and frontend
lint/format/types/tests. `yarn verify:backend` and `yarn verify:frontend` check one
module. `yarn test:unit` needs no infrastructure. Use the development servers to
check behavior. `yarn workspace frontend build` checks TypeScript and writes
`frontend/dist`; it does not replace a browser check.

The end-to-end integration test runs a bot session, real Celery workers,
PostgreSQL, Redis and the HTTP/SSE API. It checks text and voice task creation,
notification, task events and authenticated reads. Telegram and provider adapters
use test doubles. Ordinary verification makes no paid provider calls. Worker
subprocess fixtures disable dotenv loading and remove inherited provider keys.
`live_whisper` tests run only when the marker is named explicitly.

The following replays use synthetic messages and in-memory service doubles or
read-only configuration checks. They need no running stores or credentials:

```sh
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s2.py
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s3.py
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s4.py
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s5.py
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s6.py
TASKBOT_ENV_FILE=/dev/null uv run --locked --directory backend python tests/acceptance/replay_s10.py
```

| Replay | Coverage |
|---|---|
| `replay_s2.py` | Domain and task use cases |
| `replay_s3.py` | Optional worker tokens, refused roles and internal-user event channels; starts no worker |
| `replay_s4.py` | Telegram commands, text, voice acceptance and private-chat rules |
| `replay_s5.py` | Voice processing and terminal outcomes; forbids provider factories and sockets |
| `replay_s6.py` | Authenticated task API scenarios against synthetic messages |
| `replay_s10.py` | `/tasks` and menu reply scenarios; startup menu registration has isolated-store coverage |

The dashboard replay and its generated fixture command are in the dashboard
section. Paid extraction and transcription probes are opt-in commands in their
provider sections; they are outside `yarn verify`.

## Load-sensitive test quarantine

This list explains failures under machine load; it does not skip tests. Every
listed test still runs in the final `yarn verify`. The owner makes each listed
test robust. After an unrelated load-sensitive failure, rerun that test alone at
the same commit under low load and on the base commit. Record the command, both
commits and both results in the verification receipt. A repeated failure on head,
or a failure only on head, is a defect to investigate.

| Test | Observed failure | Owner |
|---|---|---|
| `backend/tests/integration/test_bot_lock.py` | Sub-second lock leases can expire while the machine is busy. | Repository owner |
| `backend/tests/integration/test_celery_job_queue.py::test_frozen_broker_bounds_thread_batches_and_keeps_dns_available` | The 200 ms event-loop gap assertion reached 229 ms under load; isolated head/base repeats passed. | Repository owner |

The whisper launcher fixtures wait for marker files for up to 30 seconds.
Worker runtime effect waits use the 120-second cold-start budget. The ETA restart
test observes the worker's scheduled list before stopping it. The dev proxy test
allows 10 seconds for the interrupted stream to close, and still fails when the
proxy keeps it open.
