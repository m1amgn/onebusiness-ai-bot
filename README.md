# AI Task Bot

A task-management application. Users create tasks by sending text or voice messages to a Telegram bot. A background worker transcribes voice messages asynchronously through Redis queues. PostgreSQL stores tasks and users. A responsive web dashboard shows the tasks in real time and lets users move each task between Pending, In Progress and Completed. Docker Compose runs the web application, PostgreSQL, Redis and the worker.

The foundation is available: a health-only API, placeholder frontend, generated
API contract, local infrastructure and verification tools. Bot, worker and task
dashboard features are added in later stages.

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
| `yarn db:migrate` / `yarn dev:seed` | Available when persistence is built; currently fail naming stage S2 |
| `yarn dev:worker transcribe` / `yarn dev:worker notify` | Worker roles; currently fail naming stage S3 |
| `yarn dev:bot` | Bot; currently fails naming stage S4 |

Never hand-edit `backend/openapi.json` or `frontend/src/api/generated`.
Change the Pydantic models and run `yarn api:schema`. TypeScript types and runtime
Zod schemas have the same source.

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
The isolated project has its own containers and volumes. Integration tests in
later stages create private databases and Redis namespaces; they never write to
the development database or assume exclusive use of the shared stack.

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
releases port 8178. The committed fixture is synthetic speech. No live Telegram,
OpenAI or Anthropic integration is claimed by this foundation stage.

## Backend image

The smoke image contains the installed Python package and runtime dependencies,
runs as a non-root user, and starts the health API by default. It contains no
model runtime, ffmpeg or `.env` file.

```sh
docker build -f docker/backend.Dockerfile -t taskbot-backend:s1 .
docker run --rm -p 127.0.0.1:18000:8000 taskbot-backend:s1
```

The full application Compose stack is delivered after the remaining modules.
