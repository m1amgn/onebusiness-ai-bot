# AI Task Bot

A task-management application. Users create tasks by sending text or voice messages to a Telegram bot. A background worker transcribes voice messages asynchronously through Redis queues. PostgreSQL stores tasks and users. A responsive web dashboard shows the tasks in real time and lets users move each task between Pending, In Progress and Completed. Docker Compose runs the web application, PostgreSQL, Redis and the worker.

The domain core and PostgreSQL persistence are available, with migrations and a
command to create development tasks. The API serves health only, and the frontend
is a placeholder with a generated API contract. Bot, worker and task dashboard
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
| `yarn --silent dev:seed <telegram_user_id> [text …]` | Create tasks and print a new dashboard token and link |
| `yarn dev:worker transcribe` / `yarn dev:worker notify` | Worker roles; currently fail naming stage S3 |
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
Every run rotates the token, so older links stop working. The dashboard and task
API arrive later; this command currently verifies persistence without Telegram.
Events are logged as unpublished until the Redis publisher is connected.

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
database or assume exclusive use of the shared stack. Later Redis tests use
private namespaces too.

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
