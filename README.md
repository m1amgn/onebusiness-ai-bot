# AI Task Bot

A task-management application. Users create tasks by sending text or voice messages to a Telegram bot. A background worker transcribes voice messages asynchronously through Redis queues. PostgreSQL stores tasks and users. A responsive web dashboard shows the tasks in real time and lets users move each task between Pending, In Progress and Completed. Docker Compose runs the web application, PostgreSQL, Redis and the worker.

The domain core and PostgreSQL persistence are available, with migrations and a
command to create development tasks. The API serves health only, and the frontend
is a placeholder with a generated API contract. The worker runtime is available. Bot handlers and task dashboard
features are added in later stages.

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
| `yarn dev:api` | API with reload on `http://127.0.0.1:8000`; public `GET /health` |
| `yarn dev:web` | Frontend on `http://127.0.0.1:5173`; `/api` proxy preserves the prefix |
| `yarn dev:whisper` | Local whisper.cpp server on `http://127.0.0.1:8178` |
| `yarn db:migrate` | Upgrade the database schema to the latest Alembic revision |
| `yarn --silent dev:seed <telegram_user_id> [text …]` | Create tasks, rotate the dashboard token and publish events |
| `yarn dev:worker transcribe` / `yarn dev:worker notify` | Start the prefork worker roles; notify also runs beat |
| `yarn dev:bot` | Bot; currently fails naming stage S4 |

Never hand-edit `backend/openapi.json` or `frontend/src/api/generated`.
Change the Pydantic models and run `yarn api:schema`. TypeScript types and runtime
Zod schemas have the same source.

## Development tasks

Apply migrations before creating tasks. Both commands read `DATABASE_URL` from
the environment or the root `.env`; it must use `postgresql+asyncpg://`.

```sh
yarn db:migrate
yarn --silent dev:seed 1001 "Call home and ask about the weekend."
```

Without text, the seed uses a built-in English sample. The current fallback keeps
one task per message, cuts its title to 120 characters and keeps longer text in
the description. Only the later model extractor splits a message into tasks.
The seed stores tasks and prints the token and `<WEB_BASE_URL>/#token=…` link.
Every run rotates the token, including a duplicate message, so older links stop
working. After both database commits, it publishes one `task.created` per task,
then `auth.revoked`. The printed `Events channel:` uses the stored user's internal
id, which differs from the Telegram id on the command line. A test namespace also
prefixes this channel. The dashboard and task API arrive later; this command
verifies persistence and Redis publication without Telegram.

To observe development events, subscribe in another terminal before seeding:

```sh
redis-cli -u redis://localhost:6379/0 PSUBSCRIBE 'events:user:*'
```

Use the configured `REDIS_URL` if it differs from this default. With Redis
unreachable, the seed waits up to about one second, logs one failed publication
batch and still prints the committed token and link. It skips the remaining
events in that batch. The CLI closes its Redis client and SQL engine on exit.
The seed publisher and root command were verified against real local stores with
synthetic tasks, an owned test database and a private Redis namespace.

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
The committed fixture is synthetic speech. No live Telegram,
OpenAI or Anthropic integration is claimed by this foundation stage.

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
